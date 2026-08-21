"""
===============================================================================
  circuit_breaker.py — Sprint 2: Consecutive-Loss Circuit Breaker
===============================================================================
Tracks consecutive losing trades TODAY and halts new orders for a cool-down
window when the count exceeds a threshold.

Rationale: options-buying strategies can suffer edge decay within a session
(e.g. regime shift from trending to chop). N consecutive losses is a strong
signal to pause and re-evaluate rather than compound losses.

State source: `trade_db` — reads today's CLOSED trades ordered by close time.
Consecutive-loss count resets to 0 on the first winning trade of the streak.

All checks are toggleable via config.yml `risk.consecutive_loss_breaker`.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = None

log = logging.getLogger("UTBotSRChannelsScanner")

_state_lock = threading.Lock()
_breaker_tripped_at: Dict[str, datetime] = {}  # date-str -> trip datetime


def _now_ist() -> datetime:
    return datetime.now(_IST) if _IST else datetime.now()


def _today_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def get_consecutive_losses() -> int:
    """
    Count trailing consecutive losing trades from today's CLOSED trades.
    Walks backwards from most recent close; stops at first winner.
    """
    try:
        import trade_db
        conn = trade_db.get_connection()
        cur = conn.cursor()
        today = _today_str()
        # SQLite: closed_at is ISO string; substring compare works
        cur.execute(
            """
            SELECT pnl_amount FROM trades
            WHERE status = 'CLOSED'
              AND substr(closed_at, 1, 10) = ?
            ORDER BY closed_at DESC
            """,
            (today,),
        )
        streak = 0
        for row in cur.fetchall():
            pnl = float(row["pnl_amount"] or 0.0)
            if pnl < 0:
                streak += 1
            else:
                break
        conn.close()
        return streak
    except Exception as exc:
        log.debug("[circuit_breaker] get_consecutive_losses error: %s", exc)
        return 0


def is_tripped(cfg: dict) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (tripped, reason, info).
    tripped=True means block new orders.
    """
    cb = cfg.get("risk", {}).get("consecutive_loss_breaker", {})
    info = {"enabled": bool(cb.get("enabled", True)), "streak": 0, "max_losses": int(cb.get("max_losses", 3))}
    if not info["enabled"]:
        return False, "", info

    max_losses = int(cb.get("max_losses", 3))
    cooldown_min = int(cb.get("cooldown_minutes", 30))
    streak = get_consecutive_losses()
    info["streak"] = streak

    # If streak already tripped today, honor cooldown timer
    today = _today_str()
    with _state_lock:
        tripped_at = _breaker_tripped_at.get(today)

    if tripped_at:
        elapsed = (_now_ist() - tripped_at).total_seconds() / 60.0
        if elapsed < cooldown_min:
            remaining = int(cooldown_min - elapsed)
            info["cooldown_remaining_min"] = remaining
            return True, f"CIRCUIT_BREAKER_ACTIVE({remaining}m_left)", info
        # Cool-down expired; clear and re-evaluate against fresh streak
        with _state_lock:
            _breaker_tripped_at.pop(today, None)

    if streak >= max_losses:
        with _state_lock:
            _breaker_tripped_at[today] = _now_ist()
        info["cooldown_remaining_min"] = cooldown_min
        return True, f"CONSECUTIVE_LOSSES({streak}>={max_losses})", info

    return False, "", info


def reset_state() -> None:
    """Test hook: clear the tripped-cache. Not called in production."""
    with _state_lock:
        _breaker_tripped_at.clear()
