"""
===============================================================================
  Bot-NSE-Options — FastAPI Web Application & REST API
===============================================================================
Serves the dark-mode interactive web dashboard on Port 9000.
Mirrors Bot-Stocks dashboard features, index live cards, auto-refresh, quick filter controls, and trade management.
"""

import sys
import time
import asyncio
import threading
import logging
from pathlib import Path
from datetime import datetime

import yaml
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from ruamel.yaml import YAML as _RYAML

_bot_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")

from scanner import load_config, run_scan, fetch_history, fetch_indices_quotes
from options_grid import generate_option_strike_grid, parse_base_option_symbol
from signal_db import get_signal_history, get_statistics
from trading_adapter import place_order as adapter_place_order, get_ltp as adapter_get_ltp
from trade_manager import PositionMonitor
import trade_db

app = FastAPI(title="Bot-NSE-Options Dashboard API", version="1.0.0")

_monitor = PositionMonitor()
_auto_scan_running = False
_auto_scan_thread = None


def _background_scanner_loop():
    global _auto_scan_running
    log.info("[AutoScanner] Background options auto-scanner worker started.")
    sys.stdout.flush()
    while _auto_scan_running:
        try:
            cfg = load_config()
            interval_mins = int(cfg.get("bot", {}).get("auto_scan_interval_minutes", 1))
            interval_secs = max(10, interval_mins * 60)

            run_scan(cfg)
            sys.stdout.flush()

            for _ in range(interval_secs):
                if not _auto_scan_running:
                    break
                time.sleep(1)
        except Exception as e:
            log.error("[AutoScanner] Exception in background scan loop: %s", e)
            sys.stdout.flush()
            time.sleep(5)


@app.on_event("startup")
async def startup_event():
    global _auto_scan_running, _auto_scan_thread
    try:
        cfg = load_config()
        _monitor.start(cfg)
        log.info("Bot-NSE-Options API server running on http://127.0.0.1:%d", cfg.get("bot", {}).get("port", 9000))
        sys.stdout.flush()

        if cfg.get("bot", {}).get("auto_scan_enabled", True):
            _auto_scan_running = True
            _auto_scan_thread = threading.Thread(target=_background_scanner_loop, daemon=True)
            _auto_scan_thread.start()

    except Exception as e:
        log.error("Failed startup: %s", e)
        sys.stdout.flush()


@app.on_event("shutdown")
async def shutdown_event():
    global _auto_scan_running
    _auto_scan_running = False
    _monitor.stop()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = _bot_dir / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def root():
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Bot-NSE-Options Web API running"}


# ---------------------------------------------------------------------------
# API Models
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    symbol: str
    exchange: str = "NFO"
    action: str = "BUY"
    quantity: int = 65
    product: str = "NRML"
    price_type: str = "MARKET"
    price: float = 0.0
    trigger_price: float = 0.0
    strategy: str = "UTBot_Options"


class GridUpdateRequest(BaseModel):
    base_atm_strike: str = ""         # Plain ATM strike number e.g. '24300', or blank for auto-ATM
    underlying: str = "NIFTY"
    expiry_date: str = "18AUG26"
    levels_up_down: int = 3
    strike_gap: float = 50.0


class FilterToggleRequest(BaseModel):
    ut_enabled: bool = True
    sr_enabled: bool = True
    ema_enabled: bool = False
    volume_enabled: bool = False
    mtf_enabled: bool = False
    squeeze_enabled: bool = False


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return cfg


@app.post("/api/config")
async def update_config(cfg_data: dict):
    config_path = _bot_dir / "config.yml"
    try:
        ryaml = _RYAML()
        ryaml.preserve_quotes = True
        with open(config_path, "w", encoding="utf-8") as fh:
            ryaml.dump(cfg_data, fh)
        return {"status": "success", "message": "Configuration updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/indices")
async def get_indices():
    cfg = load_config()
    return fetch_indices_quotes(cfg)


@app.get("/api/options/grid")
async def get_options_grid():
    cfg = load_config()
    opt_cfg = cfg.get("options", {})
    grid = generate_option_strike_grid(
        base_symbol_or_params=opt_cfg.get("base_atm_strike", ""),
        levels_up_down=int(opt_cfg.get("levels_up_down", 3)),
        configured_gap=opt_cfg.get("strike_gap", 50),
        config=cfg,
    )
    return grid


