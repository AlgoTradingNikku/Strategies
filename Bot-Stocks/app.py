import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import pandas as pd
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
sys.path.insert(0, str(_bot_dir))

# Configure logging specifically for the web app to avoid duplicating log setups
log = logging.getLogger("UTBotSRChannelsScanner")

# Import scanner functions
from scanner import load_config, run_scan, fetch_history
from signals import compute_utbot_signals, compute_sr_signals
from signal_db import get_signal_history, get_statistics, clear_all_signals
from trading_adapter import place_order as adapter_place_order, get_ltp as adapter_get_ltp
from trade_manager import PositionMonitor
import trade_db

# ---------------------------------------------------------------------------
# Module-level OpenAlgo client cache — avoids re-constructing the client on
# every /api/order request (one client per unique apikey+host combination).
# ---------------------------------------------------------------------------
_oa_client_cache: dict = {}

def _get_oa_client(oa_cfg: dict):
    """Return a cached OpenAlgo API client for the given config."""
    key = (oa_cfg.get("apikey", ""), oa_cfg.get("base_url", "http://127.0.0.1:5000"))
    if key not in _oa_client_cache:
        _oa_client_cache[key] = oa_api(api_key=key[0], host=key[1])
    return _oa_client_cache[key]


app = FastAPI(title="UTBot + SR Channels Scanner API")

_monitor = PositionMonitor()

@app.on_event("startup")
async def startup_event():
    try:
        cfg = load_config()
        _monitor.start(cfg)
    except Exception as e:
        log.error("Failed to start Trade Monitor: %s", e)

@app.on_event("shutdown")
async def shutdown_event():
    _monitor.stop()

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Models for Configuration Updates
# ---------------------------------------------------------------------------
class TelegramConfig(BaseModel):
    enabled: bool = True
    mode: str = "openalgo"
    bot_token: str = ""
    chat_id: str = ""

class OpenAlgoConfig(BaseModel):
    apikey: str
    username: str
    base_url: str
    ws_url: str
    order_mode: str = "manual"
    allowed_actions: str = "BUY_ONLY"
    order_product: str = "MIS"
    order_quantity: int = 1
    order_type: str = "MARKET"

