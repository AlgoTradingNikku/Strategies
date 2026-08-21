"""
===============================================================================
  position_sizer.py — Sprint 3: Dynamic Risk-Based Position Sizing
===============================================================================
Replaces the static `trading.options.quantity: 65` with dynamic sizing that
risks a controlled fraction of account equity on every trade.

Sizing modes:
  1. fixed_fractional  — qty such that (entry - SL) × qty ≤ equity × risk_pct
  2. kelly             — fractional-Kelly using rolling win-rate + payoff ratio
                         from trade_db; falls back to fixed_fractional when
                         sample size < kelly_min_trades.

All quantities snap DOWN to the exchange lot_size (e.g. 75 for NIFTY).
Below one lot → skip trade (returns 0, caller must handle).

Also exposes:
  * check_portfolio_exposure(cfg, extra_premium) — reject if adding this trade
    would push open premium × qty beyond `max_portfolio_exposure_pct` of equity
  * check_concurrent_positions(cfg) — reject if `max_concurrent_positions` hit
  * get_portfolio_snapshot(cfg) — for dashboard exposure pill

Fully backwards compatible: when `position_sizing.enabled: false`, callers can
still fall back to the legacy fixed quantity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("UTBotSRChannelsScanner")


# ============================================================================
# Core sizing helpers
# ============================================================================

def _grade_multiplier(cfg: dict, grade: str) -> float:
    ps = cfg.get("position_sizing", {})
    if not ps.get("grade_multiplier_enabled", False):
        return 1.0
    table = ps.get("grade_multipliers", {}) or {}
    return float(table.get(str(grade).upper(), 1.0))


def _floor_to_lot(qty: float, lot_size: int) -> int:
    """Snap DOWN to nearest lot-size multiple. Returns 0 if below one lot."""
    if lot_size <= 0:
        return int(max(0, qty))
    lots = int(qty // lot_size)
    return lots * lot_size


def _compute_kelly_fraction(cfg: dict) -> Optional[float]:
    """
    Rolling Kelly from closed trades in trade_db.
    Returns None when sample size insufficient (caller falls back).

    Formula: f* = (W × B - L) / B
      W = win probability, L = 1-W, B = avg_win / avg_loss (payoff ratio)
    Then multiplied by cfg.position_sizing.kelly_fraction (usually 0.25 = quarter-Kelly).
    """
    ps = cfg.get("position_sizing", {})
    min_trades = int(ps.get("kelly_min_trades", 20))
    frac = float(ps.get("kelly_fraction", 0.25))
    cap = float(ps.get("kelly_max_risk_pct", 5.0)) / 100.0

    try:
        import trade_db
        conn = trade_db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT pnl_amount FROM trades WHERE status = 'CLOSED' "
            "ORDER BY trade_id DESC LIMIT ?",
            (max(min_trades * 3, 100),),
        )
        pnls = [float(r["pnl_amount"] or 0.0) for r in cur.fetchall()]
        conn.close()
    except Exception as exc:
        log.debug("[position_sizer] kelly fetch error: %s", exc)
        return None

    if len(pnls) < min_trades:
        return None

    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    if not wins or not losses:
        return None

    W = len(wins) / len(pnls)
    L = 1.0 - W
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    if avg_loss <= 0:
        return None
    B = avg_win / avg_loss

    f_star = (W * B - L) / B
    if f_star <= 0:
        return 0.0
    return min(cap, f_star * frac)


def compute_position_size(
    cfg: dict,
    *,
    entry_price: float,
    stop_loss: float,
    lot_size: int,
    grade: str = "B",
) -> Dict[str, Any]:
    """
    Returns dict with quantity (rounded down to lot_size, 0 if below 1 lot),
    risk_amount, risk_pct, mode, reason, fallback_qty, grade_multiplier.
    """
    ps = cfg.get("position_sizing", {})
    fallback = int(cfg.get("trading", {}).get("options", {}).get("quantity", 65))
    result = {
        "quantity": fallback,
        "risk_amount": 0.0,
        "risk_pct": 0.0,
        "mode": "disabled",
        "reason": "DISABLED_FALLBACK",
        "fallback_qty": fallback,
        "grade_multiplier": 1.0,
    }

    if not ps.get("enabled", True):
        return result

    if entry_price <= 0 or stop_loss <= 0:
        result["reason"] = "INVALID_PRICES"
        result["quantity"] = 0
        return result

    risk_per_unit = abs(entry_price - stop_loss)
    if risk_per_unit <= 0:
        result["reason"] = "INVALID_STOP"
        result["quantity"] = 0
        return result

    equity = float(cfg.get("risk", {}).get("account_equity", 100000) or 0.0)
    if equity <= 0:
        result["reason"] = "ZERO_EQUITY"
        result["quantity"] = 0
        return result

    mode = str(ps.get("mode", "fixed_fractional")).lower()
    base_risk_pct = float(ps.get("risk_per_trade_pct", 1.0)) / 100.0

    kelly_pct = None
    if mode == "kelly":
        kelly_pct = _compute_kelly_fraction(cfg)
        if kelly_pct is None:
            mode = "fixed_fractional"
        else:
            base_risk_pct = kelly_pct

    grade_mult = _grade_multiplier(cfg, grade)
    effective_risk_pct = base_risk_pct * grade_mult

    # ── [Sprint-4] VIX regime multiplier ───────────────────────────────
    regime_mult = 1.0
    regime_label = "DISABLED"
    try:
        ae = cfg.get("alpha_enhancers", {})
        if ae.get("enabled", True) and ae.get("vix_regime", {}).get("enabled", True):
            import alpha_enhancers
            regime_label, _vix = alpha_enhancers.get_vix_regime(cfg)
            regime_mult = alpha_enhancers.get_regime_multiplier(cfg, regime_label)
            effective_risk_pct = effective_risk_pct * regime_mult
    except Exception:
        regime_mult = 1.0

    max_pct = float(ps.get("max_risk_per_trade_pct", 3.0)) / 100.0
    effective_risk_pct = min(effective_risk_pct, max_pct)

    risk_budget = equity * effective_risk_pct
    raw_qty = risk_budget / risk_per_unit
    qty = _floor_to_lot(raw_qty, lot_size)

    result["mode"] = mode
    result["risk_pct"] = round(effective_risk_pct * 100.0, 3)
    result["risk_amount"] = round(qty * risk_per_unit, 2)
    result["quantity"] = qty
    result["reason"] = "OK" if qty >= lot_size else "BELOW_ONE_LOT"
    if kelly_pct is not None:
        result["kelly_fraction_used"] = round(kelly_pct * 100.0, 3)
    result["grade_multiplier"] = grade_mult
    result["regime"] = regime_label
    result["regime_multiplier"] = regime_mult
    return result


# ============================================================================
# Portfolio-level gates
# ============================================================================

def get_portfolio_snapshot(cfg: dict) -> Dict[str, Any]:
    """Return current open premium exposure + concurrent position count."""
    snapshot = {
        "open_positions": 0,
        "total_premium": 0.0,
        "exposure_pct": 0.0,
        "equity": float(cfg.get("risk", {}).get("account_equity", 100000) or 0.0),
    }
    try:
        import trade_db
        rows = trade_db.get_active_trades()
        snapshot["open_positions"] = len(rows)
        total = 0.0
        for r in rows:
            entry = float(r.get("entry_price") or 0.0)
            qty = int(r.get("quantity") or 0)
            total += entry * qty
        snapshot["total_premium"] = total
        if snapshot["equity"] > 0:
            snapshot["exposure_pct"] = (total / snapshot["equity"]) * 100.0
    except Exception as exc:
        log.debug("[position_sizer] snapshot error: %s", exc)
    return snapshot


def check_concurrent_positions(cfg: dict) -> Tuple[bool, str]:
    ps = cfg.get("position_sizing", {})
    if not ps.get("enabled", True):
        return True, ""
    cap = int(ps.get("max_concurrent_positions", 3))
    if cap <= 0:
        return True, ""
    snap = get_portfolio_snapshot(cfg)
    if snap["open_positions"] >= cap:
        return False, f"MAX_CONCURRENT_POSITIONS({snap['open_positions']}>={cap})"
    return True, ""


def check_portfolio_exposure(cfg: dict, extra_premium: float = 0.0) -> Tuple[bool, str]:
    """
    Rejects when open premium + extra_premium exceeds
    max_portfolio_exposure_pct of equity.
    """
    ps = cfg.get("position_sizing", {})
    if not ps.get("enabled", True):
        return True, ""
    cap_pct = float(ps.get("max_portfolio_exposure_pct", 15.0))
    if cap_pct <= 0:
        return True, ""
    snap = get_portfolio_snapshot(cfg)
    if snap["equity"] <= 0:
        return True, ""
    projected = (snap["total_premium"] + max(0.0, extra_premium)) / snap["equity"] * 100.0
    if projected > cap_pct:
        return False, f"PORTFOLIO_EXPOSURE_CAP({projected:.1f}%>{cap_pct:.1f}%)"
    return True, ""

