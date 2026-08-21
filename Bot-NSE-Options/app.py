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
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from ruamel.yaml import YAML as _RYAML

_bot_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_bot_dir))

# ---- [Sprint-5] Bootstrap: logging + secrets BEFORE any downstream imports ---
try:
    from logging_setup import setup_logging
    from secrets_loader import apply_env_overrides
    # Load config early (raw YAML) so setup_logging can honor bot.log_* keys.
    _boot_cfg = {}
    _cfg_path = _bot_dir / "config.yml"
    if _cfg_path.exists():
        with open(_cfg_path, "r", encoding="utf-8") as _fh:
            _boot_cfg = yaml.safe_load(_fh) or {}
        apply_env_overrides(_boot_cfg)
    setup_logging(_boot_cfg)
    # [Sprint-6] Optional JSON log handler (fail-open — no-op if disabled).
    try:
        setup_json_logging(_boot_cfg)
    except Exception:
        pass
except Exception as _boot_exc:  # fail-open — never block startup
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("UTBotSRChannelsScanner").warning(
        "[bootstrap] fallback logging active (%s)", _boot_exc
    )

log = logging.getLogger("UTBotSRChannelsScanner")

from scanner import load_config, run_scan, fetch_history, fetch_indices_quotes
from options_grid import generate_option_strike_grid, parse_base_option_symbol
from signal_db import get_signal_history, get_statistics
from trading_adapter import place_order as adapter_place_order, get_ltp as adapter_get_ltp
from trade_manager import PositionMonitor
import trade_db
import risk_manager
# [Sprint-5] Production-hardening helpers
import health_check
import db_maintenance
from rate_limiter import build_rate_limit_middleware
# [Sprint-6] Observability + resilience
import metrics as _metrics_module
import broker_watchdog
from log_json import setup_json_logging

app = FastAPI(title="Bot-NSE-Options Dashboard API", version="1.6.0")

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
            interval_mins = float(cfg.get("bot", {}).get("auto_scan_interval_minutes", 1))
            running_bar_mode = bool(cfg.get("strategy", {}).get("signal_on_running_bar", True))

            # In running bar mode, scan every 15 seconds for real-time intraday crossovers;
            # In completed bar mode, scan at interval boundary (e.g. 60s).
            if running_bar_mode and interval_mins >= 1:
                interval_secs = 15
            else:
                interval_secs = max(10, int(interval_mins * 60))

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
        # [Sprint-5] Record start time for /api/health uptime + run boot self-check
        health_check.mark_start()
        _startup_self_check(cfg)

        _monitor.start(cfg)
        log.info("Bot-NSE-Options API server running on http://127.0.0.1:%d", cfg.get("bot", {}).get("port", 9000))
        sys.stdout.flush()

        if cfg.get("bot", {}).get("auto_scan_enabled", True):
            _auto_scan_running = True
            _auto_scan_thread = threading.Thread(target=_background_scanner_loop, daemon=True)
            _auto_scan_thread.start()

        # [Sprint-6] Kick off the broker watchdog (idempotent, cfg-gated).
        try:
            broker_watchdog.start(cfg)
        except Exception as _wd_exc:
            log.warning("[startup] watchdog failed to start: %s", _wd_exc)

    except Exception as e:
        log.error("Failed startup: %s", e)
        sys.stdout.flush()


