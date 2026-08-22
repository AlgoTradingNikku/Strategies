"""
risk_limits.py
==============
Pre-trade risk gate that runs BEFORE any auto-order is dispatched.

The scanner's auto-order block delegates each candidate to
``check_can_open_new(...)`` which returns ``(ok, reason)`` — when ``ok`` is
False the caller must skip the trade and log/alert with the ``reason``
string.

All limits are OFF unless the ``risk_limits`` block is present in
config.yml, so this module is fully backwards-compatible.

Config schema (all optional, added under top-level ``risk_limits``)
-------------------------------------------------------------------
risk_limits:
  enabled:                       true          # master on/off
  max_concurrent_positions:      5             # cap total open trades
  max_positions_per_symbol:      1             # cap open per symbol
  daily_loss_stop_pct:           -3.0          # negative → cutoff floor

The daily-loss check sums ``pnl_pct`` for positions closed since local
midnight; when the running total drops below ``daily_loss_stop_pct`` the
gate rejects further trades for the remainder of the trading day.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

import trade_db

log = logging.getLogger("UTBotSRChannelsScanner")


def _limits(config: dict) -> dict:
    """Return the ``risk_limits`` sub-dict (never None)."""
    return config.get("risk_limits", {}) or {}


def check_can_open_new(
    symbol: str,
    config: dict,
    open_positions: Optional[list[dict]] = None,
) -> tuple[bool, str]:
    """Decide whether a new position may be opened right now.

    Returns
    -------
    (ok, reason) : tuple[bool, str]
        ``ok`` is True when all configured limits allow the trade.
        ``reason`` is a short human-readable string when ``ok`` is False,
        otherwise the empty string.

    Parameters
    ----------
    symbol
        The candidate trading symbol (used for the per-symbol cap).
    config
        Full config dict — reads ``risk_limits`` sub-section.
    open_positions
        Optional pre-fetched list from ``trade_db.get_open_positions()`` to
        avoid a duplicate DB round-trip when the caller already has it.
    """
    lim = _limits(config)
    if not lim.get("enabled", False):
        return True, ""

    if open_positions is None:
        try:
            open_positions = trade_db.get_open_positions()
        except Exception as exc:      # pragma: no cover — DB failure path
            log.warning("risk_limits: DB read failed (%s) — allowing trade.", exc)
            return True, ""

    # ---- 1. Total concurrent positions ------------------------------------
    max_total = lim.get("max_concurrent_positions")
    if isinstance(max_total, int) and max_total >= 0:
        if len(open_positions) >= max_total:
            return False, f"max_concurrent_positions ({max_total}) reached"

    # ---- 2. Per-symbol cap ------------------------------------------------
    max_per_sym = lim.get("max_positions_per_symbol")
    if isinstance(max_per_sym, int) and max_per_sym >= 0:
        already = sum(1 for p in open_positions if p.get("symbol") == symbol)
        if already >= max_per_sym:
            return False, (
                f"max_positions_per_symbol ({max_per_sym}) reached for {symbol}"
            )

    # ---- 3. Daily realized-loss cutoff ------------------------------------
    #      Interpreted as a floor: if cumulative closed pnl_pct today is at
    #      or below this negative number, block new trades. Users may set
    #      it to a positive number (unusual) to require a profit floor.
    dls = lim.get("daily_loss_stop_pct")
    if isinstance(dls, (int, float)):
        try:
            midnight = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).strftime("%Y-%m-%d %H:%M:%S")
            realised_today = trade_db.get_realized_pnl_pct_since(midnight)
            if realised_today <= float(dls):
                return False, (
                    f"daily_loss_stop_pct hit "
                    f"(today's realized PnL {realised_today:+.2f}% "
                    f"≤ cutoff {float(dls):+.2f}%)"
                )
        except Exception as exc:      # pragma: no cover
            log.debug("risk_limits: daily-loss check failed (%s) — allowing.", exc)

    return True, ""


def compute_quantity(
    close_price: float,
    config: dict,
    fallback_qty: int = 1,
) -> int:
    """Return the quantity to trade based on capital-per-trade config.

    If ``openalgo.capital_per_trade`` is set to a positive number, quantity
    is ``max(1, floor(capital / close_price))``. Otherwise falls back to
    ``openalgo.order_quantity`` (or ``fallback_qty`` when that's absent).
    """
    oa = config.get("openalgo", {}) or {}
    cap = oa.get("capital_per_trade")

    if cap is not None:
        try:
            cap_f = float(cap)
            if cap_f > 0 and close_price > 0:
                return max(1, int(cap_f // close_price))
        except (TypeError, ValueError):
            pass

    try:
        return max(1, int(oa.get("order_quantity", fallback_qty)))
    except (TypeError, ValueError):
        return fallback_qty
