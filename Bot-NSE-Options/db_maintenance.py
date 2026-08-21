"""
===============================================================================
  db_maintenance.py — Database reconciliation & health (Sprint 5)
===============================================================================
Utilities to keep `trades.db` clean and to expose DB health to the dashboard.

Primary use case: after a crash / disconnect the OPEN trades table may accumulate
positions the broker no longer holds. This module lets ops close those rows in
bulk (zero PnL — treated as flat) so new orders aren't blocked by the
duplicate-entry guard.

Public API:
    count_open_positions()       -> int
    count_stale_positions(hours) -> int
    reconcile_stale_positions(hours=24, dry_run=False) -> dict
    db_health() -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import trade_db

log = logging.getLogger("UTBotSRChannelsScanner")


def _parse_opened_at(val: Any) -> datetime | None:
    """Best-effort ISO datetime parsing; returns None on failure."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        # Try a couple of common alternate formats before giving up.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(val), fmt)
            except Exception:
                continue
        return None


def count_open_positions() -> int:
    try:
        return len(trade_db.get_active_trades())
    except Exception as exc:
        log.debug("[db_maint] count_open_positions failed: %s", exc)
        return 0


def _get_stale(cutoff_hours: int) -> List[dict]:
    """Return list of OPEN trades whose opened_at is older than cutoff_hours."""
    cutoff = datetime.now() - timedelta(hours=max(0, int(cutoff_hours)))
    stale: List[dict] = []
    try:
        for t in trade_db.get_active_trades():
            opened = _parse_opened_at(t.get("opened_at"))
            if opened is None or opened <= cutoff:
                stale.append(t)
    except Exception as exc:
        log.debug("[db_maint] _get_stale failed: %s", exc)
    return stale


def count_stale_positions(cutoff_hours: int = 24) -> int:
    return len(_get_stale(cutoff_hours))


def reconcile_stale_positions(cutoff_hours: int = 24, dry_run: bool = False) -> Dict[str, Any]:
    """
    Close all OPEN trades whose `opened_at` is older than `cutoff_hours`.

    Uses each row's own `entry_price` as the exit price -> booked PnL = 0.
    Sets exit_reason='STALE_RECONCILE' so audits can find them later.

    Returns:
        {
          "status": "success"|"partial"|"error",
          "requested_cutoff_hours": int,
          "candidates": int,      # rows matched
          "closed": int,          # rows actually closed
          "dry_run": bool,
          "symbols": [str, ...],
          "errors": [str, ...],
        }
    """
    result: Dict[str, Any] = {
        "status": "success",
        "requested_cutoff_hours": int(cutoff_hours),
        "candidates": 0,
        "closed": 0,
        "dry_run": bool(dry_run),
        "symbols": [],
        "errors": [],
    }

    try:
        stale = _get_stale(cutoff_hours)
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"lookup_failed: {exc}")
        return result

    result["candidates"] = len(stale)
    if not stale:
        return result

    if dry_run:
        result["symbols"] = [t.get("symbol", "?") for t in stale]
        return result

    for t in stale:
        try:
            trade_db.close_trade(
                trade_id=int(t["trade_id"]),
                exit_price=float(t.get("entry_price") or 0.0),
                exit_reason="STALE_RECONCILE",
            )
            result["closed"] += 1
            result["symbols"].append(t.get("symbol", "?"))
        except Exception as exc:
            result["errors"].append(f"{t.get('symbol', '?')}: {exc}")

    if result["errors"] and result["closed"] > 0:
        result["status"] = "partial"
    elif result["errors"]:
        result["status"] = "error"

    log.info(
        "[db_maint] reconcile done: candidates=%d closed=%d errors=%d cutoff=%dh",
        result["candidates"], result["closed"], len(result["errors"]), int(cutoff_hours),
    )
    return result


def db_health() -> Dict[str, Any]:
    """Lightweight DB reachability + counts snapshot for /api/health."""
    out: Dict[str, Any] = {
        "reachable": False,
        "open_positions": 0,
        "path": str(getattr(trade_db, "DB_PATH", "trades.db")),
        "error": "",
    }
    try:
        conn = trade_db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
            row = cur.fetchone()
            out["open_positions"] = int(row[0] if row else 0)
            out["reachable"] = True
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:
        out["error"] = str(exc)
    return out