def _startup_self_check(cfg: dict) -> None:
    """[Sprint-5] Log a boot summary + warn on missing critical config keys."""
    try:
        oa = cfg.get("openalgo", {}) or {}
        risk = cfg.get("risk", {}) or {}
        bot_cfg = cfg.get("bot", {}) or {}

        warnings = []
        if not oa.get("apikey"):
            warnings.append("openalgo.apikey missing (broker calls will fail)")
        if not oa.get("base_url"):
            warnings.append("openalgo.base_url missing")
        if bot_cfg.get("auto_scan_enabled", True) and not oa.get("apikey"):
            warnings.append("auto-scan is enabled but no API key is configured")

        log.info("[startup] version=%s port=%s auto_scan=%s kill_switch=%s log_level=%s",
                 app.version,
                 bot_cfg.get("port", 9000),
                 bot_cfg.get("auto_scan_enabled", True),
                 risk.get("kill_switch", False),
                 bot_cfg.get("log_level", "INFO"))

        try:
            from secrets_loader import summarize_secret_sources
            sources = summarize_secret_sources(cfg)
            log.info("[startup] secret sources: %s", sources)
        except Exception:
            pass

        # Alpha layers snapshot
        ae = cfg.get("alpha_enhancers", {}) or {}
        log.info("[startup] alpha_enhancers=%s vix=%s session=%s poc=%s greeks=%s strict_mtf=%s",
                 ae.get("enabled", True),
                 (ae.get("vix_regime", {}) or {}).get("enabled", True),
                 (ae.get("session_weighting", {}) or {}).get("enabled", True),
                 (ae.get("volume_profile", {}) or {}).get("enabled", True),
                 (ae.get("greeks", {}) or {}).get("enabled", True),
                 (ae.get("strict_mtf", {}) or {}).get("enabled", False))

        # [Sprint-6] Ops snapshot: watchdog + JSON logs + metrics.
        wd = bot_cfg.get("watchdog", {}) or {}
        log.info("[startup] watchdog=%s interval=%ss threshold=%s json_logs=%s",
                 wd.get("enabled", True),
                 wd.get("interval_sec", 30),
                 wd.get("failure_threshold", 3),
                 bot_cfg.get("log_json", False))

        for w in warnings:
            log.warning("[startup] %s", w)
    except Exception as exc:
        log.debug("[startup] self-check failed: %s", exc)


@app.on_event("shutdown")
async def shutdown_event():
    global _auto_scan_running
    _auto_scan_running = False
    _monitor.stop()
    # [Sprint-6] Stop watchdog (non-blocking).
    try:
        broker_watchdog.stop()
    except Exception:
        pass


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [Sprint-5] Optional per-IP rate limiter — attach only if enabled in config.
try:
    _rl_mw = build_rate_limit_middleware(_boot_cfg)
    if _rl_mw is not None:
        app.middleware("http")(_rl_mw)
except Exception as _rl_exc:
    log.warning("[bootstrap] rate-limit middleware failed to attach: %s", _rl_exc)

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
    # ---- [Sprint-1] New guardrail toggles ---------------------------------
    dedup_enabled: bool = True                 # Duplicate-Entry Guard
    directional_gate_enabled: bool = True      # Spot-trend directional filter
    market_hours_enabled: bool = True          # Market-hours enforcement
    daily_loss_limit_enabled: bool = True      # Daily-loss circuit breaker
    # ---- [Sprint-2] Signal-quality toggles --------------------------------
    atr_filter_enabled: bool = True            # ATR% volatility floor/ceiling
    adx_filter_enabled: bool = True            # ADX trend-strength filter
    spread_filter_enabled: bool = True         # Bid-ask spread / OI filter
    consecutive_loss_breaker_enabled: bool = True  # Consecutive-loss circuit breaker
    # ---- [Sprint-3] Position-sizing toggles -------------------------------
    position_sizing_enabled: bool = True        # Master toggle for dynamic sizing
    grade_multiplier_enabled: bool = False      # Scale risk% by signal grade
    # ---- [Sprint-4] Alpha-enhancer toggles --------------------------------
    alpha_enhancers_enabled: bool = True        # Master toggle for entire alpha layer
    vix_regime_enabled: bool = True             # VIX-regime adaptive risk
    session_weighting_enabled: bool = True      # Session-of-day signal weighting
    volume_profile_enabled: bool = True         # POC distance filter
    greeks_filter_enabled: bool = True          # Options greeks (delta/theta) filter
    strict_mtf_enabled: bool = False            # Hard multi-timeframe alignment


class KillSwitchRequest(BaseModel):
    enabled: bool


class RiskSettingsRequest(BaseModel):
    account_equity: float = 100000.0
    daily_loss_max_pct: float = 3.0
    daily_loss_auto_square_off: bool = True
    min_grade: str = "B"
    min_score: float = 60.0
    dedup_cooldown_minutes: int = 5
    entry_cutoff_time: str = "14:45"
    market_open: str = "09:15"
    market_close: str = "15:30"
    # ---- [Sprint-2] Consecutive-loss breaker settings ---------------------
    consecutive_loss_max: int = 3
    consecutive_loss_cooldown_min: int = 30


class SignalQualitySettingsRequest(BaseModel):
    """[Sprint-2] Persist signal-quality thresholds from dashboard Settings tab."""
    scoring_enabled: bool = True
    atr_pct_min: float = 0.5
    atr_pct_max: float = 8.0
    adx_min: float = 20.0
    max_spread_pct: float = 1.5
    min_open_interest: int = 500


