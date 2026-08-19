"""
trade_management/rules_engine.py
=================================
Pure business logic for position monitoring.

This module is deliberately side-effect free:
  - No database calls
  - No network / order placement
  - No logging of side-effects (only debug logging)
  - Easily unit-testable in isolation

It receives the current position state (dict) and the latest LTP, then
returns a list of TradeAction objects that the executor will carry out.

Config structure expected under `trade_management` key
------------------------------------------------------

stop_loss_pct   : float   fallback % SL below/above entry (used when opening)
target_pct      : float   fallback % target above/below entry (used when opening)

partial_exit:
  enabled        : bool
  tiers:
    - trigger_pct      : float   % gain that triggers this partial exit
      exit_qty_fraction: float   fraction of *current* quantity to exit (0.0-1.0)
      move_sl_to_be    : bool    move SL to breakeven after this tier fires
  # Legacy flat keys (still honoured if `tiers` is absent):
  target1_pct        : float
  exit_qty_fraction  : float
  move_sl_to_breakeven: bool

profit_lock:
  enabled : bool
  tiers:
    - threshold_pct : float   % gain that activates this tier
      lock_fraction : float   fraction of peak gain (hwm-entry) to lock (0-1)
  # Legacy flat keys (still honoured if `tiers` is absent):
  threshold_pct : float
  lock_fraction : float

trailing_sl:
  enabled        : bool
  activation_pct : float   minimum % gain before trailing starts
  tiers:
    - min_gain_pct  : float   % gain at which this tier becomes active
      distance_pct  : float   keep SL this % behind HWM
  # Legacy flat key (still honoured if `tiers` is absent):
  distance_pct   : float

All percentage values are relative to entry price, making the logic correct
for both low-priced (₹50) and high-priced (₹5 000) stocks.
"""

from __future__ import annotations
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List

from .models import TradeAction, calc_gain_pct, sl_improves

log = logging.getLogger("UTBotSRChannelsScanner")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def evaluate(pos: dict, ltp: float, tm_cfg: dict) -> List[TradeAction]:
    """
    Evaluate all rules against the current position and return a list of
    TradeAction objects, ordered by priority:

      0. EOD Auto-Exit (highest priority — closes at 15:15 before broker RMS charges)
      1. Target exit   (immediate close)
      2. SL exit       (immediate close)
      3. Trailing SL   (adjust SL — must be checked BEFORE profit_lock so
                        the tighter rule wins when both fire on same tick)
      4. Profit Lock   (adjust SL to lock a fraction of peak gain)
      5. Partial Exit  (scale out a tranche)

    The executor processes actions in order and stops after any EXIT action.
    """
    actions: List[TradeAction] = []

    # --- 0. EOD Auto Square-Off Check (e.g. 15:15 IST) ---
    eod_exit = _check_eod_square_off(tm_cfg)
    if eod_exit:
        return [eod_exit]

    # --- 1. High-water mark update (not an action, mutates pos in-place) ---
    _update_hwm(pos, ltp)

    gain_pct = calc_gain_pct(pos, ltp)

    # --- 2. Target exit ---
    exit_action = _check_target_exit(pos, ltp)
    if exit_action:
        return [exit_action]   # no further evaluation needed

    # --- 3. SL exit ---
    sl_exit = _check_sl_exit(pos, ltp)
    if sl_exit:
        return [sl_exit]

    # --- 4. Trailing SL ---
    tsl_action = _check_trailing_sl(pos, ltp, gain_pct, tm_cfg)
    if tsl_action:
        actions.append(tsl_action)

    # --- 5. Profit Lock ---
    pl_action = _check_profit_lock(pos, ltp, gain_pct, tm_cfg)
    if pl_action:
        # Only apply profit lock if it beats the trailing-SL level already proposed
        if actions and actions[-1].action_type == "TRAILING_SL":
            # Keep whichever gives better protection
            if sl_improves(pos["direction"], pl_action.new_sl, actions[-1].new_sl):
                actions[-1] = pl_action   # profit lock is tighter, replace
            # else trailing SL already gives better protection — discard profit lock
        else:
            actions.append(pl_action)

    # --- 6. Partial Exit ---
    pe_action = _check_partial_exit(pos, ltp, gain_pct, tm_cfg)
    if pe_action:
        actions.append(pe_action)

    return actions


# ---------------------------------------------------------------------------
# Rule: High-Water Mark / Low-Water Mark
# ---------------------------------------------------------------------------

def _update_hwm(pos: dict, ltp: float) -> None:
    """Update the high (or low) water mark in-place on the position dict."""
    if pos["direction"] == "BUY":
        if ltp > pos["high_water_mark"]:
            pos["high_water_mark"] = ltp
            pos["_hwm_dirty"] = True   # signals monitor to persist to DB
    else:
        if ltp < pos["high_water_mark"]:
            pos["high_water_mark"] = ltp
            pos["_hwm_dirty"] = True


