"""
trade_management/executor.py
=============================
Side-effect layer: order placement + database updates for every TradeAction
produced by the rules engine.

This module is the *only* place that touches:
  - trading_adapter (order placement)
  - trade_db (persistence)
  - alerts (notifications)
  - active_positions dict

It does NOT contain any business-logic decisions — those live in rules_engine.py.
"""

from __future__ import annotations
import logging
import threading
from datetime import datetime
from typing import Optional

import trade_db
from trading_adapter import place_order as adapter_place_order
from .models import ExitOrderRequest, TradeAction, calc_pnl_amount
from . import alerts

log = logging.getLogger("UTBot.TradeManagement")


# ---------------------------------------------------------------------------
# Dispatcher: routes a TradeAction to the correct handler
# ---------------------------------------------------------------------------

def dispatch(
    action: TradeAction,
    pos: dict,
    ltp: float,
    config: dict,
    active_positions: dict,
    lock: threading.Lock,
    ws_client=None,
) -> None:
    """
    Execute the side-effects for a single TradeAction.

    Parameters
    ----------
    action           : decision produced by rules_engine.evaluate()
    pos              : the position dict (may be mutated in-place)
    ltp              : current last-traded price
    config           : full config dict
    active_positions : shared dict {pos_id: pos_dict} managed by PositionMonitor
    lock             : threading.Lock protecting active_positions
    ws_client        : OpenAlgo WS client (may be None)
    """
    t = action.action_type

    if t in ("EXIT_TARGET", "EXIT_SL"):
        execute_full_exit(pos, ltp, action.reason, config, active_positions, lock, ws_client)

    elif t == "TRAILING_SL":
        _apply_sl_update(pos, action.new_sl, "SL_MOVED",
                         f"Trailing SL: {action.reason}", config, notify_fn=alerts.alert_sl_move)

    elif t == "PROFIT_LOCK":
        _apply_profit_lock(pos, action.new_sl, action.tier_index, action.reason, config)

    elif t == "PARTIAL_EXIT":
        tm_cfg = config.get("trade_management", {})
        pe_cfg = tm_cfg.get("partial_exit", {})
        execute_partial_exit(pos, ltp, action.exit_qty, action.tier_index, pe_cfg, config)

    else:
        log.debug("Unknown TradeAction type: %s — skipped.", t)


# ---------------------------------------------------------------------------
# Full Exit
# ---------------------------------------------------------------------------

def execute_full_exit(
    pos: dict,
    exit_price: float,
    reason: str,
    config: dict,
    active_positions: dict,
    lock: threading.Lock,
    ws_client=None,
) -> None:
    """Place exit order, update DB, remove from active dict, send alert."""
    pos_id    = pos["id"]
    direction = pos["direction"]
    log.info("Executing full exit for %s (%s). Reason: %s", pos["symbol"], direction, reason)

    exit_action = "SELL" if direction == "BUY" else "BUY"
    req = ExitOrderRequest(
        symbol   = pos["symbol"],
        exchange = pos["exchange"],
        action   = exit_action,
        quantity = pos["quantity"],
        product  = pos.get("product", "MIS"),
    )

    try:
        res = adapter_place_order(config, req)
        if res.get("status") == "success":
            entry = float(pos["entry_price"])
            pnl_pct = (
                (exit_price - entry) / entry * 100
                if direction == "BUY"
                else (entry - exit_price) / entry * 100
            )
            pnl_pct = round(pnl_pct, 2)
            # Total rupee P&L = whatever was already realised via earlier partial
            # exits (0 if there were none) + P&L on the quantity closed just now.
            # This keeps pnl_amount correct even when a position was partially
            # scaled out before this final target/SL exit fired.
            already_realized = float(pos.get("realized_pnl_amount", 0.0))
            pnl_amount = round(already_realized + calc_pnl_amount(pos, exit_price), 2)

            trade_db.update_position(
                pos_id,
                status      = "CLOSED",
                close_reason= reason,
                close_price = exit_price,
                close_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                pnl_pct     = pnl_pct,
                pnl_amount  = pnl_amount,
            )
            trade_db.log_event(
                pos_id, "EXIT_TRIGGERED", None, exit_price,
                f"Position closed via {reason} @ ₹{exit_price:.2f} (₹{pnl_amount:+.2f})",
            )

            # Notify
            alerts.alert_exit(pos, exit_price, reason, pnl_pct, config, pnl_amount=pnl_amount)

            # Remove from active positions
            with lock:
                active_positions.pop(pos_id, None)

            # Unsubscribe from WebSocket feed
            if ws_client:
                try:
                    ws_client.unsubscribe_ltp([{"exchange": pos["exchange"], "symbol": pos["symbol"]}])
                except Exception:
                    pass

            log.info("Position %d closed. PnL: %+.2f%% (₹%+.2f)", pos_id, pnl_pct, pnl_amount)
        else:
            msg = res.get("message", str(res))
            log.error("Exit order failed for position %d: %s", pos_id, msg)
            trade_db.update_position(pos_id, status="ERROR", close_reason="EXIT_FAILED")
            trade_db.log_event(pos_id, "ERROR", None, None, f"Exit order failed: {msg}")

    except Exception as exc:
        log.error("Exception during full exit for position %d: %s", pos_id, exc)