class PositionSizingSettingsRequest(BaseModel):
    """[Sprint-3] Persist position-sizing settings from dashboard."""
    mode: str = "fixed_fractional"          # 'fixed_fractional' | 'kelly'
    risk_per_trade_pct: float = 1.0
    max_risk_per_trade_pct: float = 3.0
    max_portfolio_exposure_pct: float = 15.0
    max_concurrent_positions: int = 3
    kelly_fraction: float = 0.25
    kelly_min_trades: int = 20


class AlphaEnhancersSettingsRequest(BaseModel):
    """[Sprint-4] Persist alpha-enhancer thresholds from dashboard."""
    vix_low_threshold: float = 15.0
    vix_high_threshold: float = 22.0
    vix_low_multiplier: float = 1.10
    vix_normal_multiplier: float = 1.00
    vix_high_multiplier: float = 0.60
    session_opening_minutes: int = 30
    session_closing_minutes: int = 30
    session_opening_bonus: float = -5.0
    session_prime_bonus: float = 5.0
    session_closing_bonus: float = -10.0
    poc_max_distance_pct: float = 1.5
    greeks_min_abs_delta: float = 0.20
    greeks_max_theta_pct: float = 5.0
    strict_mtf_timeframes: str = "5m,15m"    # comma-separated in payload for simplicity


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return cfg


def _save_commented_config(updater_fn):
    """Utility to load config.yml preserving comments via ruamel.yaml, update values, and save back."""
    config_path = _bot_dir / "config.yml"
    ryaml = _RYAML()
    ryaml.preserve_quotes = True
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as fh:
            commented_cfg = ryaml.load(fh)
    else:
        commented_cfg = {}
    updater_fn(commented_cfg)
    with open(config_path, "w", encoding="utf-8") as fh:
        ryaml.dump(commented_cfg, fh)
    return commented_cfg


@app.post("/api/config")
async def update_config(cfg_data: dict):
    try:
        def update_dict(target, source):
            for k, v in source.items():
                if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                    update_dict(target[k], v)
                else:
                    target[k] = v

        _save_commented_config(lambda cfg: update_dict(cfg, cfg_data))
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
    def update_grid(cfg):
        cfg.setdefault("options", {})
        cfg["options"]["base_atm_strike"] = req.base_atm_strike
        cfg["options"]["underlying"] = req.underlying
        cfg["options"]["expiry_date"] = req.expiry_date
        cfg["options"]["levels_up_down"] = req.levels_up_down
        cfg["options"]["strike_gap"] = req.strike_gap

    _save_commented_config(update_grid)

    grid = generate_option_strike_grid(
        base_symbol_or_params=req.base_atm_strike,
        levels_up_down=req.levels_up_down,
        configured_gap=req.strike_gap,
    )
    return {"status": "success", "grid": grid}


@app.post("/api/filters")
async def toggle_filters(req: FilterToggleRequest):
    def update_filters(cfg):
        cfg.setdefault("strategy", {})["ut_enabled"] = req.ut_enabled
        cfg.setdefault("sr_channels", {})["enabled"] = req.sr_enabled
        cfg.setdefault("filters", {})["ema_trend_filter"] = req.ema_enabled
        cfg.setdefault("filters", {})["volume_filter"] = req.volume_enabled
        cfg.setdefault("filters", {})["mtf_confirmation"] = req.mtf_enabled
        cfg.setdefault("filters", {})["squeeze_filter"] = req.squeeze_enabled
        # [Sprint-1] Guardrail toggles
        cfg.setdefault("trading", {}).setdefault("dedup", {})["enabled"] = req.dedup_enabled
        cfg.setdefault("trading", {}).setdefault("directional_gate", {})["enabled"] = req.directional_gate_enabled
        cfg.setdefault("bot", {})["market_hours_check"] = req.market_hours_enabled
        cfg.setdefault("risk", {}).setdefault("daily_loss_limit", {})["enabled"] = req.daily_loss_limit_enabled
        # [Sprint-2] Signal-quality + circuit-breaker toggles
        cfg.setdefault("signal_quality", {})["atr_filter_enabled"] = req.atr_filter_enabled
        cfg["signal_quality"]["adx_filter_enabled"] = req.adx_filter_enabled
        cfg["signal_quality"]["spread_filter_enabled"] = req.spread_filter_enabled
        cfg.setdefault("risk", {}).setdefault("consecutive_loss_breaker", {})["enabled"] = req.consecutive_loss_breaker_enabled
        # [Sprint-3] Position-sizing toggles
        cfg.setdefault("position_sizing", {})["enabled"] = req.position_sizing_enabled
        cfg["position_sizing"]["grade_multiplier_enabled"] = req.grade_multiplier_enabled
        # [Sprint-4] Alpha-enhancer toggles
        cfg.setdefault("alpha_enhancers", {})["enabled"] = req.alpha_enhancers_enabled
        cfg["alpha_enhancers"].setdefault("vix_regime", {})["enabled"] = req.vix_regime_enabled
        cfg["alpha_enhancers"].setdefault("session_weighting", {})["enabled"] = req.session_weighting_enabled
        cfg["alpha_enhancers"].setdefault("volume_profile", {})["enabled"] = req.volume_profile_enabled
        cfg["alpha_enhancers"].setdefault("greeks", {})["enabled"] = req.greeks_filter_enabled
        cfg["alpha_enhancers"].setdefault("strict_mtf", {})["enabled"] = req.strict_mtf_enabled

    _save_commented_config(update_filters)
    return {"status": "success", "message": "Quick filters updated"}


