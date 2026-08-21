"""
===============================================================================
  metrics.py — In-process counters/gauges + Prometheus renderer (Sprint 6)
===============================================================================
Zero external dependency (no `prometheus_client`). Thread-safe. Fail-open.

Public API
----------
    inc(name, value=1.0, labels=None)
    set_gauge(name, value, labels=None)
    observe(name, value, labels=None)          # histogram-lite (sum+count)
    render_prometheus() -> str                 # text exposition format
    snapshot() -> dict                         # JSON-friendly for dashboards
    reset()                                    # test hook

Metric registry auto-declares each metric on first use. Types are locked in
after the first call — an accidental type change is logged and ignored.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("UTBotSRChannelsScanner")

_LOCK = threading.Lock()

# name -> {"type": "counter"|"gauge"|"histogram", "help": str,
#          "values": {label_key: float | (sum, count)}}
_REGISTRY: Dict[str, Dict[str, Any]] = {}

_START_TS: float = time.time()


def _label_key(labels: Optional[Dict[str, Any]]) -> str:
    """Stable canonical key for a label set: sorted k="v" joined by ','."""
    if not labels:
        return ""
    try:
        parts = []
        for k in sorted(labels.keys()):
            v = str(labels[k]).replace('"', "'").replace("\n", " ")
            parts.append(f'{k}="{v}"')
        return ",".join(parts)
    except Exception:
        return ""


def _ensure(name: str, kind: str, help_text: str = "") -> Optional[Dict[str, Any]]:
    m = _REGISTRY.get(name)
    if m is None:
        m = {"type": kind, "help": help_text or name, "values": {}}
        _REGISTRY[name] = m
        return m
    if m["type"] != kind:
        log.warning(
            "[metrics] type conflict on %s: existing=%s attempted=%s (ignored)",
            name, m["type"], kind,
        )
        return None
    return m


def declare(name: str, kind: str, help_text: str = "") -> None:
    with _LOCK:
        _ensure(name, kind, help_text)


def inc(name: str, value: float = 1.0, labels: Optional[Dict[str, Any]] = None,
        help_text: str = "") -> None:
    try:
        with _LOCK:
            m = _ensure(name, "counter", help_text)
            if m is None:
                return
            k = _label_key(labels)
            m["values"][k] = float(m["values"].get(k, 0.0)) + float(value)
    except Exception as exc:  # pragma: no cover - fail-open
        log.debug("[metrics] inc(%s) failed: %s", name, exc)


def set_gauge(name: str, value: float,
              labels: Optional[Dict[str, Any]] = None,
              help_text: str = "") -> None:
    try:
        with _LOCK:
            m = _ensure(name, "gauge", help_text)
            if m is None:
                return
            m["values"][_label_key(labels)] = float(value)
    except Exception as exc:
        log.debug("[metrics] set_gauge(%s) failed: %s", name, exc)


def observe(name: str, value: float,
            labels: Optional[Dict[str, Any]] = None,
            help_text: str = "") -> None:
    """Record into a histogram-lite bucket: (sum, count) tuple."""
    try:
        with _LOCK:
            m = _ensure(name, "histogram", help_text)
            if m is None:
                return
            k = _label_key(labels)
            s, c = m["values"].get(k, (0.0, 0))
            m["values"][k] = (float(s) + float(value), int(c) + 1)
    except Exception as exc:
        log.debug("[metrics] observe(%s) failed: %s", name, exc)


def reset() -> None:
    with _LOCK:
        _REGISTRY.clear()


def snapshot() -> Dict[str, Any]:
    """
    Return a JSON-safe snapshot of all metrics. Histograms exposed as
    {"sum", "count", "avg"}.
    """
    out: Dict[str, Any] = {}
    with _LOCK:
        for name, m in _REGISTRY.items():
            kind = m["type"]
            entry: Dict[str, Any] = {"type": kind, "help": m["help"], "series": []}
            for lk, v in m["values"].items():
                if kind == "histogram":
                    s, c = v
                    entry["series"].append({
                        "labels": lk, "sum": s, "count": c,
                        "avg": (s / c) if c else 0.0,
                    })
                else:
                    entry["series"].append({"labels": lk, "value": float(v)})
            out[name] = entry
    return out


def render_prometheus() -> str:
    """Emit all metrics in Prometheus text exposition format 0.0.4."""
    lines = []
    with _LOCK:
        uptime = time.time() - _START_TS
        lines.append("# HELP bot_uptime_seconds Seconds since metrics module first loaded.")
        lines.append("# TYPE bot_uptime_seconds gauge")
        lines.append(f"bot_uptime_seconds {uptime:.3f}")

        for name, m in _REGISTRY.items():
            kind = m["type"]
            help_text = m["help"].replace("\n", " ")
            lines.append(f"# HELP {name} {help_text}")
            promtype = "summary" if kind == "histogram" else kind
            lines.append(f"# TYPE {name} {promtype}")
            for lk, v in m["values"].items():
                label_expr = f"{{{lk}}}" if lk else ""
                if kind == "histogram":
                    s, c = v
                    lines.append(f"{name}_sum{label_expr} {float(s):.6f}")
                    lines.append(f"{name}_count{label_expr} {int(c)}")
                else:
                    lines.append(f"{name}{label_expr} {float(v):.6f}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Convenience wrappers (called from broker_retry, trading_adapter, scanner...)
# ---------------------------------------------------------------------------

def record_order(action: str, symbol: str, success: bool) -> None:
    labels = {"action": (action or "?").upper(), "outcome": "ok" if success else "fail"}
    inc("orders_total", 1.0, labels=labels,
        help_text="Total broker order attempts, labeled by action and outcome.")


def record_retry(op_name: str, attempts: int, exhausted: bool) -> None:
    labels = {"op": op_name or "unknown"}
    observe("retry_attempts", float(attempts), labels=labels,
            help_text="Attempts consumed per retryable op (sum+count).")
    if exhausted:
        inc("retry_exhausted_total", 1.0, labels=labels,
            help_text="Ops that hit max_attempts and re-raised.")


def record_signal(side: str, accepted: bool, reject_reason: str = "") -> None:
    labels = {"side": (side or "?").upper(),
              "outcome": "accepted" if accepted else "rejected"}
    inc("signals_total", 1.0, labels=labels,
        help_text="Signals generated by the scanner, labeled by outcome.")
    if not accepted and reject_reason:
        inc("signals_rejected_by_reason_total", 1.0,
            labels={"reason": reject_reason[:40]},
            help_text="Rejections bucketed by reason code (truncated to 40 chars).")


def record_rate_limit_block() -> None:
    inc("ratelimit_blocks_total", 1.0,
        help_text="Requests denied with HTTP 429.")


def record_broker_state(up: bool) -> None:
    set_gauge("broker_up", 1.0 if up else 0.0,
              help_text="1 if broker watchdog last-check succeeded, else 0.")


def record_watchdog_event(kind: str) -> None:
    """kind: 'disconnect' or 'recovery'."""
    inc("broker_watchdog_events_total", 1.0, labels={"kind": kind},
        help_text="Broker watchdog state-transition events.")

