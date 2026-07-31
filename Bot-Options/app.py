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
from pathlib import Path
from datetime import datetime
import yaml
import uvicorn
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

app = FastAPI(title="Options Trading Terminal API")

@app.on_event("startup")
async def startup_event():
    try:
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        _monitor.start(cfg, oa_client)
    except Exception as e:
        log.error("Failed to start Options Position Monitor: %s", e)

@app.on_event("shutdown")
async def shutdown_event():
    _monitor.stop()

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
    """Square off and exit position manually."""
    target_pos = None
    with _monitor.lock:
        target_pos = _monitor.active_positions.get(pos_id)
        
    if not target_pos:
        raise HTTPException(status_code=404, detail="Active position not found.")
        
    try:
        # Fetch current LTP from OpenAlgo
        cfg = load_config()
        oa_client = _get_oa_client(cfg.get("openalgo", {}))
        resp = await run_in_threadpool(oa_client.quotes, target_pos["symbol"], target_pos["exchange"])
        
        ltp = float(resp.get("data", {}).get("ltp") or resp.get("ltp"))
        
        # Execute exit squaring off
        await run_in_threadpool(_monitor._execute_exit, target_pos, ltp, "MANUAL")
        return {"status": "success", "message": f"Square off successfully executed for position {pos_id}."}
    except Exception as e:
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

# Mount Frontend static files
frontend_dir = _bot_dir / "frontend"
if not frontend_dir.exists():
    frontend_dir.mkdir(exist_ok=True)
    
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    log.info("Starting Options Trading Terminal on http://127.0.0.1:8001")
    uvicorn.run(app, host="127.0.0.1", port=8001)
