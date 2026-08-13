"""
trade_management/models.py
==========================
Shared data structures for the trade management package.

Provides:
  - ExitOrderRequest  : minimal duck-type compatible with the OrderRequest pydantic
                        model expected by trading_adapter.place_order()
  - position helpers  : utility functions that work on raw position dicts (as
                        returned by trade_db) without imposing a heavy ORM layer.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Exit / Partial-Exit Order Request
# ---------------------------------------------------------------------------

@dataclass
class ExitOrderRequest:
    """
    Minimal order request object accepted by trading_adapter.place_order().

    Replaces the two ad-hoc inline mock classes that previously existed inside
    _execute_exit() and _execute_partial_exit() in the old trade_manager.py.
    """
    symbol: str
    exchange: str
    action: str          # "BUY" or "SELL"
    quantity: int
    product: str = "MIS"
    strategy: str = "UTBotSR_TradeManager"
    price_type: str = "MARKET"
    price: float = 0.0
    trigger_price: float = 0.0


# ---------------------------------------------------------------------------
# Action types returned by the rules engine
# ---------------------------------------------------------------------------

@dataclass
class TradeAction:
    """
    Represents a single decision produced by the rules engine for a given
    price tick.  The executor consumes this and carries out the side-effects.

    action_type values
    ------------------
    EXIT_TARGET       — close full position; target reached
    EXIT_SL           — close full position; stop-loss hit
    PARTIAL_EXIT      — scale out a tranche of the position
    PROFIT_LOCK       — ratchet SL up to lock-in a portion of unrealised profit
    TRAILING_SL       — move SL behind the high-water mark
    UPDATE_HWM        — update high/low water mark only (no order needed)
    """
    action_type: str               # see values above
    new_sl: Optional[float] = None
    exit_qty: Optional[int] = None
    tier_index: Optional[int] = None   # which tier fired (0-based)
    reason: str = ""


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def calc_gain_pct(pos: dict, ltp: float) -> float:
    """
    Return the current unrealised gain as a percentage of entry price.
    Positive = profit, negative = loss.  Works for both BUY and SELL.
    """
    entry = float(pos["entry_price"])
    if entry <= 0:
        return 0.0
    if pos["direction"] == "BUY":
        return (ltp - entry) / entry * 100.0
    else:
        return (entry - ltp) / entry * 100.0


def calc_sl_price(entry: float, sl_pct: float, direction: str) -> float:
    """
    Convert a stop-loss percentage into an absolute price level.

    Uses entry-price-anchored arithmetic so the result is meaningful
    regardless of whether the stock trades at ₹50 or ₹5 000.

    Parameters
    ----------
    entry     : entry price of the position
    sl_pct    : stop-loss distance as a positive percentage (e.g. 1.0 = 1%)
    direction : "BUY" or "SELL"
    """
    if direction == "BUY":
        return entry * (1.0 - sl_pct / 100.0)
    return entry * (1.0 + sl_pct / 100.0)


def calc_target_price(entry: float, tgt_pct: float, direction: str) -> float:
    """
    Convert a target percentage into an absolute price level.

    Parameters
    ----------
    entry     : entry price
    tgt_pct   : target profit as a positive percentage (e.g. 2.0 = 2%)
    direction : "BUY" or "SELL"
    """
    if direction == "BUY":
        return entry * (1.0 + tgt_pct / 100.0)
    return entry * (1.0 - tgt_pct / 100.0)


def sl_improves(direction: str, candidate_sl: float, current_sl: float) -> bool:
    """
    Return True if candidate_sl offers *better* protection than current_sl.

    For a BUY position, a higher SL is better (more protected from downside).
    For a SELL position, a lower SL is better (more protected from upside).
    """
    if direction == "BUY":
        return candidate_sl > current_sl
    return candidate_sl < current_sl