# ---------------------------------------------------------------------------
# Partial Exit
# ---------------------------------------------------------------------------

def execute_partial_exit(
    pos: dict,
    price: float,
    exit_qty: int,
    tier_index: int,
    pe_cfg: dict,
    config: dict,
) -> None:
    """Place partial exit order, update DB and position dict."""
    pos_id    = pos["id"]
    direction = pos["direction"]
    current_qty = pos["quantity"]

    if exit_qty <= 0 or exit_qty > current_qty:
        log.warning("Partial exit qty %d invalid for position %d (current qty %d). Skipped.",
                    exit_qty, pos_id, current_qty)
        return

    exit_action = "SELL" if direction == "BUY" else "BUY"
    req = ExitOrderRequest(
        symbol   = pos["symbol"],
        exchange = pos["exchange"],
        action   = exit_action,
        quantity = exit_qty,
        product  = pos.get("product", "MIS"),
    )

    entry = float(pos["entry_price"])
    tranche_pnl_amount = round(
        (price - entry) * exit_qty if direction == "BUY" else (entry - price) * exit_qty, 2
    )

    log.info("Partial exit tier %d: %d units of %s @ ~₹%.2f (₹%+.2f on this tranche)",
             tier_index + 1, exit_qty, pos["symbol"], price, tranche_pnl_amount)

    try:
        res = adapter_place_order(config, req)
        if res.get("status") == "success":
            new_qty = current_qty - exit_qty

            # Resolve per-tier move-to-breakeven flag
            tiers = pe_cfg.get("tiers", [])
            if tiers and tier_index < len(tiers):
                move_to_be = bool(tiers[tier_index].get("move_sl_to_be", False))
            else:
                move_to_be = bool(pe_cfg.get("move_sl_to_breakeven", True))

            new_realized = round(float(pos.get("realized_pnl_amount", 0.0)) + tranche_pnl_amount, 2)

            updates = {
                "quantity":            new_qty,
                "partial_exit_tier":   tier_index + 1,
                "realized_pnl_amount": new_realized,
            }
            note_suffix = ""

            if move_to_be and new_qty > 0:
                be_price = float(pos["entry_price"])
                if (direction == "BUY"  and be_price > pos["current_sl"]) or \
                   (direction == "SELL" and be_price < pos["current_sl"]):
                    updates["current_sl"] = be_price
                    pos["current_sl"] = be_price
                    note_suffix = " & SL moved to breakeven"

            trade_db.update_position(pos_id, **updates)
            trade_db.log_event(
                pos_id, "PARTIAL_EXIT", current_qty, new_qty,
                f"Tier {tier_index + 1}: exited {exit_qty} units @ ₹{price:.2f} "
                f"(₹{tranche_pnl_amount:+.2f}){note_suffix}",
            )

            # Update in-memory position
            pos["quantity"] = new_qty
            pos["partial_exit_tier"] = tier_index + 1
            pos["realized_pnl_amount"] = new_realized

            # Handle fully exited via partials
            if new_qty <= 0:
                _mark_closed_by_partials(pos, price, config)
                return

            alerts.alert_partial_exit(pos, exit_qty, price, tier_index, config,
                                       pnl_amount=tranche_pnl_amount)
        else:
            log.error("Partial exit order failed for position %d: %s", pos_id, res.get("message"))

    except Exception as exc:
        log.error("Exception during partial exit for position %d: %s", pos_id, exc)