# ---------------------------------------------------------------------------
# Rule: EOD Intraday Auto Square-Off (e.g. 15:15 IST)
# ---------------------------------------------------------------------------

def _check_eod_square_off(tm_cfg: dict) -> TradeAction | None:
    if not tm_cfg.get("auto_square_off_enabled", False):
        return None
    cutoff_str = tm_cfg.get("auto_square_off_time", "15:15")
    try:
        now_tz = datetime.now(ZoneInfo("Asia/Kolkata"))
        cutoff_parts = [int(p) for p in cutoff_str.strip().split(":")]
        cutoff_min = cutoff_parts[0] * 60 + cutoff_parts[1]
        now_min = now_tz.hour * 60 + now_tz.minute
        if now_min >= cutoff_min:
            return TradeAction(action_type="EXIT_TARGET", reason="EOD_SQUARE_OFF")
    except Exception as e:
        log.debug("EOD square-off time evaluation error: %s", e)
    return None


# ---------------------------------------------------------------------------
# Rule: Target Exit
# ---------------------------------------------------------------------------

def _check_target_exit(pos: dict, ltp: float) -> TradeAction | None:
    target = pos.get("target_price", 0.0)
    if not target:
        return None
    direction = pos["direction"]
    hit = (direction == "BUY" and ltp >= target) or \
          (direction == "SELL" and ltp <= target)
    if hit:
        return TradeAction(action_type="EXIT_TARGET", reason="TARGET")
    return None


# ---------------------------------------------------------------------------
# Rule: Stop-Loss Exit
# ---------------------------------------------------------------------------

def _check_sl_exit(pos: dict, ltp: float) -> TradeAction | None:
    current_sl = pos.get("current_sl", 0.0)
    if not current_sl:
        return None
    direction = pos["direction"]
    hit = (direction == "BUY" and ltp <= current_sl) or \
          (direction == "SELL" and ltp >= current_sl)
    if hit:
        return TradeAction(action_type="EXIT_SL", reason="STOP_LOSS")
    return None


# ---------------------------------------------------------------------------
# Rule: Trailing Stop Loss
# ---------------------------------------------------------------------------

def _check_trailing_sl(pos: dict, ltp: float, gain_pct: float, tm_cfg: dict) -> TradeAction | None:
    tsl_cfg = tm_cfg.get("trailing_sl", {})
    if not tsl_cfg.get("enabled", False):
        return None

    activation_pct = float(tsl_cfg.get("activation_pct", 1.0))
    if gain_pct < activation_pct:
        return None   # trailing not yet active

    hwm = pos["high_water_mark"]
    direction = pos["direction"]
    current_sl = pos["current_sl"]

    # Resolve trailing distance — tiered takes precedence over flat key
    tiers = tsl_cfg.get("tiers", [])
    if tiers:
        distance_pct = _resolve_tsl_tier_distance(gain_pct, tiers)
    else:
        distance_pct = float(tsl_cfg.get("distance_pct", 0.5))

    if direction == "BUY":
        new_sl = hwm * (1.0 - distance_pct / 100.0)
    else:
        new_sl = hwm * (1.0 + distance_pct / 100.0)

    new_sl = round(new_sl, 2)

    if sl_improves(direction, new_sl, current_sl):
        return TradeAction(
            action_type="TRAILING_SL",
            new_sl=new_sl,
            reason=f"Trailing SL: {distance_pct}% behind HWM {hwm:.2f}",
        )
    return None


def _resolve_tsl_tier_distance(gain_pct: float, tiers: list) -> float:
    """
    Return the trailing distance_pct for the highest applicable tier.

    Tiers are expected as a list of dicts:
        [{"min_gain_pct": 1.0, "distance_pct": 0.8},
         {"min_gain_pct": 2.0, "distance_pct": 0.5},
         {"min_gain_pct": 3.0, "distance_pct": 0.3}]

    The highest tier whose min_gain_pct <= current gain_pct is selected,
    giving a tighter trail as the trade becomes more profitable.
    """
    active_distance = float(tiers[0].get("distance_pct", 0.5))
    for tier in tiers:
        if gain_pct >= float(tier.get("min_gain_pct", 0.0)):
            active_distance = float(tier.get("distance_pct", active_distance))
    return active_distance


# ---------------------------------------------------------------------------
# Rule: Profit Lock (multi-tier)
# ---------------------------------------------------------------------------