class FlattradeConfig(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    client_id: str = ""
    session_token: str = ""
    order_product: str = "MIS"
    order_quantity: int = 1
    order_type: str = "MARKET"

class MStockConfig(BaseModel):
    client_id: str = ""
    access_token: str = ""
    order_product: str = "MIS"
    order_quantity: int = 1
    order_type: str = "MARKET"

class ShoonyaConfig(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    client_id: str = ""
    session_token: str = ""
    order_product: str = "MIS"
    order_quantity: int = 1
    order_type: str = "MARKET"

class DhanConfig(BaseModel):
    client_id: str = ""
    access_token: str = ""
    order_product: str = "MIS"
    order_quantity: int = 1
    order_type: str = "MARKET"

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

class BotConfig(BaseModel):
    log_level: str
    market_hours_check: bool
    market_open: str
    market_close: str
    auto_refresh_enabled: bool = False

class FiltersConfig(BaseModel):
    ema_filter_enabled: bool
    ema_period: int
    volume_filter_enabled: bool
    volume_sma_period: int
    volume_min_pct: int
    min_alert_score: int
    mtf_filter_enabled: bool
    mtf_timeframe: str
    mtf_neutral_pct: float
    mtf_atr_period: int = 10
    adx_filter_enabled: bool
    adx_min_threshold: float
    adx_strong_threshold: float
    adx_moderate_threshold: float
    rsi_filter_enabled: bool
    rsi_period: int
    rsi_buy_min: float
    rsi_buy_max: float
    rsi_sell_min: float
    rsi_sell_max: float
    rs_period: int
    rs_buy_threshold: float
    rs_sell_threshold: float
    risk_reward_enabled: bool
    rr_atr_multiplier: float
    rr_default_ratio: float
    squeeze_filter_enabled: bool = False
    squeeze_length: int = 20
    squeeze_bb_mult: float = 2.0
    squeeze_kc_mult: float = 1.5
    candle_patterns_enabled: bool
    signal_history_enabled: bool
    outcome_check_hours: int
    win_rate_backtest_enabled: bool = False

class ConfigUpdateRequest(BaseModel):
    data_source: str
    trading_api_source: str = "openalgo"
    exchange: str
    candle_timeframe: str = "5m"
    scan_timeframe: str | None = None
    scan_interval_seconds: int
    segment: list[str] | str
    use_symbols: bool
    signal_lookback_candles: int
    strategy: StrategyConfig
    sr_channels: SRChannelsConfig
    filters: FiltersConfig
    telegram: TelegramConfig
    openalgo: OpenAlgoConfig
    flattrade: FlattradeConfig = FlattradeConfig()
    mstock: MStockConfig = MStockConfig()
    shoonya: ShoonyaConfig = ShoonyaConfig()
    dhan: DhanConfig = DhanConfig()
    data: dict
    bot: BotConfig
    symbols: list[str]

class OrderRequest(BaseModel):
    symbol: str
    action: str          # "BUY" or "SELL"
    exchange: str = "NSE"
    price_type: str = "MARKET"
    product: str = "MIS"
    quantity: int = 1
    price: float = 0.0
    trigger_price: float = 0.0
    strategy: str = "UTBotScanner"

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    """Retrieve the current configuration file."""
    try:
        cfg = load_config()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")

def _update_commented_map(cm, updates: dict) -> None:
    """Recursively update a ruamel.yaml CommentedMap in-place from a plain dict.

    Only existing keys are updated — new keys are added without comments.
    This preserves all inline comments on unchanged keys.
    """
    for key, value in updates.items():
        if isinstance(value, dict) and key in cm and hasattr(cm[key], "items"):
            _update_commented_map(cm[key], value)
        else:
            cm[key] = value


@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    """Save the updated configuration file to disk, preserving all YAML comments."""
    try:
        config_path = _bot_dir / "config.yml"
        config_dict = req.model_dump()

        ryaml = _RYAML()
        ryaml.preserve_quotes = True

        # Load the existing file to capture its comment structure
        with open(config_path, "r", encoding="utf-8") as fh:
            commented_map = ryaml.load(fh)

        # Merge new values into the CommentedMap without disturbing comment nodes
        _update_commented_map(commented_map, config_dict)

        # Write back — comments and key order are preserved
        with open(config_path, "w", encoding="utf-8") as fh:
            ryaml.dump(commented_map, fh)

        return {"status": "success", "message": "Configuration saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

@app.get("/api/ltp/{symbol}")
async def get_ltp_endpoint(symbol: str, exchange: str = None):
    """
    Fetch the live Last Traded Price via the configured trading_api_source.

    Returns { "symbol": "INFY", "ltp": 1423.55, "exchange": "NSE", "source": "openalgo" }
    Used by the dashboard to get a fresh price for LIMIT orders at click time.
    """
    try:
        cfg    = load_config()
        exch   = exchange or cfg.get("exchange", "NSE")
        source = cfg.get("trading_api_source", "openalgo").lower()
        ltp    = await run_in_threadpool(adapter_get_ltp, cfg, symbol, exch)
        log.info("LTP fetched for %s (%s) via %s: ₹%.2f", symbol, exch, source.upper(), ltp)
        return {"symbol": symbol, "exchange": exch, "ltp": round(float(ltp), 2), "source": source}
    except HTTPException:
        raise
    except Exception as e:
        log.error("LTP fetch failed for %s: %s", symbol, e)
        raise HTTPException(status_code=502, detail=f"LTP fetch failed: {e}")


@app.post("/api/order")
async def place_order_endpoint(req: OrderRequest):
    """Place an order via the configured trading_api_source."""
    try:
        cfg    = load_config()
        source = cfg.get("trading_api_source", "openalgo").lower()
        result = await run_in_threadpool(adapter_place_order, cfg, req)
        if result.get("status") == "error":
            raise HTTPException(status_code=502, detail=f"{source.upper()} error: {result.get('message', result)}")
        
        # Register position if trade management is enabled
        tm_cfg = cfg.get("trade_management", {})
        if tm_cfg.get("enabled", False):
            # Run in thread pool to avoid blocking async endpoint
            await run_in_threadpool(_monitor.open_position, result, req, cfg)

        return {"status": "success", "order": result.get("raw", result), "orderid": result.get("orderid", "")}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Order placement failed for %s: %s", req.symbol, e)
        raise HTTPException(status_code=500, detail=f"Order failed: {e}")

# ---------------------------------------------------------------------------
# Positions & Trade Management Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/positions")
async def get_positions():
    """List all currently active/open monitored positions."""
    try:
        # Re-fetch from database to get latest status
        open_pos = await run_in_threadpool(trade_db.get_open_positions)
        return {"status": "success", "positions": open_pos}
    except Exception as e:
        log.error("Failed to retrieve open positions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/positions/closed")
async def get_closed_positions_endpoint(limit: int = 50, offset: int = 0):
    """Get paginated history of closed positions."""
    try:
        closed = await run_in_threadpool(trade_db.get_closed_positions, limit, offset)
        return {"status": "success", "positions": closed}
    except Exception as e:
        log.error("Failed to retrieve closed positions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/positions/{pos_id}/events")
async def get_pos_events(pos_id: int):
    """Get the full audit log/events for a specific position."""
    try:
        events = await run_in_threadpool(trade_db.get_position_events, pos_id)
        return {"status": "success", "events": events}
    except Exception as e:
        log.error("Failed to retrieve position events: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/positions/{pos_id}/close")
async def manual_close_position(pos_id: int):
    """Manually square off and close a monitored position."""
    # Find the position in active list
    target_pos = None
    with _monitor.lock:
        target_pos = _monitor.active_positions.get(pos_id)
    
    if not target_pos:
        raise HTTPException(status_code=444, detail="Active position not found or already closed.")

    try:
        cfg = load_config()
        # Fetch current LTP for exit computation
        ltp = await run_in_threadpool(adapter_get_ltp, cfg, target_pos["symbol"], target_pos["exchange"])
        
        # Execute the exit in background thread pool
        await run_in_threadpool(_monitor._execute_exit, target_pos, ltp, "MANUAL")
        return {"status": "success", "message": f"Manual exit triggered for position {pos_id}."}
    except Exception as e:
        log.error("Manual exit failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Manual exit failed: {e}")


@app.post("/api/positions/close-all")
async def close_all_positions_endpoint():
    """Square off and close ALL active monitored positions in one click (Parallelized)."""
    try:
        cfg = load_config()
        open_pos_list = await run_in_threadpool(trade_db.get_open_positions)
        if not open_pos_list:
            return {"status": "success", "message": "No active open positions to close.", "closed_count": 0}

        def _close_single_pos(p):
            pos_id = p["id"]
            target_pos = None
            with _monitor.lock:
                target_pos = _monitor.active_positions.get(pos_id)

            symbol = p["symbol"]
            exchange = p["exchange"]

            ltp = adapter_get_ltp(cfg, symbol, exchange)
            if ltp <= 0:
                ltp = float(p.get("entry_price", 0.0))

            if target_pos:
                try:
                    _monitor._execute_exit(target_pos, ltp, "MANUAL_CLOSE_ALL")
                except Exception as e:
                    log.warning("Monitor exit for %s raised exception: %s", symbol, e)
            else:
                action = "SELL" if p["direction"].upper() == "BUY" else "BUY"
                from types import SimpleNamespace
                req = SimpleNamespace(
                    symbol=symbol,
                    exchange=exchange,
                    action=action,
                    quantity=p["quantity"],
                    product=p.get("product", "MIS"),
                    price_type="MARKET",
                    price=ltp,
                    trigger_price=0.0,
                    strategy="UTBot_SR_CloseAll",
                )
                try:
                    adapter_place_order(cfg, req)
                except Exception as e:
                    log.warning("Adapter place order for %s exit raised: %s", symbol, e)

            # Always mark position as CLOSED in trade_db & clear from monitor active positions
            entry = float(p.get("entry_price", ltp or 1.0))
            direction = p.get("direction", "BUY")
            pnl_pct = round(((ltp - entry)/entry)*100, 2) if direction.upper() == "BUY" else round(((entry - ltp)/entry)*100, 2)

            trade_db.update_position(
                pos_id,
                status="CLOSED",
                close_reason="MANUAL_CLOSE_ALL",
                close_price=ltp,
                close_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                pnl_pct=pnl_pct
            )
            with _monitor.lock:
                _monitor.active_positions.pop(pos_id, None)

            return symbol

        closed_count = 0
        errors = []

        def run_all_exits():
            nonlocal closed_count
            with ThreadPoolExecutor(max_workers=min(10, len(open_pos_list))) as pool:
                futures = {pool.submit(_close_single_pos, p): p for p in open_pos_list}
                for f in as_completed(futures):
                    p = futures[f]
                    try:
                        f.result()
                        closed_count += 1
                    except Exception as exc:
                        errors.append(f"{p['symbol']}: {exc}")

        await run_in_threadpool(run_all_exits)

        return {
            "status": "success",
            "message": f"Closed {closed_count} position(s)." + (f" Errors: {', '.join(errors)}" if errors else ""),
            "closed_count": closed_count,
        }
    except Exception as e:
        log.error("Close all positions failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Close all positions failed: {e}")

@app.post("/api/reset-data")
async def reset_data_endpoint():
    """Clear all trades, position events, signal history, and outcomes to start fresh."""
    try:
        await run_in_threadpool(trade_db.clear_all_trades)
        with _monitor.lock:
            _monitor.active_positions.clear()
        await run_in_threadpool(clear_all_signals)
        return {
            "status": "success",
            "message": "All signal history, trade records, and position logs cleared successfully. Starting fresh!"
        }
    except Exception as e:
        log.error("Failed to reset databases: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to reset databases: {e}")


@app.post("/api/scan")
async def trigger_scan(timeframe: str | None = None, mode: str | None = None):
    """Trigger a manual scan and return the results.

    The scan runs in a thread pool so the FastAPI event loop is not blocked —
    other dashboard endpoints (/api/logs, /api/config, etc.) remain responsive
    while a long-running scan is in progress.
    """
    try:
        cfg = load_config()
        buy, sell, label, tf, total_symbols = await run_in_threadpool(
            run_scan,
            cfg,
            timeframe_override=timeframe,
            mode_override=mode,
        )
        return {
            "status": "success",
            "segment_label": label,
            "timeframe": tf,
            "buy_signals": buy,
            "sell_signals": sell,
            "total_scanned": total_symbols,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run scan: {e}")

@app.get("/api/history/{symbol}")
def get_history(symbol: str, timeframe: str | None = None):
    """Fetch historical OHLC data and calculate UTBot/SR values for charting."""
    try:
        cfg = load_config()
        tf = timeframe or cfg.get("candle_timeframe", cfg.get("scan_timeframe", "5m"))
        
        # Fetch history via the scanner module's data source
        df = fetch_history(symbol, tf, cfg)
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail=f"No historical data found for {symbol}.")

        # Make a copy and compute signals
        df = df.copy()
        
        # Calculate UT Bot
        strat = cfg.get("strategy", {})
        df = compute_utbot_signals(
            df,
            key_value=float(strat.get("key_value", 1.0)),
            atr_period=int(strat.get("atr_period", 2))
        )
        
        # Calculate S/R Zones — unpack tuple returned by compute_sr_signals
        sr_cfg = cfg.get("sr_channels", {})
        df, zones = compute_sr_signals(
            df,
            pivot_period=int(sr_cfg.get("pivot_period", 10)),
            source=sr_cfg.get("source", "High/Low"),
            channel_width_pct=float(sr_cfg.get("channel_width_pct", 5.0)),
            min_strength=int(sr_cfg.get("min_strength", 1)),
            max_num_sr=int(sr_cfg.get("max_num_sr", 6)),
            loopback=int(sr_cfg.get("loopback", 290)),
            proximity_pct=float(sr_cfg.get("proximity_pct", 0.2))
        )

        # Format zones for frontend: [ [hi, lo], [hi, lo], ... ]
        formatted_zones = [{"high": float(z[0]), "low": float(z[1])} for z in zones]

        # Prepare chart series (OHLC + UT Trail + Buy/Sell flags) — vectorised
        timestamps  = (df.index.astype("int64") // 10**9).tolist()   # seconds
        opens       = df["open"].tolist()
        highs       = df["high"].tolist()
        lows        = df["low"].tolist()
        closes      = df["close"].tolist()
        ut_trails   = df["ut_trail"].where(df["ut_trail"].notna(), other=None).tolist() \
                      if "ut_trail" in df.columns else [None] * len(df)
        buy_sigs    = df["ut_buy"].tolist()  if "ut_buy"  in df.columns else [False] * len(df)
        sell_sigs   = df["ut_sell"].tolist() if "ut_sell" in df.columns else [False] * len(df)

        chart_data = [
            {
                "time":     timestamps[k],
                "open":     opens[k],
                "high":     highs[k],
                "low":      lows[k],
                "close":    closes[k],
                "ut_trail": ut_trails[k],
                "buy":      bool(buy_sigs[k]),
                "sell":     bool(sell_sigs[k]),
            }
            for k in range(len(df))
        ]

        return {
            "symbol": symbol,
            "timeframe": tf,
            "history": chart_data,
            "sr_zones": formatted_zones
        }
    except Exception as e:
        log.error("Failed to generate history for %s: %s", symbol, e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/signal-history")
def get_history_list(limit: int = 50, offset: int = 0):
    """Retrieve paginated list of historical signals and outcomes."""
    try:
        history = get_signal_history(limit=limit, offset=offset)
        return {"status": "success", "history": history}
    except Exception as e:
        log.error("Failed to retrieve signal history: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve signal history: {e}")

@app.get("/api/statistics")
def get_stats(days: int = 30):
    """Retrieve statistical performance breakdown for logged signals."""
    try:
        cfg = load_config()
        stats = get_statistics(days=days, config=cfg)
        return {"status": "success", "statistics": stats}
    except Exception as e:
        log.error("Failed to retrieve statistics: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve statistics: {e}")

@app.get("/api/index-status")
async def get_index_status_endpoint():
    """
    Fetch live index levels for NIFTY 50, BANKNIFTY, and NIFTY IT.
    """
    try:
        def fetch_task():
            import yfinance as yf
            tickers = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTY IT": "^CNXIT"}
            results = {}
            try:
                data = yf.download(tickers=list(tickers.values()), period="5d", interval="1d", progress=False)
                if not data.empty:
                    for label, sym in tickers.items():
                        if isinstance(data.columns, pd.MultiIndex):
                            col = ('Close', sym)
                        else:
                            col = 'Close'
                        if col in data.columns:
                            series = data[col].dropna()
                            if len(series) >= 2:
                                prev = float(series.iloc[-2])
                                curr = float(series.iloc[-1])
                                chg = curr - prev
                                pct = (chg / prev) * 100
                                results[label] = {
                                    "ltp": round(curr, 2),
                                    "change": round(chg, 2),
                                    "pct": round(pct, 2)
                                }
                            elif len(series) == 1:
                                curr = float(series.iloc[-1])
                                results[label] = {
                                    "ltp": round(curr, 2),
                                    "change": 0.0,
                                    "pct": 0.0
                                }
            except Exception as e:
                log.error("Error in fetch_task: %s", e)
            return results

        results = await run_in_threadpool(fetch_task)
        return {"status": "success", "data": results}
    except Exception as e:
        log.error("Failed to retrieve index status: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve index status: {e}")

@app.get("/api/logs")
def get_logs(lines: int = 150):
    """Retrieve the latest log lines from scanner.log."""
    try:
        from collections import deque
        log_path = _bot_dir / "scanner.log"
        if not log_path.exists():
            return {"logs": "No log file found."}

        with open(log_path, "r", encoding="utf-8") as fh:
            last_lines = deque(fh, maxlen=lines)

        return {"logs": "".join(last_lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {e}")


# ---------------------------------------------------------------------------
# Serve Web Frontend (mount static files at last root)
# ---------------------------------------------------------------------------
frontend_dir = _bot_dir / "frontend"
if not frontend_dir.exists():
    frontend_dir.mkdir(exist_ok=True)

# Mount the static directory
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    log.info("Starting local web dashboard server on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