# ---------------------------------------------------------------------------
# [Sprint-1] Risk Management & Kill Switch endpoints
# ---------------------------------------------------------------------------

@app.get("/api/risk/status")
async def risk_status():
    """Return current risk snapshot for the dashboard status strip."""
    cfg = load_config()
    return risk_manager.get_status(cfg)


@app.post("/api/kill-switch")
async def toggle_kill_switch(req: KillSwitchRequest):
    """Toggle the global kill switch. When enabled, blocks all new auto-orders."""
    def update_ks(cfg):
        cfg.setdefault("risk", {})["kill_switch"] = bool(req.enabled)
    _save_commented_config(update_ks)
    state = "ENABLED (all new orders blocked)" if req.enabled else "DISABLED (trading resumed)"
    log.warning("🛑 [Sprint-1] Kill Switch %s via dashboard", state)
    return {"status": "success", "kill_switch": req.enabled, "message": f"Kill switch {state}"}


@app.post("/api/risk/settings")
async def update_risk_settings(req: RiskSettingsRequest):
    """Persist Risk Management settings from the dashboard Settings tab."""
    def update_risk(cfg):
        cfg.setdefault("risk", {})
        cfg["risk"]["account_equity"] = float(req.account_equity)
        cfg["risk"].setdefault("daily_loss_limit", {})
        cfg["risk"]["daily_loss_limit"]["max_loss_pct"] = float(req.daily_loss_max_pct)
        cfg["risk"]["daily_loss_limit"]["auto_square_off"] = bool(req.daily_loss_auto_square_off)
        cfg.setdefault("trading", {})
        cfg["trading"]["min_grade"] = str(req.min_grade).upper()
        cfg["trading"]["min_score"] = float(req.min_score)
        cfg["trading"].setdefault("dedup", {})["cooldown_minutes"] = int(req.dedup_cooldown_minutes)
        cfg.setdefault("bot", {})
        cfg["bot"]["entry_cutoff_time"] = str(req.entry_cutoff_time)
        cfg["bot"]["market_open"] = str(req.market_open)
        cfg["bot"]["market_close"] = str(req.market_close)
        # [Sprint-2] Consecutive-loss breaker settings
        cfg["risk"].setdefault("consecutive_loss_breaker", {})
        cfg["risk"]["consecutive_loss_breaker"]["max_losses"] = int(req.consecutive_loss_max)
        cfg["risk"]["consecutive_loss_breaker"]["cooldown_minutes"] = int(req.consecutive_loss_cooldown_min)
    _save_commented_config(update_risk)
    return {"status": "success", "message": "Risk settings saved"}


@app.post("/api/signal-quality/settings")
async def update_signal_quality_settings(req: SignalQualitySettingsRequest):
    """[Sprint-2] Persist signal-quality thresholds from dashboard Settings tab."""
    def update_sq(cfg):
        cfg.setdefault("signal_quality", {})
        cfg["signal_quality"]["scoring_enabled"] = bool(req.scoring_enabled)
        cfg["signal_quality"]["atr_pct_min"] = float(req.atr_pct_min)
        cfg["signal_quality"]["atr_pct_max"] = float(req.atr_pct_max)
        cfg["signal_quality"]["adx_min"] = float(req.adx_min)
        cfg["signal_quality"]["max_spread_pct"] = float(req.max_spread_pct)
        cfg["signal_quality"]["min_open_interest"] = int(req.min_open_interest)
    _save_commented_config(update_sq)
    return {"status": "success", "message": "Signal-quality settings saved"}


