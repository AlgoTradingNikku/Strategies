"""
===============================================================================
  BOT-Antigravity — FastAPI Dashboard Server
===============================================================================

Launches the UT Bot trading engine as background threads and exposes a full
REST + Server-Sent-Events API consumed by the professional trading dashboard.

Run (any of these are equivalent):
    python app.py                                     <- recommended, matches Bot-Stocks
    python dashboard.py
    python server.py
    uvicorn server:app --host 127.0.0.1 --port 9000 --reload

API endpoints:
  GET  /api/status          Bot heartbeat, market hours, workers, ML status
  GET  /api/config          Current config.yml as JSON
  POST /api/config          Save config (preserves YAML comments); triggers reload
  GET  /api/signals         Paginated signal history from signals.db
  GET  /api/signals/stats   Win/loss statistics
  POST /api/signals/label   Trigger label_signals.py (async subprocess)
  POST /api/ml/train        Train XGBoost model (SSE stdout stream)
  GET  /api/ml/report       ML model performance report
  GET  /api/logs            Last N lines from utbot.log
  GET  /api/logs/stream     SSE: tail utbot.log in real time
  GET  /api/ltp/{symbol}    Live price from OpenAlgo
  GET  /api/htf-trend       Current HTF trend per symbol
  GET  /api/system          CPU / RAM / disk stats
  POST /api/bot/restart     Restart all bot workers
  GET  /api/positions               Open positions (live P&L, lots, expiry countdown)
  GET  /api/positions/closed        Closed trade history (paginated)
  GET  /api/positions/{id}/events   Audit log for one position
  POST /api/positions/{id}/close    Manually close an open position
===============================================================================
"""

import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ruamel.yaml import YAML as _RYAML

# ---------------------------------------------------------------------------
# Path setup — so we can import the bot modules
# ---------------------------------------------------------------------------
_server_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_server_dir))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_server_dir / "utbot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("UTBotServer")

# ---------------------------------------------------------------------------
# Import bot modules
# ---------------------------------------------------------------------------
from app import (
    HtfTrendStore,
    LivePriceMonitor,
    TimeframeWorker,
    _check_single_instance,
    _is_market_hours,
    _parse_timeframe,
    _print_banner,
    compute_utbot_signals,
    load_config,
    position_monitor,
)
import trade_db
from instrument_master import expiry_countdown
from trade_management.models import calc_gain_pct, calc_lots, calc_pnl_amount
try:
    from signal_logger import labeled_count, signal_count
    _SIGNAL_LOGGER_AVAILABLE = True
except ImportError:
    _SIGNAL_LOGGER_AVAILABLE = False
    signal_count = labeled_count = lambda: 0

try:
    from ml_filter import MLFilter, MODEL_PATH, DB_PATH as ML_DB_PATH
    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False
    MLFilter = None
    MODEL_PATH = _server_dir / "ml_model.pkl"
    ML_DB_PATH = _server_dir / "signals.db"

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Global Bot State — wraps the threading engine
# ---------------------------------------------------------------------------

