import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

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
from signal_db import get_signal_history, get_statistics

app = FastAPI(title="UTBot + SR Channels Scanner API")

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
    exchange: str
    scan_timeframe: str
    scan_interval_seconds: int
    segment: list[str] | str
    use_symbols: bool
    signal_lookback_candles: int
    strategy: StrategyConfig
    sr_channels: SRChannelsConfig
    filters: FiltersConfig
    telegram: TelegramConfig
    openalgo: OpenAlgoConfig
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

@app.post("/api/order")
async def place_order(req: OrderRequest):
    """Place a market order via OpenAlgo for a scanner signal."""
    try:
        cfg = load_config()
        oa_cfg = cfg.get("openalgo", {})
        client = oa_api(
            api_key=oa_cfg.get("apikey", ""),
            host=oa_cfg.get("base_url", "http://127.0.0.1:5000"),
        )
        response = client.placeorder(
            strategy=req.strategy,
            symbol=req.symbol,
            action=req.action,
            exchange=req.exchange,
            price_type=req.price_type,
            product=req.product,
            quantity=req.quantity,
            price=req.price,
            trigger_price=req.trigger_price
        )
        # openalgo returns a dict; treat any non-error as success
        if isinstance(response, dict) and response.get("status") == "error":
            raise HTTPException(status_code=502, detail=f"OpenAlgo error: {response.get('message', response)}")
        log.info("Order placed via OpenAlgo: %s %s qty=%d → %s", req.action, req.symbol, req.quantity, response)
        return {"status": "success", "order": response}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Order placement failed for %s: %s", req.symbol, e)
        raise HTTPException(status_code=500, detail=f"Order failed: {e}")

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
        tf = timeframe or cfg.get("scan_timeframe", "15m")
        
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

        # Prepare chart series (OHLC + UT Trail + Buy/Sell flags)
        chart_data = []
        for idx, row in df.iterrows():
            timestamp = idx.timestamp() * 1000  # JS epoch timestamp in ms
            
            # Extract UT Trail stop
            ut_trail = float(row["ut_trail"]) if "ut_trail" in row and not pd.isna(row["ut_trail"]) else None
            
            # Determine UT Bot buy/sell flags
            buy_signal = bool(row["ut_buy"]) if "ut_buy" in row else False
            sell_signal = bool(row["ut_sell"]) if "ut_sell" in row else False

            chart_data.append({
                "time": int(timestamp // 1000),  # TV Lightweight charts uses seconds for unix timestamp
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "ut_trail": ut_trail,
                "buy": buy_signal,
                "sell": sell_signal
            })

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

@app.get("/api/logs")
def get_logs(lines: int = 150):
    """Retrieve the latest log lines from scanner.log."""
    try:
        log_path = _bot_dir / "scanner.log"
        if not log_path.exists():
            return {"logs": "No log file found."}
        
        # Read last N lines
        with open(log_path, "r", encoding="utf-8") as fh:
            all_lines = fh.readlines()
            
        last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
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
