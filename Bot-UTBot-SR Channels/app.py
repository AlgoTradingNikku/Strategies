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

# Add project root to sys.path
_bot_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_bot_dir))

# Configure logging specifically for the web app to avoid duplicating log setups
log = logging.getLogger("UTBotSRChannelsScanner")

# Import scanner functions
from scanner import load_config, run_scan, fetch_history
from signals import compute_utbot_signals, compute_sr_signals

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
    mode: str
    bot_token: str
    chat_id: str

class OpenAlgoConfig(BaseModel):
    apikey: str
    username: str
    base_url: str
    ws_url: str

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

class ConfigUpdateRequest(BaseModel):
    data_source: str
    exchange: str
    scan_timeframe: str
    scan_interval_seconds: int
    segment: list[str] | str
    use_symbols: bool
    signal_mode: str
    signal_lookback_candles: int
    strategy: StrategyConfig
    sr_channels: SRChannelsConfig
    telegram: TelegramConfig
    openalgo: OpenAlgoConfig
    data: dict
    bot: BotConfig
    symbols: list[str]

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

@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    """Save the updated configuration file to disk."""
    try:
        config_path = _bot_dir / "config.yml"
        # Convert request to pure python dict and dump to YAML
        config_dict = req.model_dump()
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(config_dict, fh, sort_keys=False, default_flow_style=False)
        return {"status": "success", "message": "Configuration saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

@app.post("/api/scan")
def trigger_scan(timeframe: str | None = None, mode: str | None = None):
    """Trigger a manual scan and return the results."""
    try:
        cfg = load_config()
        buy, sell, label, tf = run_scan(
            cfg,
            timeframe_override=timeframe,
            mode_override=mode
        )
        return {
            "status": "success",
            "segment_label": label,
            "timeframe": tf,
            "buy_signals": buy,
            "sell_signals": sell,
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
        
        # Calculate S/R Zones
        sr_cfg = cfg.get("sr_channels", {})
        df = compute_sr_signals(
            df,
            pivot_period=int(sr_cfg.get("pivot_period", 10)),
            source=sr_cfg.get("source", "High/Low"),
            channel_width_pct=float(sr_cfg.get("channel_width_pct", 5.0)),
            min_strength=int(sr_cfg.get("min_strength", 1)),
            max_num_sr=int(sr_cfg.get("max_num_sr", 6)),
            loopback=int(sr_cfg.get("loopback", 290)),
            proximity_pct=float(sr_cfg.get("proximity_pct", 0.2))
        )

        # Extract S/R zones from dataframe metadata or details
        zones = []
        if hasattr(df, "attrs") and "sr_zones" in df.attrs:
            zones = df.attrs["sr_zones"]
        elif "sr_zones" in df.columns:
            # Fallback if saved in series or metadata
            last_zones = df["sr_zones"].iloc[-1]
            if isinstance(last_zones, list):
                zones = last_zones

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
# Background Continuous Scanner
# ---------------------------------------------------------------------------
import threading
import time
from scanner import (
    _is_market_hours,
    _parse_timeframe,
    build_telegram_message,
    print_results_table,
    send_telegram_alert,
)
from nse_indices import get_index_symbols

def run_background_scanner():
    log.info("Background scanner scheduler started on server.")
    last_scan_boundary = None
    
    while True:
        try:
            cfg = load_config()
            timeframe = cfg.get("scan_timeframe", "15m")
            scan_interval = int(cfg.get("scan_interval_seconds", 300))
            
            if _is_market_hours(cfg):
                # Calculate current candle boundary to avoid duplicate scans
                try:
                    candle_secs = int(_parse_timeframe(timeframe).total_seconds())
                    epoch_secs  = int(datetime.now().timestamp())
                    boundary    = (epoch_secs // candle_secs) * candle_secs
                except ValueError:
                    boundary = None

                if boundary != last_scan_boundary:
                    last_scan_boundary = boundary
                    
                    buy, sell, label, tf = run_scan(cfg)
                    
                    # Calculate total tickers scanned
                    segment = cfg.get("segment", "")
                    use_symbols = cfg.get("use_symbols", False)
                    if isinstance(segment, str):
                        seg_list = [segment] if segment.strip() else []
                    else:
                        seg_list = [s for s in (segment or []) if s and s.strip()]
                    
                    total_syms = set()
                    for s in seg_list:
                        syms = get_index_symbols(s)
                        if syms:
                            total_syms.update(syms)
                    if use_symbols or not seg_list:
                        total_syms.update(cfg.get("symbols", []))
                    total = len(total_syms) if total_syms else len(cfg.get("symbols", []))

                    print_results_table(buy, sell, label, tf, total)
                    
                    if buy or sell:
                        eff_mode = cfg.get("signal_mode", "UTBot+SR")
                        msg = build_telegram_message(buy, sell, label, tf, eff_mode, total)
                        tg_result = send_telegram_alert(msg, priority=8)
                        if "error" in tg_result:
                            log.warning("Telegram alert failed: %s", tg_result["error"])
                        else:
                            log.info("✅ Telegram alert sent successfully.")
                    else:
                        log.info("No signals — skipping Telegram alert.")
                else:
                    log.debug("Same candle boundary — waiting for next bar...")
            else:
                log.debug("Outside market hours — sleeping...")
                
            time.sleep(scan_interval)
        except Exception as e:
            log.error("Error in background scanner thread: %s", e)
            time.sleep(60)

@app.on_event("startup")
def start_scanner_thread():
    t = threading.Thread(target=run_background_scanner, daemon=True)
    t.start()

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