def _check_profit_lock(pos: dict, ltp: float, gain_pct: float, tm_cfg: dict) -> TradeAction | None:
    pl_cfg = tm_cfg.get("profit_lock", {})
    if not pl_cfg.get("enabled", False):
        return None

    direction = pos["direction"]
    entry = float(pos["entry_price"])
    hwm = pos["high_water_mark"]
    current_sl = pos["current_sl"]

    # ---- Multi-tier mode ----
    tiers = pl_cfg.get("tiers", [])
    if tiers:
        # Determine the highest tier that has fired vs. what's newly eligible
        locked_tier = int(pos.get("profit_lock_tier", 0))   # DB persisted
        best_new_sl = None
        next_tier_idx = locked_tier  # we only process tiers above what's already locked

        for idx, tier in enumerate(tiers):
            if idx < locked_tier:
                continue   # already applied
            threshold = float(tier.get("threshold_pct", 9999))
            if gain_pct >= threshold:
                lock_fraction = float(tier.get("lock_fraction", 0.5))
                if direction == "BUY":
                    candidate_sl = round(entry + (hwm - entry) * lock_fraction, 2)
                else:
                    candidate_sl = round(entry - (entry - hwm) * lock_fraction, 2)

                if sl_improves(direction, candidate_sl, current_sl):
                    if best_new_sl is None or sl_improves(direction, candidate_sl, best_new_sl):
                        best_new_sl = candidate_sl
                        next_tier_idx = idx + 1  # mark next unfired tier index

        if best_new_sl is not None:
            return TradeAction(
                action_type="PROFIT_LOCK",
                new_sl=best_new_sl,
                tier_index=next_tier_idx,
                reason=f"Profit lock tier {next_tier_idx} @ gain {gain_pct:.2f}%",
            )
        return None

    # ---- Legacy flat key mode ----
    if pos.get("profit_locked", 0):
        return None   # already fired once

    threshold = float(pl_cfg.get("threshold_pct", 1.5))
    if gain_pct < threshold:
        return None

    lock_fraction = float(pl_cfg.get("lock_fraction", 0.5))
    if direction == "BUY":
        candidate_sl = round(entry + (hwm - entry) * lock_fraction, 2)
    else:
        candidate_sl = round(entry - (entry - hwm) * lock_fraction, 2)

    if sl_improves(direction, candidate_sl, current_sl):
        return TradeAction(
            action_type="PROFIT_LOCK",
            new_sl=candidate_sl,
            tier_index=1,
            reason=f"Profit locked at threshold {threshold}%",
        )
    return None


# ---------------------------------------------------------------------------
# Rule: Partial Exit (multi-tier)
# ---------------------------------------------------------------------------

def _check_partial_exit(pos: dict, ltp: float, gain_pct: float, tm_cfg: dict) -> TradeAction | None:
    pe_cfg = tm_cfg.get("partial_exit", {})
    if not pe_cfg.get("enabled", False):
        return None
    if pos.get("quantity", 0) <= 0:
        return None

    # ---- Multi-tier mode ----
    tiers = pe_cfg.get("tiers", [])
    if tiers:
        current_tier = int(pos.get("partial_exit_tier", 0))
        if current_tier >= len(tiers):
            return None   # all tiers exhausted

        tier = tiers[current_tier]
        trigger_pct = float(tier.get("trigger_pct", 9999))
        if gain_pct < trigger_pct:
            return None

        qty_fraction = float(tier.get("exit_qty_fraction", 0.5))
        exit_qty = max(1, int(pos["quantity"] * qty_fraction))
        # Guard: a "partial" exit must never close the entire remaining position.
        # If the requested fraction would leave zero shares, either skip (when
        # only 1 share remains — nothing meaningful to scale out of) or clamp
        # to `quantity - 1` so at least one share survives for later rules.
        if exit_qty >= pos["quantity"]:
            if pos["quantity"] <= 1:
                return None
            exit_qty = pos["quantity"] - 1
        if exit_qty <= 0:
            return None

        return TradeAction(
            action_type="PARTIAL_EXIT",
            exit_qty=exit_qty,
            tier_index=current_tier,
            reason=f"Partial exit tier {current_tier + 1} @ gain {gain_pct:.2f}%",
        )

    # ---- Legacy flat key mode ----
    if pos.get("partial_exit_done", 0):
        return None

    trigger_pct = float(pe_cfg.get("target1_pct", 1.0))
    if gain_pct < trigger_pct:
        return None

    qty_fraction = float(pe_cfg.get("exit_qty_fraction", 0.5))
    exit_qty = max(1, int(pos["quantity"] * qty_fraction))
    # Same guard as multi-tier: never let a "partial" swallow the whole position.
    if exit_qty >= pos["quantity"]:
        if pos["quantity"] <= 1:
            return None
        exit_qty = pos["quantity"] - 1
    if exit_qty <= 0:
        return None

    return TradeAction(
        action_type="PARTIAL_EXIT",
        exit_qty=exit_qty,
        tier_index=0,
        reason=f"Partial exit @ gain {gain_pct:.2f}%",
    )
