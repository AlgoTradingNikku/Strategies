"""
===============================================================================
  health_check.py — /api/health composite check (Sprint 5)
===============================================================================
Aggregates a live snapshot of critical subsystem health for the dashboard.

Sections:
    * app       : uptime, version
    * config    : path, secret-source summary, missing-key warnings
    * broker    : reachable (ping via a lightweight OpenAlgo call)
    * database  : reachable + open-position count + stale count
    * disk      : free bytes on the bot dir's drive
    * logging   : log-file path + current size

Overall status is one of:
    ok        -> all critical checks passing
    degraded  -> non-critical (disk, stale, telegram missing) issues
    down      -> broker OR database unreachable
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("UTBotSRChannelsScanner")

# Populated by health_check.mark_start() at app startup so we can compute uptime.
_start_ts: float = time.time()

_APP_VERSION = "1.6.0-sprint6"


def mark_start(ts: float | None = None) -> None:
    """Record the app start-time. Idempotent; last call wins."""
    global _start_ts
    _start_ts = float(ts) if ts is not None else time.time()


def _fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def _check_broker(cfg: dict) -> Dict[str, Any]:
    """Ping the broker by attempting an LTP lookup. Never raises."""
    out: Dict[str, Any] = {"reachable": False, "detail": "", "latency_ms": None}
    oa = (cfg or {}).get("openalgo", {}) or {}
    if not oa.get("apikey"):
        out["detail"] = "no_apikey"
        return out
    try:
        # Local import so this module works even if trading_adapter is broken.
        import trading_adapter
        underlying = (cfg.get("options", {}) or {}).get("underlying", "NIFTY")
        exchange = (cfg.get("options", {}) or {}).get("index_exchange", "NSE_INDEX")
        t0 = time.time()
        ltp = trading_adapter.get_ltp(cfg, underlying, exchange)
        elapsed_ms = int((time.time() - t0) * 1000)
        out["latency_ms"] = elapsed_ms
        if ltp and float(ltp) > 0:
            out["reachable"] = True
            out["detail"] = f"ltp={ltp}"
        else:
            out["detail"] = "ltp_zero_or_missing"
    except Exception as exc:
        out["detail"] = f"error: {exc.__class__.__name__}"
    return out


def _check_disk(bot_dir: Path) -> Dict[str, Any]:
    try:
        total, used, free = shutil.disk_usage(str(bot_dir))
        return {
            "reachable": True,
            "free_bytes": int(free),
            "free_gb": round(free / (1024 ** 3), 2),
            "used_pct": round(100.0 * used / total, 1) if total else 0.0,
        }
    except Exception as exc:
        return {"reachable": False, "detail": str(exc)}


def _check_log_file(cfg: dict) -> Dict[str, Any]:
    try:
        # Local import to avoid a circular reference at module load time.
        from logging_setup import get_log_file_path
        p = get_log_file_path(cfg)
        exists = p.exists()
        size = p.stat().st_size if exists else 0
        return {
            "path": str(p),
            "exists": exists,
            "size_bytes": int(size),
            "size_mb": round(size / (1024 ** 2), 2),
        }
    except Exception as exc:
        return {"path": "", "exists": False, "size_bytes": 0, "error": str(exc)}


def _check_config(cfg: dict) -> Dict[str, Any]:
    """Validate a handful of critical config keys."""
    warnings: list[str] = []
    oa = (cfg or {}).get("openalgo", {}) or {}
    if not oa.get("apikey"):
        warnings.append("openalgo.apikey missing")
    if not oa.get("base_url"):
        warnings.append("openalgo.base_url missing")

    from secrets_loader import summarize_secret_sources
    return {
        "warnings": warnings,
        "secret_sources": summarize_secret_sources(cfg),
    }


def build_health_report(cfg: dict, bot_dir: Path) -> Dict[str, Any]:
    """
    Compose the full /api/health payload. Never raises — always returns a dict.
    """
    report: Dict[str, Any] = {
        "status": "ok",
        "version": _APP_VERSION,
        "uptime_seconds": int(time.time() - _start_ts),
        "uptime_human": _fmt_uptime(time.time() - _start_ts),
        "checks": {},
    }

    try:
        report["checks"]["broker"] = _check_broker(cfg)
    except Exception as exc:
        report["checks"]["broker"] = {"reachable": False, "detail": f"internal: {exc}"}

    try:
        from db_maintenance import db_health, count_stale_positions
        stale_cutoff = int((cfg.get("bot", {}) or {}).get("stale_position_cutoff_hours", 24))
        db = db_health()
        db["stale_positions"] = count_stale_positions(stale_cutoff)
        db["stale_cutoff_hours"] = stale_cutoff
        report["checks"]["database"] = db
    except Exception as exc:
        report["checks"]["database"] = {"reachable": False, "error": str(exc)}

    report["checks"]["disk"] = _check_disk(bot_dir)
    report["checks"]["logging"] = _check_log_file(cfg)
    report["checks"]["config"] = _check_config(cfg)

    # [Sprint-6] Watchdog state (fail-open — absent when watchdog isn't wired).
    try:
        import broker_watchdog
        wd = broker_watchdog.get_state()
        report["checks"]["watchdog"] = wd
        # If watchdog says the broker is down but the direct probe just succeeded,
        # trust the direct probe (which is what the "broker" section captures).
        # If watchdog says down AND direct probe also failed, we already flag `down`.
    except Exception as exc:
        report["checks"]["watchdog"] = {"state": "unavailable", "error": str(exc)}

    # Compute overall status
    broker_ok = report["checks"]["broker"].get("reachable")
    db_ok = report["checks"]["database"].get("reachable")
    disk_ok = report["checks"]["disk"].get("reachable")

    if not broker_ok or not db_ok:
        report["status"] = "down"
    elif not disk_ok or report["checks"]["config"].get("warnings"):
        report["status"] = "degraded"
    elif report["checks"]["database"].get("stale_positions", 0) > 0:
        report["status"] = "degraded"

    return report
