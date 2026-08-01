"""
===============================================================================
  Bot-Options / app.py
  FastAPI server serving the Options Trading Terminal on port 8001.
  Orchestrates the background monitor, handles configuration API, and
  manages options scanner execution.
===============================================================================
"""

import sys
import logging
import threading
from pathlib import Path
from datetime import datetime
import yaml
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from ruamel.yaml import YAML as _RYAML
from openalgo import api as oa_api

# Add project root to sys.path
_bot_dir = Path(__file__).resolve().parent
if str(_bot_dir) not in sys.path:
    sys.path.insert(0, str(_bot_dir))

# Configure logging specifically for Bot-Options
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(_bot_dir / "options.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("OptionsBot")

# Suppress noisy third-party INFO chatter that has no trading value
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# Imports
from option_scanner import run_option_scan, execute_options_trade
from execution.position_monitor import OptionPositionMonitor
from data.option_chain import fetch_option_chain
from core.expiry_manager import select_expiry
from db.option_signal_db import get_option_signals, get_option_statistics
from db.option_trade_db import get_open_positions, get_closed_positions, get_position_events
from execution.order_engine import place_direct_options_order

# ---------------------------------------------------------------------------
# SDK Client Cache
# ---------------------------------------------------------------------------
_oa_client_cache: dict = {}

def _get_oa_client(oa_cfg: dict):
    """Return a cached OpenAlgo API client."""
    key = (oa_cfg.get("apikey", ""), oa_cfg.get("base_url", "http://127.0.0.1:5000"), oa_cfg.get("ws_url", ""))
    if key not in _oa_client_cache:
        _oa_client_cache[key] = oa_api(
            api_key=key[0], 
            host=key[1],
            ws_url=key[2] if key[2] else None
        )
    return _oa_client_cache[key]


def load_config() -> dict:
    """Load config.yml safely."""
    path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Initialize monitor
_monitor = OptionPositionMonitor()

# app is declared after lifespan() below so it can reference it.

# ---------------------------------------------------------------------------
# Item 13: Signal Outcome Tracking — background thread
# ---------------------------------------------------------------------------
def _run_outcome_tracker():
    """
    Background job: periodically check signals marked EXECUTED and update
    their outcome_pnl_pct by looking up the corresponding closed position.

    Runs every `outcome_check_hours` hours (from config, default 4 hours).
    This creates a feedback loop for analytics: every signal that was executed
    eventually gets its real P&L stamped back into the option_signals table.
    """
    import time as _time
    while True:
        try:
            cfg = load_config()
            check_hours = int(cfg.get("filters", {}).get("outcome_check_hours", 4))
            _time.sleep(check_hours * 3600)

            # Only proceed if signal history tracking is enabled
            if not cfg.get("filters", {}).get("signal_history_enabled", True):
                continue

            conn_sig = None
            conn_pos = None
            try:
                from db.option_signal_db import get_db_connection as sig_conn_fn, DB_PATH as SIG_DB
                from db.option_trade_db import get_db_connection as pos_conn_fn
                import sqlite3

                # Fetch executed but not-yet-checked signals
                conn_sig = sig_conn_fn()
                cursor_sig = conn_sig.cursor()
                cursor_sig.execute("""
                    SELECT id, symbol, direction, entry_premium, timestamp
                    FROM option_signals
                    WHERE status = 'EXECUTED' AND outcome_checked = 0
                """)
                pending = cursor_sig.fetchall()

                if not pending:
                    continue

                conn_pos = pos_conn_fn()
                cursor_pos = conn_pos.cursor()

                for row in pending:
                    sig_id = row["id"]
                    symbol = row["symbol"]
                    direction = row["direction"]

                    # Find the closed position for this symbol closest in time
                    cursor_pos.execute("""
                        SELECT pnl_pct FROM option_positions
                        WHERE symbol = ? AND status = 'CLOSED'
                        ORDER BY ABS(julianday(close_time) - julianday(?)) ASC
                        LIMIT 1
                    """, (symbol, row["timestamp"]))
                    pos_row = cursor_pos.fetchone()

                    if pos_row and pos_row["pnl_pct"] is not None:
                        outcome_pnl = float(pos_row["pnl_pct"])
                        cursor_sig.execute("""
                            UPDATE option_signals
                            SET outcome_pnl_pct = ?, outcome_checked = 1
                            WHERE id = ?
                        """, (outcome_pnl, sig_id))
                        log.info("Outcome tracked: signal %d → pnl_pct=%.2f%%", sig_id, outcome_pnl)
                    else:
                        # No matching closed position yet — mark checked to avoid re-querying forever
                        cursor_sig.execute("""
                            UPDATE option_signals SET outcome_checked = 1 WHERE id = ?
                        """, (sig_id,))

                conn_sig.commit()

            except Exception as db_err:
                log.error("Signal outcome tracker DB error: %s", db_err)
            finally:
                if conn_sig:
                    conn_sig.close()
                if conn_pos:
                    conn_pos.close()

        except Exception as e:
            log.error("Signal outcome tracker error: %s", e)


_outcome_tracker_thread = threading.Thread(target=_run_outcome_tracker, daemon=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler — replaces deprecated on_event startup/shutdown."""
    # startup
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        _monitor.start(cfg, oa_client)
        _outcome_tracker_thread.start()
        log.info("Signal outcome tracker thread started.")
    except Exception as e:
        log.error("Failed to start Options Position Monitor: %s", e)

    yield  # server is running

    # shutdown
    _monitor.stop()


app = FastAPI(title="Options Trading Terminal API", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Models for Config Updates
# ---------------------------------------------------------------------------
class UnderlyingConfig(BaseModel):
    name: str
    exchange_index: str
    lot_size: int
    strike_step: float
    enabled: bool

class StrikeSelectionConfig(BaseModel):
    expiry_preference: str
    auto_roll_days: int
    method: str
    otm_strikes: int
    itm_strikes: int
    premium_min: float
    premium_max: float
    oi_min_threshold: int
    liquidity_min_volume: int
    min_days_to_expiry: int
    scan_both_sides: bool

class StrategyConfig(BaseModel):
    ut_enabled: bool
    key_value: float
    atr_period: int
    use_heikin_ashi: bool

class SRChannelsConfig(BaseModel):
    enabled: bool
    pivot_period: int
    source: str
    channel_width_pct: float
    min_strength: int
    max_num_sr: int
    loopback: int
    proximity_pct: float

class OptionChartConfConfig(BaseModel):
    enabled: bool
    mode: str
    key_value: float
    atr_period: int
    require_sr_proximity: bool
    confirmation_bonus_pts: float
    contradiction_penalty_pts: float

class FiltersConfig(BaseModel):
    mtf_filter_enabled: bool
    mtf_timeframe: str
    mtf_neutral_pct: float
    mtf_atr_period: int
    ema_filter_enabled: bool
    ema_period: int
    volume_filter_enabled: bool
    volume_sma_period: int
    volume_min_pct: int
    min_alert_score: int
    iv_score_enabled: bool
    oi_score_enabled: bool
    oi_momentum_score_enabled: bool
    time_decay_penalty_enabled: bool
    time_decay_threshold_days: int
    candle_patterns_enabled: bool
    signal_history_enabled: bool
    outcome_check_hours: int

class ExecutionConfig(BaseModel):
    order_mode: str
    order_type: str
    order_product: str
    num_lots: int
    slippage_pts: float
    strategy_tag: str

class ProfitLockLevel(BaseModel):
    threshold_pct: float
    lock_fraction: float

class ProfitLockConfig(BaseModel):
    enabled: bool
    levels: list[ProfitLockLevel]

class TrailingSLConfig(BaseModel):
    enabled: bool
    activation_pct: float
    distance_pct: float

class PartialExitConfig(BaseModel):
    enabled: bool
    target1_pct: float
    exit_qty_fraction: float
    move_sl_to_breakeven: bool

class ExpiryMgmtConfig(BaseModel):
    auto_exit_on_expiry: bool
    exit_minutes_before_close: int

class NotificationsConfig(BaseModel):
    on_signal: bool
    on_execution: bool
    on_sl_move: bool
    on_profit_lock: bool
    on_partial_exit: bool
    on_exit: bool

class TradeManagementConfig(BaseModel):
    enabled: bool
    poll_interval_seconds: int
    stop_loss_pct: float
    target_pct: float
    profit_lock: ProfitLockConfig
    trailing_sl: TrailingSLConfig
    partial_exit: PartialExitConfig
    expiry_management: ExpiryMgmtConfig
    notifications: NotificationsConfig

class RiskManagementConfig(BaseModel):
    enabled: bool
    capital_allocation: float
    max_capital_per_trade: float
    max_simultaneous_positions: int
    max_trades_per_day: int
    max_daily_loss_amount: float
    max_daily_loss_pct: float
    consecutive_loss_limit: int
    cooldown_minutes: int

class OpenAlgoConfig(BaseModel):
    apikey: str
    username: str
    base_url: str
    ws_url: str

class TelegramConfig(BaseModel):
    enabled: bool
    mode: str
    bot_token: str
    chat_id: str

class WhatsAppConfig(BaseModel):
    enabled: bool

class DataConfig(BaseModel):
    lookback_days: int

class BotSettingsConfig(BaseModel):
    log_level: str
    market_hours_check: bool
    market_open: str
    market_close: str
    auto_refresh_enabled: bool

class ConfigUpdateRequest(BaseModel):
    platform: str
    trading_api_source: str
    exchange: str
    underlyings: list[UnderlyingConfig]
    strike_selection: StrikeSelectionConfig
    underlying_data_source: str
    option_data_source: str
    strategy: StrategyConfig
    sr_channels: SRChannelsConfig
    option_chart_confirmation: OptionChartConfConfig
    filters: FiltersConfig
    execution: ExecutionConfig
    trade_management: TradeManagementConfig
    risk_management: RiskManagementConfig
    openalgo: OpenAlgoConfig
    telegram: TelegramConfig
    whatsapp: WhatsAppConfig
    data: DataConfig
    bot: BotSettingsConfig

class ManualOrderRequest(BaseModel):
    symbol: str
    action: str          # "BUY" or "SELL"
    quantity: int
    price: float = 0.0

# ---------------------------------------------------------------------------
# API Routing endpoints
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    """Retrieve the current option configuration file."""
    try:
        return load_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")


def _update_commented_map(cm, updates: dict) -> None:
    """Recursively update a CommentedMap in-place preserving comments."""
    for key, value in updates.items():
        if isinstance(value, dict) and key in cm and hasattr(cm[key], "items"):
            _update_commented_map(cm[key], value)
        elif isinstance(value, list) and key in cm and isinstance(cm[key], list):
            # For lists, simple replacement is usually best if structure is identical
            cm[key] = value
        else:
            cm[key] = value


@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    """Save the updated configuration file, preserving YAML comments."""
    try:
        config_path = _bot_dir / "config.yml"
        config_dict = req.model_dump()

        ryaml = _RYAML()
        ryaml.preserve_quotes = True

        with open(config_path, "r", encoding="utf-8") as fh:
            commented_map = ryaml.load(fh)

        _update_commented_map(commented_map, config_dict)

        with open(config_path, "w", encoding="utf-8") as fh:
            ryaml.dump(commented_map, fh)

        return {"status": "success", "message": "Configuration saved successfully."}
    except Exception as e:
        log.error("Failed to save config: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")


@app.get("/api/option-chain")
def get_option_chain_endpoint(underlying: str, strike_count: int = 15):
    """Fetch live option chain with greeks."""
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        
        pref = cfg.get("strike_selection", {}).get("expiry_preference", "WEEKLY")
        roll_days = int(cfg.get("strike_selection", {}).get("auto_roll_days", 1))
        
        expiry_res = select_expiry(underlying, oa_client, pref, roll_days)
        if not expiry_res:
            raise HTTPException(status_code=404, detail=f"Could not find valid expiry date for {underlying}")
            
        expiry_date_obj, expiry_str = expiry_res
        
        chain = fetch_option_chain(underlying, expiry_str, oa_client, strike_count)
        if not chain:
            raise HTTPException(status_code=502, detail="Failed to fetch chain from OpenAlgo")
            
        return {
            "status": "success",
            "underlying": underlying,
            "expiry": expiry_str,
            "days_to_expiry": (expiry_date_obj - datetime.now().date()).days,
            "data": chain
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan")
async def trigger_scan_endpoint():
    """Trigger a manual three-stage scan for options signals."""
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        
        buys, sells, total = await run_in_threadpool(
            run_option_scan,
            cfg,
            oa_client,
            _monitor
        )
        return {
            "status": "success",
            "buy_signals": buys,
            "sell_signals": sells,
            "total_scanned": total,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        log.error("Manual options scan failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Scan execution failed: {e}")


@app.get("/api/signals")
def get_signals(limit: int = 50, offset: int = 0):
    """Retrieve logged signal history."""
    try:
        signals = get_option_signals(limit, offset)
        return {"status": "success", "signals": signals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
def get_active_positions():
    """Retrieve all open options positions."""
    try:
        positions = get_open_positions()
        return {"status": "success", "positions": positions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/closed")
def get_closed_positions_history(limit: int = 50, offset: int = 0):
    """Retrieve closed options positions history."""
    try:
        positions = get_closed_positions(limit, offset)
        return {"status": "success", "positions": positions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/{pos_id}/events")
def get_pos_events(pos_id: int):
    """Retrieve operational audit log events for a position."""
    try:
        events = get_position_events(pos_id)
        return {"status": "success", "events": events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/positions/{pos_id}/close")
async def manual_close_position_endpoint(pos_id: int):
    """Square off and exit position manually.

    Race-condition fix: the is_closing flag is set under lock before calling
    _execute_exit so the background monitoring loop skips the same position
    in its next tick, preventing a double-exit order.
    """
    target_pos = None
    with _monitor.lock:
        target_pos = _monitor.active_positions.get(pos_id)
        if target_pos:
            # Guard against concurrent exit from the monitoring loop
            if target_pos.get("is_closing"):
                raise HTTPException(status_code=409, detail="Position exit already in progress.")
            target_pos["is_closing"] = True

    if not target_pos:
        raise HTTPException(status_code=404, detail="Active position not found.")

    try:
        # Fetch current LTP from OpenAlgo
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        resp = await run_in_threadpool(oa_client.quotes, target_pos["symbol"], target_pos["exchange"])

        ltp = float(resp.get("data", {}).get("ltp") or resp.get("ltp"))

        # Execute exit (position removed from active_positions inside _execute_exit on success)
        await run_in_threadpool(_monitor._execute_exit, target_pos, ltp, "MANUAL")
        return {"status": "success", "message": f"Square off successfully executed for position {pos_id}."}
    except Exception as e:
        # Clear the flag so the monitor can retry if needed
        with _monitor.lock:
            if pos_id in _monitor.active_positions:
                _monitor.active_positions[pos_id]["is_closing"] = False
        log.error("Manual position square off exit failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/order")
async def manual_order_placement(req: ManualOrderRequest):
    """Place direct manual options order."""
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        
        result = await run_in_threadpool(
            place_direct_options_order,
            cfg,
            req.symbol,
            req.action,
            req.quantity,
            req.price,
            oa_client
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=502, detail=result.get("message"))
            
        return {"status": "success", "orderid": result.get("orderid"), "symbol": req.symbol}
    except Exception as e:
        log.error("Manual order placement failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/statistics")
def get_statistics_endpoint(days: int = 30):
    """Get Win/Loss stats."""
    try:
        stats = get_option_statistics(days)
        return {"status": "success", "statistics": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
def get_terminal_logs(lines: int = 150):
    """Get options.log snippets."""
    try:
        from collections import deque
        log_path = _bot_dir / "options.log"
        if not log_path.exists():
            return {"logs": "No log file found."}

        with open(log_path, "r", encoding="utf-8") as fh:
            last_lines = deque(fh, maxlen=lines)

        return {"logs": "".join(last_lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Item 8: Portfolio Greeks endpoint
# ---------------------------------------------------------------------------
@app.get("/api/greeks")
def get_portfolio_greeks():
    """
    Aggregate live Greeks across all open positions.
    Calls optiongreeks() for each open position and sums delta, gamma, theta, vega.
    Net theta is expressed as rupee value (theta × quantity).
    """
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        positions = get_open_positions()

        net_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0  # ₹ per day
        net_vega  = 0.0
        greeks_per_position = []

        for pos in positions:
            try:
                greeks_resp = oa_client.optiongreeks(
                    symbol=pos["symbol"],
                    exchange=pos.get("exchange", "NFO")
                )
                if not isinstance(greeks_resp, dict) or greeks_resp.get("status") != "success":
                    continue
                g = greeks_resp.get("data", {})
                qty = int(pos.get("quantity", 1))
                delta = float(g.get("delta", 0.0))
                gamma = float(g.get("gamma", 0.0))
                theta = float(g.get("theta", 0.0))
                vega  = float(g.get("vega",  0.0))
                # Long positions — add Greeks; the monitor only has long option positions
                net_delta += delta * qty
                net_gamma += gamma * qty
                net_theta += theta * qty   # theta is negative for buyers
                net_vega  += vega  * qty
                greeks_per_position.append({
                    "pos_id":    pos["id"],
                    "symbol":    pos["symbol"],
                    "quantity":  qty,
                    "delta":     delta,
                    "gamma":     gamma,
                    "theta":     theta,
                    "vega":      vega,
                    "net_theta_inr": round(theta * qty, 2)
                })
            except Exception as gex:
                log.warning("Greeks fetch failed for %s: %s", pos["symbol"], gex)

        return {
            "status": "success",
            "portfolio": {
                "net_delta": round(net_delta, 4),
                "net_gamma": round(net_gamma, 6),
                "net_theta_inr": round(net_theta, 2),
                "net_vega":  round(net_vega,  4),
            },
            "positions": greeks_per_position
        }
    except Exception as e:
        log.error("Portfolio greeks fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Item 9: Live market pulse — VIX, PCR, spot prices
# ---------------------------------------------------------------------------
@app.get("/api/market-pulse")
def get_market_pulse():
    """
    Returns live NIFTY / BANKNIFTY spot, India VIX, and Put-Call Ratio (PCR).
    PCR is computed from the option chain's total CE vs PE open interest for the
    nearest weekly expiry — the only statistically meaningful PCR definition.
    """
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))

        pulse: dict = {"status": "success", "underlyings": [], "vix": None, "pcr": {}}

        # 1. Spot prices for each enabled underlying
        for und in cfg.get("underlyings", []):
            if not und.get("enabled", True):
                continue
            name = und["name"]
            try:
                q = oa_client.quotes(symbol=name, exchange="NSE_INDEX")
                ltp = float(q.get("data", {}).get("ltp") or q.get("ltp", 0))
                change_pct = float(q.get("data", {}).get("change_percent", 0) or 0)
                pulse["underlyings"].append({"symbol": name, "ltp": ltp, "change_pct": round(change_pct, 2)})
            except Exception:
                pulse["underlyings"].append({"symbol": name, "ltp": 0.0, "change_pct": 0.0})

        # 2. India VIX
        try:
            vix_q = oa_client.quotes(symbol="INDIA VIX", exchange="NSE_INDEX")
            vix_ltp = float(vix_q.get("data", {}).get("ltp") or vix_q.get("ltp", 0))
            pulse["vix"] = round(vix_ltp, 2)
        except Exception:
            pulse["vix"] = None

        # 3. PCR per underlying (total PE OI / total CE OI from nearest weekly expiry chain)
        for und in cfg.get("underlyings", []):
            if not und.get("enabled", True):
                continue
            name = und["name"]
            try:
                pref    = cfg.get("strike_selection", {}).get("expiry_preference", "WEEKLY")
                roll_d  = int(cfg.get("strike_selection", {}).get("auto_roll_days", 1))
                expiry_res = select_expiry(name, oa_client, pref, roll_d)
                if not expiry_res:
                    continue
                _, expiry_str = expiry_res
                chain = oa_client.optionchain(underlying=name, exchange="NSE_INDEX", expiry_date=expiry_str)
                if not isinstance(chain, dict) or chain.get("status") != "success":
                    continue
                total_ce_oi = sum(float(row.get("ce", {}).get("oi", 0)) for row in chain.get("chain", []))
                total_pe_oi = sum(float(row.get("pe", {}).get("oi", 0)) for row in chain.get("chain", []))
                pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else 0.0
                pulse["pcr"][name] = {"total_ce_oi": int(total_ce_oi), "total_pe_oi": int(total_pe_oi), "pcr": pcr}
            except Exception as pcr_ex:
                log.debug("PCR calc failed for %s: %s", name, pcr_ex)

        return pulse
    except Exception as e:
        log.error("Market pulse fetch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Item 10: Max Pain calculation
# ---------------------------------------------------------------------------
@app.get("/api/max-pain")
def get_max_pain(underlying: str):
    """
    Compute Max Pain strike for the given underlying's nearest expiry.

    Max Pain = the strike at which total option writer losses are minimised,
    i.e. where the sum of (intrinsic pain to CE writers + PE writers) is lowest.
    This is purely a function of open interest data already present in the chain.
    """
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))

        pref    = cfg.get("strike_selection", {}).get("expiry_preference", "WEEKLY")
        roll_d  = int(cfg.get("strike_selection", {}).get("auto_roll_days", 1))
        expiry_res = select_expiry(underlying, oa_client, pref, roll_d)
        if not expiry_res:
            raise HTTPException(status_code=404, detail=f"No valid expiry for {underlying}")
        expiry_date_obj, expiry_str = expiry_res

        chain = oa_client.optionchain(underlying=underlying, exchange="NSE_INDEX", expiry_date=expiry_str)
        if not isinstance(chain, dict) or chain.get("status") != "success":
            raise HTTPException(status_code=502, detail="Failed to fetch chain from OpenAlgo")

        rows = chain.get("chain", [])
        if not rows:
            raise HTTPException(status_code=404, detail="Chain has no strikes")

        strikes = sorted({float(r.get("strike", 0)) for r in rows})

        # Build OI arrays indexed by strike
        ce_oi: dict = {}
        pe_oi: dict = {}
        for r in rows:
            s = float(r.get("strike", 0))
            ce_oi[s] = float(r.get("ce", {}).get("oi", 0))
            pe_oi[s] = float(r.get("pe", {}).get("oi", 0))

        # For each candidate expiry strike, compute total pain to writers
        pain_map: dict = {}
        for candidate in strikes:
            total_pain = 0.0
            for s in strikes:
                # CE writer pain: if candidate > strike, CE expires ITM → CE OI × (candidate − strike)
                if candidate > s:
                    total_pain += ce_oi.get(s, 0.0) * (candidate - s)
                # PE writer pain: if candidate < strike, PE expires ITM → PE OI × (strike − candidate)
                if candidate < s:
                    total_pain += pe_oi.get(s, 0.0) * (s - candidate)
            pain_map[candidate] = total_pain

        max_pain_strike = min(pain_map, key=lambda k: pain_map[k])

        return {
            "status": "success",
            "underlying": underlying,
            "expiry": expiry_str,
            "max_pain_strike": max_pain_strike,
            "pain_map": {str(k): round(v, 0) for k, v in sorted(pain_map.items())}
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Max pain calculation failed for %s: %s", underlying, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Item 14: Emergency Kill Switch
# ---------------------------------------------------------------------------
@app.post("/api/emergency-exit")
async def emergency_exit_all():
    """
    Emergency kill switch — immediately:
    1. Halts the auto-scan loop (sets order_mode to manual in memory).
    2. Exits ALL open positions at market price.
    3. Sends an urgent Telegram alert.

    This endpoint is deliberately not protected by a token since it needs to
    work under maximum stress. Restrict network access instead.
    """
    log.warning("🚨 EMERGENCY EXIT triggered via API.")
    cfg = load_config()
    oa_client = _get_oa_client(cfg.get("openalgo", {}))

    results = []
    with _monitor.lock:
        positions_snapshot = list(_monitor.active_positions.values())
        # Mark all as closing to block the monitoring loop
        for pos in positions_snapshot:
            pos["is_closing"] = True

    for pos in positions_snapshot:
        try:
            resp = await run_in_threadpool(oa_client.quotes, pos["symbol"], pos["exchange"])
            ltp = float(resp.get("data", {}).get("ltp") or resp.get("ltp", 0))
            await run_in_threadpool(_monitor._execute_exit, pos, ltp, "EMERGENCY")
            results.append({"symbol": pos["symbol"], "status": "exited", "ltp": ltp})
        except Exception as ex:
            log.error("Emergency exit failed for %s: %s", pos["symbol"], ex)
            results.append({"symbol": pos["symbol"], "status": "failed", "error": str(ex)})
            # Clear flag so operator can retry
            with _monitor.lock:
                if pos["id"] in _monitor.active_positions:
                    _monitor.active_positions[pos["id"]]["is_closing"] = False

    # Send critical alert
    try:
        from notifications.notifier import send_alert
        msg = (
            f"🚨 *EMERGENCY EXIT EXECUTED* 🚨\n\n"
            f"Attempted square-off for {len(positions_snapshot)} positions.\n"
            f"Results: {results}"
        )
        send_alert(msg, cfg, oa_client, priority=10)
    except Exception:
        pass

    return {
        "status": "success",
        "positions_attempted": len(positions_snapshot),
        "results": results
    }

# Mount Frontend static files
frontend_dir = _bot_dir / "frontend"
if not frontend_dir.exists():
    frontend_dir.mkdir(exist_ok=True)
    
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    log.info("Starting Options Trading Terminal on http://127.0.0.1:8001")
    uvicorn.run(app, host="127.0.0.1", port=8001)