class BotEngine:
    """Manages the lifecycle of all TimeframeWorker threads."""

    def __init__(self):
        self.threads: list[threading.Thread] = []
        self.stop_event: threading.Event = threading.Event()
        self.htf_store: HtfTrendStore = HtfTrendStore()
        self.ltp_map: dict = {}
        self.config: dict = {}
        self.start_time: datetime | None = None
        self.worker_names: list[str] = []
        self._lock = threading.Lock()
        self._running = False

    def start(self, config: dict):
        """Start all workers based on config."""
        with self._lock:
            if self._running:
                return

        self.config = config
        self.stop_event = threading.Event()
        self.threads = []
        self.worker_names = []
        self.htf_store = HtfTrendStore()
        self.start_time = datetime.now()

        try:
            self._spawn_workers(config)
            self._running = True
            log.info("[BotEngine] All workers started.")
        except Exception as exc:
            log.error("[BotEngine] Failed to start workers: %s", exc)

    def stop(self, timeout: float = 8.0):
        """Signal all workers to stop and wait for them."""
        with self._lock:
            if not self._running:
                return
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=timeout)
        self.threads = []
        self._running = False
        log.info("[BotEngine] All workers stopped.")

    def restart(self, config: dict):
        """Stop current workers and start fresh with new config."""
        log.info("[BotEngine] Restarting workers...")
        self.stop()
        self.start(config)

    def is_running(self) -> bool:
        return self._running and any(t.is_alive() for t in self.threads)

    def alive_worker_count(self) -> int:
        return sum(1 for t in self.threads if t.is_alive())

    def _spawn_workers(self, config: dict):
        from openalgo import api as oa_api

        symbols: list[str]          = config.get("symbols") or []
        index_symbols: list[str]    = config.get("index_symbols") or []
        trend_timeframe: str        = config.get("trend_timeframe", "15m")
        equity_timeframe: str       = config.get("equity_timeframe", "5m")
        option_timeframes: list[str] = config.get("option_timeframes") or [equity_timeframe]
        exchange: str               = config.get("exchange", "NSE")
        mtf_enabled: bool           = config.get("mtf_filter", {}).get("enabled", False)
        data_source: str            = config.get("data_source", "openalgo").lower()
        option_exchange             = "NFO"

        if data_source == "openalgo":
            oa_cfg = config.get("openalgo", {})
            api_key = oa_cfg.get("apikey", "")
            base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
            ws_url = oa_cfg.get("ws_url", "ws://127.0.0.1:8765")
            client = oa_api(api_key=api_key, host=base_url, ws_url=ws_url)

            all_ws_instruments = []
            for s in symbols:
                all_ws_instruments.append({"exchange": exchange, "symbol": s})
            for s in index_symbols:
                all_ws_instruments.append({"exchange": option_exchange, "symbol": s})

            ws_monitor = LivePriceMonitor(client, all_ws_instruments, self.stop_event)
            ws_thread = threading.Thread(target=ws_monitor.run, name="WS-LivePrices", daemon=True)
            self.threads.append(ws_thread)
            ws_thread.start()
            self.ltp_map = ws_monitor.ltp_map
            time.sleep(2)
        else:
            client = None
            self.ltp_map = {}

        # Trend workers (trend-only)
        if mtf_enabled:
            for symbol in symbols:
                worker = TimeframeWorker(
                    symbol=symbol, timeframe=trend_timeframe, client=client,
                    config=config, stop_event=self.stop_event,
                    ltp_map=self.ltp_map, htf_store=self.htf_store, role="htf",
                )
                t = threading.Thread(target=worker.run, name=f"Worker-{symbol}-{trend_timeframe}-TREND", daemon=True)
                self.threads.append(t)
                self.worker_names.append(f"{symbol}@{trend_timeframe} [TREND]")
                t.start()

        # Equity workers (signal + order)
        for symbol in symbols:
            worker = TimeframeWorker(
                symbol=symbol, timeframe=equity_timeframe, client=client,
                config=config, stop_event=self.stop_event,
                ltp_map=self.ltp_map,
                htf_store=self.htf_store if mtf_enabled else None,
                role="ltf",
            )
            t = threading.Thread(target=worker.run, name=f"Worker-{symbol}-{equity_timeframe}-EQUITY", daemon=True)
            self.threads.append(t)
            self.worker_names.append(f"{symbol}@{equity_timeframe} [EQUITY]")
            t.start()

        # Option/index workers
        for idx_sym in index_symbols:
            idx_config = dict(config)
            idx_config["exchange"] = option_exchange
            for tf in option_timeframes:
                worker = TimeframeWorker(
                    symbol=idx_sym, timeframe=tf, client=client,
                    config=idx_config, stop_event=self.stop_event,
                    ltp_map=self.ltp_map,
                )
                t = threading.Thread(target=worker.run, name=f"Worker-{idx_sym}-{tf}", daemon=True)
                self.threads.append(t)
                self.worker_names.append(f"{idx_sym}@{tf} [OPT]")
                t.start()


# Singleton engine
_engine = BotEngine()

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="UT Bot Antigravity — Trading Platform API",
    version="1.0.0",
    description="Professional trading dashboard for the UT Bot Antigravity engine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    # Refuse to start a second instance against the same broker/positions —
    # this used to only run in app.py's now-retired headless main() loop, so
    # the dashboard path had no protection against being launched twice.
    _check_single_instance()

    log.info("[Server] FastAPI startup — launching bot workers...")
    try:
        config = load_config()
        _print_banner(config)
        _engine.start(config)
    except Exception as exc:
        log.error("[Server] Startup error: %s", exc)

    # Trade management runs independently of the signal-worker engine — a
    # config-reload / bot restart (see /api/bot/restart) shouldn't interrupt
    # monitoring of positions that are already open in the market.
    try:
        position_monitor.start(config)
    except Exception as exc:
        log.error("[Server] Trade management startup error: %s", exc)