@app.post("/api/position-sizing/settings")
async def update_position_sizing_settings(req: PositionSizingSettingsRequest):
    """[Sprint-3] Persist position-sizing settings from dashboard Settings tab."""
    def update_ps(cfg):
        cfg.setdefault("position_sizing", {})
        cfg["position_sizing"]["mode"] = str(req.mode).lower()
        cfg["position_sizing"]["risk_per_trade_pct"] = float(req.risk_per_trade_pct)
        cfg["position_sizing"]["max_risk_per_trade_pct"] = float(req.max_risk_per_trade_pct)
        cfg["position_sizing"]["max_portfolio_exposure_pct"] = float(req.max_portfolio_exposure_pct)
        cfg["position_sizing"]["max_concurrent_positions"] = int(req.max_concurrent_positions)
        cfg["position_sizing"]["kelly_fraction"] = float(req.kelly_fraction)
        cfg["position_sizing"]["kelly_min_trades"] = int(req.kelly_min_trades)
    _save_commented_config(update_ps)
    return {"status": "success", "message": "Position-sizing settings saved"}


@app.post("/api/alpha/settings")
async def update_alpha_settings(req: AlphaEnhancersSettingsRequest):
    """[Sprint-4] Persist alpha-enhancer thresholds from dashboard Settings tab."""
    def update_ae(cfg):
        ae = cfg.setdefault("alpha_enhancers", {})
        vr = ae.setdefault("vix_regime", {})
        vr["low_threshold"] = float(req.vix_low_threshold)
        vr["high_threshold"] = float(req.vix_high_threshold)
        vr.setdefault("risk_multipliers", {})
        vr["risk_multipliers"]["LOW"] = float(req.vix_low_multiplier)
        vr["risk_multipliers"]["NORMAL"] = float(req.vix_normal_multiplier)
        vr["risk_multipliers"]["HIGH"] = float(req.vix_high_multiplier)

        sw = ae.setdefault("session_weighting", {})
        sw["opening_minutes"] = int(req.session_opening_minutes)
        sw["closing_minutes"] = int(req.session_closing_minutes)
        sw.setdefault("bonuses", {})
        sw["bonuses"]["opening"] = float(req.session_opening_bonus)
        sw["bonuses"]["prime"] = float(req.session_prime_bonus)
        sw["bonuses"]["closing"] = float(req.session_closing_bonus)

        vp = ae.setdefault("volume_profile", {})
        vp["max_poc_distance_pct"] = float(req.poc_max_distance_pct)

        gk = ae.setdefault("greeks", {})
        gk["min_abs_delta"] = float(req.greeks_min_abs_delta)
        gk["max_theta_pct"] = float(req.greeks_max_theta_pct)

        sm = ae.setdefault("strict_mtf", {})
        tfs = [t.strip() for t in str(req.strict_mtf_timeframes).split(",") if t.strip()]
        if tfs:
            sm["required_timeframes"] = tfs
    _save_commented_config(update_ae)
    return {"status": "success", "message": "Alpha-enhancer settings saved"}


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
    try:
        risk_manager.record_exit(pos["symbol"])
    except Exception:
        pass
    return {"status": "success", "message": f"Closed trade {trade_id} for {pos['symbol']}"}


@app.post("/api/positions/close-all")
async def close_all_positions_endpoint():
    cfg = load_config()
    trades = trade_db.get_active_trades()
    if not trades:
        return {"status": "success", "message": "No active positions to close.", "closed_count": 0}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _close_opt_trade(pos):
        trade_id = pos["trade_id"]
        ltp = adapter_get_ltp(cfg, pos["symbol"], pos["exchange"])
        trade_db.close_trade(trade_id, exit_price=ltp if ltp > 0 else pos["entry_price"], exit_reason="MANUAL_CLOSE_ALL")
        try:
            risk_manager.record_exit(pos["symbol"])
        except Exception:
            pass
        return pos.get("symbol")

    closed_count = 0
    errors = []

    def run_all_opt_exits():
        nonlocal closed_count
        with ThreadPoolExecutor(max_workers=min(10, len(trades))) as pool:
            futures = {pool.submit(_close_opt_trade, pos): pos for pos in trades}
            for f in as_completed(futures):
                pos = futures[f]
                try:
                    f.result()
                    closed_count += 1
                except Exception as e:
                    errors.append(f"{pos.get('symbol')}: {e}")

    await run_in_threadpool(run_all_opt_exits)

    return {
        "status": "success",
        "message": f"Closed {closed_count} position(s)." + (f" Errors: {', '.join(errors)}" if errors else ""),
        "closed_count": closed_count,
    }


