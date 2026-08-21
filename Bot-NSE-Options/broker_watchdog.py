"""
===============================================================================
  broker_watchdog.py — Background health-checker for OpenAlgo (Sprint 6)
===============================================================================
Daemon thread that periodically pings the broker via a cheap LTP lookup on
the underlying index and tracks up/down state. On disconnect it emits a
WARNING log + Prometheus event; on recovery it emits an INFO log + recovery
event.

State machine (threshold-triggered):
    UP    -> after `failure_threshold` consecutive failures -> DOWN + disconnect
    DOWN  -> after 1 success                                -> UP   + recovery

Fail-open: any internal error is caught and treated as "no state change".
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import metrics

log = logging.getLogger("UTBotSRChannelsScanner")

_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_RUNNING: bool = False

_STATE: Dict[str, Any] = {
    "state": "unknown",            # "up" | "down" | "unknown"
    "last_check_ts": 0.0,
    "last_change_ts": 0.0,
    "last_error": "",
    "consecutive_failures": 0,
    "last_latency_ms": None,
}


def get_state() -> Dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _read_cfg(cfg: Optional[dict]) -> Dict[str, Any]:
    w = ((cfg or {}).get("bot", {}) or {}).get("watchdog", {}) or {}
    return {
        "enabled": bool(w.get("enabled", True)),
        "interval_sec": max(5, int(w.get("interval_sec", 30))),
        "failure_threshold": max(1, int(w.get("failure_threshold", 3))),
    }


def _probe_broker(cfg: dict) -> Tuple[bool, str, Optional[int]]:
    try:
        import trading_adapter
        oa = (cfg.get("openalgo", {}) or {})
        if not oa.get("apikey"):
            return False, "no_apikey", None
        underlying = (cfg.get("options", {}) or {}).get("underlying", "NIFTY")
        exchange = (cfg.get("options", {}) or {}).get("index_exchange", "NSE_INDEX")
        t0 = time.time()
        ltp = trading_adapter.get_ltp(cfg, underlying, exchange)
        elapsed_ms = int((time.time() - t0) * 1000)
        if ltp and float(ltp) > 0:
            return True, f"ltp={ltp}", elapsed_ms
        return False, "ltp_zero_or_missing", elapsed_ms
    except Exception as exc:
        return False, f"error: {exc.__class__.__name__}: {exc}", None


def _apply_probe_result(ok: bool, detail: str, latency_ms: Optional[int],
                        threshold: int) -> None:
    """Update state machine + emit logs & metrics on transitions."""
    with _LOCK:
        _STATE["last_check_ts"] = time.time()
        _STATE["last_error"] = "" if ok else detail
        _STATE["last_latency_ms"] = latency_ms
        prev_state = _STATE["state"]

        if ok:
            _STATE["consecutive_failures"] = 0
            if prev_state != "up":
                _STATE["state"] = "up"
                _STATE["last_change_ts"] = time.time()
                metrics.record_broker_state(True)
                if prev_state == "down":
                    metrics.record_watchdog_event("recovery")
                    log.info("[watchdog] broker RECOVERED (latency=%sms)", latency_ms)
                else:
                    log.info("[watchdog] broker up (latency=%sms)", latency_ms)
        else:
            _STATE["consecutive_failures"] += 1
            fails = _STATE["consecutive_failures"]
            if prev_state != "down" and fails >= threshold:
                _STATE["state"] = "down"
                _STATE["last_change_ts"] = time.time()
                metrics.record_broker_state(False)
                metrics.record_watchdog_event("disconnect")
                log.warning(
                    "[watchdog] broker DOWN after %d consecutive failures: %s",
                    fails, detail,
                )


def _loop(cfg_getter) -> None:
    """Daemon loop. `cfg_getter` returns fresh cfg each tick."""
    global _RUNNING
    log.info("[watchdog] thread started")
    while _RUNNING:
        try:
            cfg = cfg_getter() or {}
            settings = _read_cfg(cfg)
            if not settings["enabled"]:
                for _ in range(settings["interval_sec"]):
                    if not _RUNNING:
                        break
                    time.sleep(1)
                continue

            ok, detail, latency_ms = _probe_broker(cfg)
            _apply_probe_result(ok, detail, latency_ms, settings["failure_threshold"])

            for _ in range(settings["interval_sec"]):
                if not _RUNNING:
                    break
                time.sleep(1)
        except Exception as exc:  # pragma: no cover - fail-open loop
            log.debug("[watchdog] loop tick failed: %s", exc)
            time.sleep(5)
    log.info("[watchdog] thread stopped")


def start(cfg: dict) -> bool:
    """Spawn the watchdog daemon. Idempotent."""
    global _THREAD, _RUNNING
    settings = _read_cfg(cfg)
    if not settings["enabled"]:
        log.info("[watchdog] disabled via config")
        return False
    if _THREAD is not None and _THREAD.is_alive():
        log.debug("[watchdog] already running")
        return False

    def _cfg_getter():
        try:
            from scanner import load_config
            return load_config()
        except Exception:
            return cfg

    _RUNNING = True
    _THREAD = threading.Thread(
        target=_loop, args=(_cfg_getter,),
        name="broker-watchdog", daemon=True,
    )
    _THREAD.start()
    log.info(
        "[watchdog] started (interval=%ds threshold=%d)",
        settings["interval_sec"], settings["failure_threshold"],
    )
    return True


def stop() -> None:
    """Signal the loop to exit (non-blocking)."""
    global _RUNNING
    _RUNNING = False