@app.post("/api/options/grid")
async def update_options_grid(req: GridUpdateRequest):
    cfg = load_config()
    cfg.setdefault("options", {})
    cfg["options"]["base_atm_strike"] = req.base_atm_strike
    cfg["options"]["underlying"] = req.underlying
    cfg["options"]["expiry_date"] = req.expiry_date
    cfg["options"]["levels_up_down"] = req.levels_up_down
    cfg["options"]["strike_gap"] = req.strike_gap

    ryaml = _RYAML()
    with open(_bot_dir / "config.yml", "w", encoding="utf-8") as fh:
        ryaml.dump(cfg, fh)

    grid = generate_option_strike_grid(
        base_symbol_or_params=req.base_atm_strike,
        levels_up_down=req.levels_up_down,
        configured_gap=req.strike_gap,
    )
    return {"status": "success", "grid": grid}


@app.post("/api/filters")
async def toggle_filters(req: FilterToggleRequest):
    cfg = load_config()
    cfg.setdefault("strategy", {})["ut_enabled"] = req.ut_enabled
    cfg.setdefault("sr_channels", {})["enabled"] = req.sr_enabled
    cfg.setdefault("filters", {})["ema_trend_filter"] = req.ema_enabled
    cfg["filters"]["volume_filter"] = req.volume_enabled
    cfg["filters"]["mtf_confirmation"] = req.mtf_enabled
    cfg["filters"]["squeeze_filter"] = req.squeeze_enabled

    ryaml = _RYAML()
    with open(_bot_dir / "config.yml", "w", encoding="utf-8") as fh:
        ryaml.dump(cfg, fh)

    return {"status": "success", "message": "Quick filters updated"}


@app.get("/api/signals")
async def get_signals():
    results = run_scan()
    return results


@app.post("/api/scan")
async def trigger_scan():
    results = run_scan()
    return {"status": "success", "results": results}


@app.get("/api/positions")
async def get_positions():
    active = trade_db.get_active_trades()
    history = trade_db.get_all_trades(limit=50)
    return {"active": active, "history": history}


@app.post("/api/positions/{trade_id}/close")
async def close_position(trade_id: int):
    cfg = load_config()
    trades = trade_db.get_active_trades()
    matching = [t for t in trades if t["trade_id"] == trade_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Active position not found")

    pos = matching[0]
    ltp = adapter_get_ltp(cfg, pos["symbol"], pos["exchange"])
    trade_db.close_trade(trade_id, exit_price=ltp if ltp > 0 else pos["entry_price"], exit_reason="MANUAL_WEB_UI")
    return {"status": "success", "message": f"Closed trade {trade_id} for {pos['symbol']}"}


@app.post("/api/order")
async def place_order_endpoint(req: OrderRequest):
    cfg = load_config()
    res = adapter_place_order(cfg, req.dict())

    ltp = adapter_get_ltp(cfg, req.symbol, req.exchange)
    entry = ltp if ltp > 0 else (req.price if req.price > 0 else 100.0)

    trade_id = trade_db.add_trade({
        "order_id": res.get("order_id") or f"ORD_{int(datetime.now().timestamp()*1000)}",
        "symbol": req.symbol,
        "exchange": req.exchange,
        "action": req.action,
        "quantity": req.quantity,
        "entry_price": entry,
        "product": req.product,
        "stop_loss": round(entry * 0.8, 2),
        "target": round(entry * 1.4, 2),
    })

    return {"status": "success", "order_response": res, "trade_id": trade_id}


@app.get("/api/history")
async def get_history_signals(limit: int = Query(100)):
    return get_signal_history(limit=limit)


@app.get("/api/stats")
async def get_stats():
    return get_statistics()


@app.get("/api/logs")
async def get_logs(lines: int = Query(100)):
    log_file = _bot_dir / "scanner.log"
    if not log_file.exists():
        return {"logs": ["Log file initialized."]}

    with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()
        return {"logs": [line.strip() for line in all_lines[-lines:]]}


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=9000, reload=True)