@app.post("/api/reset-data")
async def reset_data_endpoint():
    try:
        await run_in_threadpool(trade_db.clear_all_trades)
        return {"status": "success", "message": "All options trades and signal history cleared successfully. Starting fresh!"}
    except Exception as e:
        log.error("Failed to reset options database: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to reset database: {e}")


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


# ---------------------------------------------------------------------------
# [Sprint-5] Production-Hardening Endpoints
# ---------------------------------------------------------------------------

class ReconcileRequest(BaseModel):
    """[Sprint-5] Payload for /api/admin/reconcile."""
    cutoff_hours: int = 24
    dry_run: bool = False


@app.get("/api/health")
async def api_health():
    """
    [Sprint-5] Composite live-health snapshot for the dashboard.
    Never raises — returns status = ok | degraded | down.
    """
    try:
        cfg = load_config()
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"status": "down", "error": f"config_load_failed: {e}"},
        )
    try:
        report = await run_in_threadpool(health_check.build_health_report, cfg, _bot_dir)
    except Exception as e:
        report = {"status": "down", "error": f"health_check_failed: {e}"}
    return report


@app.get("/api/admin/system")
async def api_admin_system():
    """
    [Sprint-5] Lightweight system stats for the Settings-tab System card.
    Excludes broker call so it stays fast.
    """
    try:
        cfg = load_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"config_load_failed: {e}")

    try:
        from logging_setup import get_log_file_path
        log_path = get_log_file_path(cfg)
        log_size = log_path.stat().st_size if log_path.exists() else 0
    except Exception:
        log_path, log_size = None, 0

    stale_cutoff = int((cfg.get("bot", {}) or {}).get("stale_position_cutoff_hours", 24))

    try:
        db = db_maintenance.db_health()
        db["stale_positions"] = db_maintenance.count_stale_positions(stale_cutoff)
    except Exception as e:
        db = {"reachable": False, "error": str(e), "open_positions": 0, "stale_positions": 0}

    return {
        "status": "success",
        "uptime_seconds": int(time.time() - health_check._start_ts),
        "log_file": str(log_path) if log_path else "",
        "log_size_bytes": int(log_size),
        "database": db,
        "stale_cutoff_hours": stale_cutoff,
    }


@app.post("/api/admin/reconcile")
async def api_admin_reconcile(req: ReconcileRequest):
    """
    [Sprint-5] Close OPEN trades older than `cutoff_hours` (zero PnL,
    exit_reason='STALE_RECONCILE'). Use `dry_run=true` to preview only.
    """
    try:
        result = await run_in_threadpool(
            db_maintenance.reconcile_stale_positions,
            int(req.cutoff_hours),
            bool(req.dry_run),
        )
        return result
    except Exception as e:
        log.error("[admin] reconcile failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# [Sprint-6] Observability endpoints
# ---------------------------------------------------------------------------

@app.get("/api/metrics")
async def api_metrics():
    """
    [Sprint-6] Prometheus text exposition of all in-process metrics.
    Return as text/plain so `prometheus_client`-compatible scrapers accept it.
    """
    try:
        body = _metrics_module.render_prometheus()
    except Exception as exc:
        log.error("[metrics] render failed: %s", exc)
        body = f"# render_error {exc}\n"
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/metrics/snapshot")
async def api_metrics_snapshot():
    """
    [Sprint-6] JSON snapshot of all metrics + the broker watchdog state.
    Powers the 'Live Metrics' dashboard card.
    """
    try:
        snap = _metrics_module.snapshot()
    except Exception as exc:
        snap = {"_error": str(exc)}
    try:
        wd = broker_watchdog.get_state()
    except Exception as exc:
        wd = {"state": "unavailable", "error": str(exc)}
    return {"metrics": snap, "watchdog": wd}


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=9000, reload=True)