def _mark_closed_by_partials(pos: dict, price: float, config: dict) -> None:
    """
    When all quantity is exited via partials, mark position as closed.

    pos["quantity"] is already 0 at this point (set by the caller just
    before it calls us), and pos["realized_pnl_amount"] already includes
    the tranche that just brought it to zero — so
    realized_pnl_amount + calc_pnl_amount(pos, price) collapses to exactly
    realized_pnl_amount (calc_pnl_amount is 0 on a 0-quantity position).
    Using the same formula as execute_full_exit here (rather than a special
    case) keeps the two code paths consistent.
    """
    pos_id = pos["id"]
    entry = float(pos["entry_price"])
    pnl_pct = (
        (price - entry) / entry * 100
        if pos["direction"] == "BUY"
        else (entry - price) / entry * 100
    )
    pnl_amount = round(float(pos.get("realized_pnl_amount", 0.0)) + calc_pnl_amount(pos, price), 2)
    trade_db.update_position(
        pos_id,
        status      = "CLOSED",
        close_reason= "FULL_PARTIAL_EXIT",
        close_price = price,
        close_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        pnl_pct     = round(pnl_pct, 2),
        pnl_amount  = pnl_amount,
    )
    trade_db.log_event(pos_id, "EXIT_TRIGGERED", None, price,
                       f"Position fully closed via partial exits (₹{pnl_amount:+.2f})")
    alerts.alert_exit(pos, price, "FULL_PARTIAL_EXIT", round(pnl_pct, 2), config, pnl_amount=pnl_amount)


# ---------------------------------------------------------------------------
# SL Adjustments (Trailing / Profit Lock shared helper)
# ---------------------------------------------------------------------------

def _apply_sl_update(
    pos: dict,
    new_sl: float,
    event_type: str,
    note: str,
    config: dict,
    notify_fn=None,
) -> None:
    """Write SL change to DB and in-memory position, then send notification."""
    pos_id = pos["id"]
    old_sl = pos["current_sl"]

    trade_db.update_position(pos_id, current_sl=new_sl, trailing_active=1)
    trade_db.log_event(pos_id, event_type, old_sl, new_sl, note)

    pos["current_sl"]    = new_sl
    pos["trailing_active"] = 1

    if notify_fn:
        notify_fn(pos, old_sl, new_sl, config)


def _apply_profit_lock(
    pos: dict,
    new_sl: float,
    tier_index: int,
    note: str,
    config: dict,
) -> None:
    """Write profit-lock SL change to DB and notify."""
    pos_id = pos["id"]
    old_sl = pos["current_sl"]

    trade_db.update_position(
        pos_id,
        current_sl       = new_sl,
        profit_locked    = 1,
        profit_lock_tier = tier_index,
    )
    trade_db.log_event(pos_id, "PROFIT_LOCKED", old_sl, new_sl, note)

    pos["current_sl"]       = new_sl
    pos["profit_locked"]    = 1
    pos["profit_lock_tier"] = tier_index

    alerts.alert_profit_lock(pos, old_sl, new_sl, tier_index, config)