@app.on_event("shutdown")
async def _shutdown():
    log.info("[Server] FastAPI shutdown — stopping bot workers...")
    _engine.stop()
    position_monitor.stop()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class ConfigSaveRequest(BaseModel):
    config: dict


class BotRestartRequest(BaseModel):
    reason: str = "manual"


# ---------------------------------------------------------------------------
# Helper: SQLite signals.db query
# ---------------------------------------------------------------------------
_DB_PATH = _server_dir / "signals.db"

def _query_signals(limit: int = 50, offset: int = 0, symbol: str = None,
                   signal_type: str = None) -> list[dict]:
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        where_clauses = []
        params = []
        if symbol:
            where_clauses.append("symbol = ?")
            params.append(symbol)
        if signal_type:
            where_clauses.append("signal_type = ?")
            params.append(signal_type.upper())
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        rows = conn.execute(
            f"SELECT * FROM signals {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM signals {where_sql}", params
        ).fetchone()[0]
        conn.close()
        return [dict(r) for r in rows], total
    except Exception as exc:
        log.error("signals query error: %s", exc)
        return [], 0


def _query_signal_stats(days: int = 30) -> dict:
    if not _DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        since = datetime.now().strftime(f"%Y-%m-%d")

        total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        today_str = datetime.now().strftime("%Y-%m-%d")
        today = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE logged_at >= ?", (today_str,)
        ).fetchone()[0]
        buys = conn.execute("SELECT COUNT(*) FROM signals WHERE signal_type='BUY'").fetchone()[0]
        sells = conn.execute("SELECT COUNT(*) FROM signals WHERE signal_type='SELL'").fetchone()[0]
        labeled = conn.execute("SELECT COUNT(*) FROM signals WHERE labeled=1").fetchone()[0]

        win5 = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE labeled=1 AND label_5=1"
        ).fetchone()[0]
        total5 = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE labeled=1 AND label_5 IS NOT NULL"
        ).fetchone()[0]

        win10 = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE labeled=1 AND label_10=1"
        ).fetchone()[0]
        total10 = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE labeled=1 AND label_10 IS NOT NULL"
        ).fetchone()[0]

        # By symbol
        by_symbol = conn.execute(
            "SELECT symbol, signal_type, COUNT(*) as cnt FROM signals GROUP BY symbol, signal_type"
        ).fetchall()

        # Recent signals (last 10)
        recent = conn.execute(
            "SELECT bar_time, symbol, timeframe, signal_type, close, atr_stop, rsi_14 "
            "FROM signals ORDER BY id DESC LIMIT 10"
        ).fetchall()

        conn.close()
        return {
            "total": total,
            "today": today,
            "buys": buys,
            "sells": sells,
            "labeled": labeled,
            "win_rate_5": round(win5 / total5 * 100, 1) if total5 else None,
            "win_rate_10": round(win10 / total10 * 100, 1) if total10 else None,
            "by_symbol": [dict(r) for r in by_symbol],
            "recent": [dict(r) for r in recent],
        }
    except Exception as exc:
        log.error("signal stats error: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    """Bot heartbeat, worker status, ML readiness, market hours."""
    config = load_config()
    is_market = _is_market_hours(config)
    ml_ready = False
    ml_threshold = None
    if _ML_AVAILABLE and MLFilter is not None:
        ml_cfg = config.get("ml", {})
        ml_ready = bool(ml_cfg.get("enabled", False)) and MODEL_PATH.exists()
        ml_threshold = float(ml_cfg.get("confidence_threshold", 0.60))

    uptime_seconds = None
    if _engine.start_time:
        uptime_seconds = int((datetime.now() - _engine.start_time).total_seconds())

    return {
        "status": "running" if _engine.is_running() else "stopped",
        "bot_running": _engine.is_running(),
        "active_workers": _engine.alive_worker_count(),
        "total_workers": len(_engine.threads),
        "worker_names": _engine.worker_names,
        "is_market_hours": is_market,
        "market_open": config.get("bot", {}).get("market_open", "09:15"),
        "market_close": config.get("bot", {}).get("market_close", "15:30"),
        "market_hours_check": config.get("bot", {}).get("market_hours_check", False),
        "ml_ready": ml_ready,
        "ml_threshold": ml_threshold,
        "ml_model_exists": MODEL_PATH.exists(),
        "data_source": config.get("data_source", "openalgo").upper(),
        "exchange": config.get("exchange", "NSE"),
        "symbols": config.get("symbols") or [],
        "index_symbols": config.get("index_symbols") or [],
        "htf": config.get("trend_timeframe", "15m"),
        "ltf": config.get("equity_timeframe", "5m"),
        "mtf_enabled": config.get("mtf_filter", {}).get("enabled", False),
        "sr_enabled": config.get("sr_channels", {}).get("enabled", False),
        "auto_scan_enabled": config.get("bot", {}).get("auto_scan_enabled", True),
        "auto_scan_interval_minutes": config.get("bot", {}).get("auto_scan_interval_minutes", 5),
        "uptime_seconds": uptime_seconds,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/config")
def get_config():
    """Return the current config.yml parsed as JSON."""
    try:
        return load_config()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _update_commented_map(cm, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and key in cm and hasattr(cm[key], "items"):
            _update_commented_map(cm[key], value)
        else:
            cm[key] = value


@app.post("/api/config")
def save_config(req: ConfigSaveRequest):
    """Save the config, preserving YAML comments, then restart workers."""
    try:
        config_path = _server_dir / "config.yml"
        ryaml = _RYAML()
        ryaml.preserve_quotes = True
        with open(config_path, "r", encoding="utf-8") as fh:
            commented_map = ryaml.load(fh)
        _update_commented_map(commented_map, req.config)
        with open(config_path, "w", encoding="utf-8") as fh:
            ryaml.dump(commented_map, fh)
        # Restart workers in background
        threading.Thread(target=lambda: _engine.restart(req.config), daemon=True).start()
        return {"status": "success", "message": "Config saved and bot restarting."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/signals")
def get_signals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    symbol: str = Query(None),
    signal_type: str = Query(None),
):
    """Paginated signal history from signals.db."""
    rows, total = _query_signals(limit, offset, symbol, signal_type)
    return {"status": "success", "total": total, "signals": rows}


@app.get("/api/signals/stats")
def get_signal_stats(days: int = Query(30, ge=1, le=365)):
    """Win/loss statistics and signal counts."""
    return {"status": "success", "stats": _query_signal_stats(days)}


@app.post("/api/signals/label")
async def label_signals():
    """Run label_signals.py in a subprocess and return stdout."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, str(_server_dir / "label_signals.py"), "--status"],
                capture_output=True, text=True, cwd=str(_server_dir),
            )
        )
        return {
            "status": "success",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/ml/train")
def trigger_ml_train():
    """Trigger ml_filter.py --train; returns job started confirmation."""
    script = _server_dir / "ml_filter.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail="ml_filter.py not found.")

    def _run():
        try:
            subprocess.run(
                [sys.executable, str(script), "--train"],
                cwd=str(_server_dir),
            )
        except Exception as exc:
            log.error("ML train error: %s", exc)

    threading.Thread(target=_run, daemon=True, name="ML-Train").start()
    return {"status": "success", "message": "ML training started. Check logs for progress."}


@app.get("/api/ml/report")
async def get_ml_report():
    """Run ml_filter.py --report and return the output."""
    script = _server_dir / "ml_filter.py"
    if not script.exists():
        return {"status": "error", "report": "ml_filter.py not found."}
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, str(script), "--report"],
                capture_output=True, text=True, cwd=str(_server_dir),
            )
        )
        return {
            "status": "success",
            "report": result.stdout + result.stderr,
            "model_exists": MODEL_PATH.exists(),
            "model_size_kb": round(MODEL_PATH.stat().st_size / 1024, 1) if MODEL_PATH.exists() else 0,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/htf-trend")
def get_htf_trend():
    """Return the current HTF trend position for each symbol."""
    config = load_config()
    symbols = config.get("symbols") or []
    trends = {}
    for sym in symbols:
        pos = _engine.htf_store.get(sym)
        trends[sym] = {
            "pos": pos,
            "label": "LONG" if pos == 1 else ("SHORT" if pos == -1 else ("FLAT" if pos == 0 else "N/A")),
        }
    return {"status": "success", "trends": trends, "htf": config.get("htf", "15m")}


@app.get("/api/ltp/{symbol}")
async def get_ltp(symbol: str, exchange: str = Query(None)):
    """Fetch live LTP from OpenAlgo for a symbol."""
    config = load_config()
    exch = exchange or config.get("exchange", "NSE")

    # First try ltp_map from WS monitor
    ltp = _engine.ltp_map.get(symbol)
    if ltp is not None:
        return {"symbol": symbol, "exchange": exch, "ltp": round(float(ltp), 2), "source": "websocket"}

    # Fallback: OpenAlgo REST
    try:
        from openalgo import api as oa_api
        oa_cfg = config.get("openalgo", {})
        client = oa_api(api_key=oa_cfg.get("apikey", ""), host=oa_cfg.get("base_url", "http://127.0.0.1:5000"))
        resp = client.quotes(symbol=symbol, exchange=exch)
        if isinstance(resp, dict):
            price = resp.get("ltp") or resp.get("price") or resp.get("last")
            if price is not None:
                return {"symbol": symbol, "exchange": exch, "ltp": round(float(price), 2), "source": "rest"}
    except Exception as exc:
        log.warning("LTP fetch failed for %s: %s", symbol, exc)

    raise HTTPException(status_code=404, detail=f"LTP not available for {symbol}")


@app.get("/api/system")
def get_system():
    """CPU, RAM, disk stats + basic process info."""
    if not _PSUTIL_AVAILABLE:
        return {"status": "unavailable", "reason": "psutil not installed"}
    try:
        proc = psutil.Process(os.getpid())
        return {
            "status": "ok",
            "cpu_pct": psutil.cpu_percent(interval=0.1),
            "ram_pct": psutil.virtual_memory().percent,
            "ram_used_mb": round(psutil.virtual_memory().used / 1024 / 1024, 1),
            "ram_total_mb": round(psutil.virtual_memory().total / 1024 / 1024, 1),
            "disk_pct": psutil.disk_usage("/").percent if sys.platform != "win32" else psutil.disk_usage("C:\\").percent,
            "process_ram_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
            "process_threads": proc.num_threads(),
            "platform": sys.platform,
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.get("/api/logs")
def get_logs(lines: int = Query(150, ge=10, le=1000)):
    """Return the last N lines from utbot.log."""
    log_path = _server_dir / "utbot.log"
    if not log_path.exists():
        return {"logs": ""}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=lines)
        return {"logs": "".join(tail)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/logs/stream")
async def stream_logs():
    """SSE endpoint: streams new log lines in real time."""
    log_path = _server_dir / "utbot.log"

    async def _generator() -> AsyncGenerator[str, None]:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(0, 2)  # seek to end
                while True:
                    line = fh.readline()
                    if line:
                        payload = json.dumps({"line": line.rstrip()})
                        yield f"data: {payload}\n\n"
                    else:
                        await asyncio.sleep(0.5)
        except GeneratorExit:
            pass
        except Exception as exc:
            yield f"data: {json.dumps({'line': f'[SSE error: {exc}]'})}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/signals/clear")
def clear_signals():
    """Truncate the signals table in SQLite signals.db."""
    try:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.execute("DELETE FROM signals")
        conn.commit()
        conn.close()
        return {"status": "success", "message": "All logged signals deleted."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/bot/restart")
def restart_bot(req: BotRestartRequest):
    """Restart all bot workers (hot-reload config too)."""
    try:
        config = load_config()
        threading.Thread(
            target=lambda: _engine.restart(config),
            daemon=True,
            name="BotRestart",
        ).start()
        return {"status": "success", "message": f"Bot restart triggered. Reason: {req.reason}"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ManualOrderRequest(BaseModel):
    symbol: str
    action: str
    quantity: int
    price: float


@app.post("/api/order")
def manual_order(req: ManualOrderRequest):
    """Place a manual order using OpenAlgo API credentials from config."""
    try:
        from openalgo import api as oa_api
        config = load_config()
        oa_cfg = config.get("openalgo", {})
        api_key = oa_cfg.get("apikey", "")
        base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
        
        client = oa_api(api_key=api_key, host=base_url)
        res = client.placeorder(
            symbol=req.symbol,
            action=req.action,
            exchange=config.get("exchange", "NSE"),
            quantity=str(req.quantity),
            product="CNC",
            price_type="LIMIT",
            price=str(req.price)
        )
        return {"status": "success", "orderid": res.get("orderid", "")}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Positions & Trade Management
# ---------------------------------------------------------------------------
# GET  /api/positions            Open positions, enriched with live premium,
#                                 unrealised P&L (%/₹), lots, and expiry countdown
# GET  /api/positions/closed     Closed trade history (paginated)
# GET  /api/positions/{id}/events  Full audit log for one position
# POST /api/positions/{id}/close   Manually square off an open position
# ---------------------------------------------------------------------------

def _enrich_open_position(pos: dict) -> dict:
    """Attach live premium, unrealised P&L, lots, and expiry countdown to an
    open position row for the dashboard. Read-only — never mutates trade_db."""
    enriched = dict(pos)
    ltp = position_monitor.last_ltp.get(pos["symbol"])
    enriched["live_ltp"] = ltp

    if ltp is not None:
        enriched["unrealized_gain_pct"] = round(calc_gain_pct(pos, ltp), 2)
        enriched["unrealized_pnl_amount"] = calc_pnl_amount(pos, ltp)
    else:
        # No tick received yet since server start (e.g. just restarted with
        # positions restored from DB) — dashboard should show "—", not 0%.
        enriched["unrealized_gain_pct"] = None
        enriched["unrealized_pnl_amount"] = None

    enriched["lots"] = calc_lots(pos)

    expiry_val = None
    if pos.get("expiry_date"):
        try:
            from datetime import date as _date
            expiry_val = _date.fromisoformat(pos["expiry_date"])
        except ValueError:
            expiry_val = None
    enriched["expiry_countdown"] = expiry_countdown(expiry_val)

    return enriched


@app.get("/api/positions")
def get_positions():
    """List all currently open, trade-management-monitored positions."""
    try:
        open_pos = trade_db.get_open_positions()
        return {"status": "success", "positions": [_enrich_open_position(p) for p in open_pos]}
    except Exception as exc:
        log.error("Failed to retrieve open positions: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/positions/closed")
def get_closed_positions_endpoint(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Paginated history of closed positions, newest first."""
    try:
        closed = trade_db.get_closed_positions(limit=limit, offset=offset)
        for p in closed:
            p["lots"] = calc_lots(p)
        return {"status": "success", "positions": closed}
    except Exception as exc:
        log.error("Failed to retrieve closed positions: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/positions/{pos_id}/events")
def get_position_events_endpoint(pos_id: int):
    """Full audit trail (opens, SL moves, partial exits, close) for one position."""
    try:
        events = trade_db.get_position_events(pos_id)
        return {"status": "success", "events": events}
    except Exception as exc:
        log.error("Failed to retrieve events for position %d: %s", pos_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/positions/{pos_id}/close")
def manual_close_position(pos_id: int):
    """Manually square off an open, monitored position at the current LTP."""
    with position_monitor.lock:
        target_pos = position_monitor.active_positions.get(pos_id)

    if target_pos is None:
        raise HTTPException(
            status_code=404,
            detail="Position not found in the active monitor (already closed, or "
                   "trade_management wasn't enabled when it was opened).",
        )

    ltp = position_monitor.last_ltp.get(target_pos["symbol"])
    if ltp is None:
        try:
            from trading_adapter import get_ltp as _get_ltp
            ltp = _get_ltp(load_config(), target_pos["symbol"], target_pos["exchange"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not resolve a current price to close at: {exc}")

    try:
        position_monitor._execute_exit(target_pos, ltp, "MANUAL")
        return {"status": "success", "message": f"Manual exit triggered for position {pos_id} @ ~₹{ltp:.2f}."}
    except Exception as exc:
        log.error("Manual close failed for position %d: %s", pos_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Serve static frontend
# ---------------------------------------------------------------------------
_frontend_dir = _server_dir / "frontend"
_frontend_dir.mkdir(exist_ok=True)

app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Direct launch
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Starting UT Bot Antigravity Dashboard on http://127.0.0.1:9000")
    uvicorn.run("server:app", host="127.0.0.1", port=9000, reload=False)
