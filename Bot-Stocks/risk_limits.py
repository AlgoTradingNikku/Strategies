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
  daily_loss_stop_rupees:        -500          # absolute ₹ floor (optional)

  # Sprint 2: risk-based sizing
  capital:                       100000        # ₹ deployable per idea
  capital_allow_unlimited:       false         # skip 10k-10L bounds check
  sizing_mode:                   "risk_based"  # "risk_based" | "capital_pct" | "legacy"
  risk_per_trade_pct:            1.0           # % of capital risked per trade

  # Sprint 3: grade multipliers + portfolio exposure cap
  grade_multiplier_enabled:      false         # scale risk% by signal grade
  grade_multipliers:                           # capped at 3.0x
    A: 1.5
    B: 1.0
    C: 0.75
    D: 0.5
  max_portfolio_exposure_pct:    300           # cap total open entry-notional at
                                               # 3x per-trade capital. null → off.

The daily-loss check sums ``pnl_pct`` for positions closed since local
midnight; when the running total drops below ``daily_loss_stop_pct`` the
gate rejects further trades for the remainder of the trading day.

Sizing modes
------------
  * ``risk_based``  : qty = floor((capital × risk_per_trade_pct/100) / |entry - sl|)
                      This is classic fixed-fractional. Each trade risks at
                      most ``risk_per_trade_pct`` of capital regardless of SL
                      distance. Preferred mode.
  * ``capital_pct`` : qty = floor(capital / entry). Deploy the whole capital
                      per trade; risk depends on SL width. Legacy behaviour.
  * ``legacy``      : Uses ``openalgo.capital_per_trade`` / ``order_quantity``
                      as before Sprint 2.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

import trade_db

log = logging.getLogger("UTBotSRChannelsScanner")

# Capital sanity bounds (user-specified range: ₹10k–₹10L).
# These are WARNING-only unless ``capital_allow_unlimited`` is False and the
# value falls outside — then we clamp back to the nearest bound with a warn.
_CAPITAL_MIN = 10_000.0
_CAPITAL_MAX = 10_00_000.0


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

    # ---- 3. Daily realized-loss cutoff (percentage) -----------------------
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

    # ---- 4. Daily realized-loss cutoff (absolute ₹) -----------------------
    #      Optional Sprint-2 addition. Useful when the user wants a floor
    #      like "never lose more than ₹500 in a day" independent of %.
    dls_rs = lim.get("daily_loss_stop_rupees")
    if isinstance(dls_rs, (int, float)):
        try:
            midnight = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).strftime("%Y-%m-%d %H:%M:%S")
            realised_rs = trade_db.get_realized_pnl_rupees_since(midnight)
            if realised_rs <= float(dls_rs):
                return False, (
                    f"daily_loss_stop_rupees hit "
                    f"(today's realized ₹{realised_rs:+.2f} "
                    f"≤ cutoff ₹{float(dls_rs):+.2f})"
                )
        except Exception as exc:      # pragma: no cover
            log.debug("risk_limits: ₹ daily-loss check failed (%s) — allowing.", exc)

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


# ---------------------------------------------------------------------------
# Sprint 2: Capital validation + Risk-based sizing
# ---------------------------------------------------------------------------

def validate_capital(config: dict) -> float:
    """Return the effective capital-per-trade in ₹, clamped to sane bounds.

    Reads ``risk_limits.capital`` (falls back to ``openalgo.capital_per_trade``
    for backwards compatibility, then to a hard-coded default of ₹1,00,000).

    Bounds
    ------
    * If ``capital_allow_unlimited: true`` — no clamping, just log a warning
      when the user goes above ₹10L (bold move, we honour it).
    * Otherwise, values outside ₹10k–₹10L are clamped to the nearest bound
      and a warning is logged. This protects against config typos (e.g.
      dropping a zero and ending up sizing on ₹1k).
    """
    lim = _limits(config)
    cap = lim.get("capital")

    # Legacy fallback path — old configs used openalgo.capital_per_trade.
    if cap is None:
        cap = (config.get("openalgo", {}) or {}).get("capital_per_trade")

    try:
        cap_f = float(cap) if cap is not None else 1_00_000.0
    except (TypeError, ValueError):
        log.warning(
            "risk_limits: capital=%r is not numeric — defaulting to ₹1,00,000",
            cap,
        )
        return 1_00_000.0

    if cap_f <= 0:
        log.warning(
            "risk_limits: capital=%.2f is non-positive — defaulting to ₹1,00,000",
            cap_f,
        )
        return 1_00_000.0

    allow_unlimited = bool(lim.get("capital_allow_unlimited", False))

    if allow_unlimited:
        if cap_f > _CAPITAL_MAX:
            log.warning(
                "risk_limits: capital=₹%.0f exceeds ₹10L — proceeding "
                "because capital_allow_unlimited=true. Ensure your broker "
                "and margin can support this.",
                cap_f,
            )
        return cap_f

    # Bounded mode — clamp to [10k, 10L].
    if cap_f < _CAPITAL_MIN:
        log.warning(
            "risk_limits: capital=₹%.0f below minimum ₹%.0f — clamping. "
            "Set capital_allow_unlimited=true to override.",
            cap_f, _CAPITAL_MIN,
        )
        return _CAPITAL_MIN
    if cap_f > _CAPITAL_MAX:
        log.warning(
            "risk_limits: capital=₹%.0f above maximum ₹%.0f — clamping. "
            "Set capital_allow_unlimited=true to override.",
            cap_f, _CAPITAL_MAX,
        )
        return _CAPITAL_MAX

    return cap_f



# ---------------------------------------------------------------------------
# Sprint 3: Grade-based risk multiplier
# ---------------------------------------------------------------------------

# Conservative defaults: an A-grade setup gets 1.5x the base risk, a D-grade
# gets 0.5x. Note these SCALE ``risk_per_trade_pct`` — with the default 1% base
# an A trade risks 1.5% and a D trade 0.5%. The multiplier is OFF by default
# (``grade_multiplier_enabled: false``) so sizing behaviour is unchanged until
# an operator has enough by-grade win-rate data to justify it.
_DEFAULT_GRADE_MULTIPLIERS: dict[str, float] = {
    "A": 1.5,
    "B": 1.0,
    "C": 0.75,
    "D": 0.5,
}

# Hard ceiling on the multiplier regardless of config. Protects against a typo
# like ``A: 15`` (meant 1.5) turning a 1% risk into a 15% risk.
_MULTIPLIER_MAX = 3.0


def get_grade_multiplier(grade: Optional[str], config: dict) -> float:
    """Return the risk multiplier for *grade*.

    Returns 1.0 (no change) when:
      * ``risk_limits.grade_multiplier_enabled`` is false (the default), or
      * *grade* is None / unrecognised (fail-neutral).

    Values are clamped to (0, ``_MULTIPLIER_MAX``]. A configured value of 0 or
    below is rejected with a warning and treated as 1.0 — if you want to stop
    trading a grade entirely, use ``signal_grading.min_grade_to_trade`` rather
    than a zero multiplier, so the intent is explicit in the logs.
    """
    lim = _limits(config)
    if not lim.get("grade_multiplier_enabled", False):
        return 1.0

    if not grade:
        return 1.0

    g = str(grade).strip().upper()
    table = dict(_DEFAULT_GRADE_MULTIPLIERS)

    user = lim.get("grade_multipliers") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            key = str(k).strip().upper()
            if key not in _DEFAULT_GRADE_MULTIPLIERS:
                continue
            try:
                table[key] = float(v)
            except (TypeError, ValueError):
                log.warning(
                    "risk_limits: grade_multipliers[%s]=%r not numeric — using default %.2f",
                    key, v, _DEFAULT_GRADE_MULTIPLIERS[key],
                )

    mult = table.get(g)
    if mult is None:
        log.debug("risk_limits: no multiplier for grade %r — using 1.0", grade)
        return 1.0

    if mult <= 0:
        log.warning(
            "risk_limits: grade_multipliers[%s]=%.3f is non-positive — using 1.0. "
            "Use signal_grading.min_grade_to_trade to block a grade entirely.",
            g, mult,
        )
        return 1.0

    if mult > _MULTIPLIER_MAX:
        log.warning(
            "risk_limits: grade_multipliers[%s]=%.3f exceeds cap %.1f — clamping.",
            g, mult, _MULTIPLIER_MAX,
        )
        return _MULTIPLIER_MAX

    return mult


def compute_quantity_risk_based(
    entry_price: float,
    stop_loss: float,
    config: dict,
    fallback_qty: int = 1,
    grade: Optional[str] = None,
) -> dict:
    """Compute position size using fixed-fractional (risk-based) sizing.

    Formula
    -------
        risk_budget = capital × (risk_per_trade_pct / 100)
        qty         = floor(risk_budget / |entry - stop_loss|)

    Guarantees that a single stop-out costs at most ``risk_per_trade_pct``
    of ``capital`` regardless of how wide the stop is. This is the
    industry-standard sizing rule for discretionary equity trading.

    Returns
    -------
    dict with keys:
        quantity      : int — final quantity (>= 0). 0 means "skip this trade".
        risk_amount   : float — ₹ risked on this trade (qty × per-unit risk).
        risk_pct      : float — actual % of capital risked (may differ slightly
                        from configured pct due to integer rounding).
        capital       : float — effective capital used for sizing.
        mode          : str — sizing mode actually applied.
        reason        : str — "OK" on success, or an explanation on 0-qty.

    Fallback behaviour
    ------------------
    * ``sizing_mode: capital_pct`` — qty = floor(capital/entry). Deploys the
       whole capital; per-trade risk floats with SL width.
    * ``sizing_mode: legacy``     — delegates to ``compute_quantity()``.
    * Missing/invalid SL, or entry==SL — falls back to capital_pct sizing
       with a warning (can't risk-size without a valid SL).
    """
    lim = _limits(config)
    mode = str(lim.get("sizing_mode", "legacy")).lower()

    # Legacy path — preserve pre-Sprint-2 behaviour exactly.
    if mode == "legacy":
        qty = compute_quantity(entry_price, config, fallback_qty=fallback_qty)
        return {
            "quantity": qty,
            "risk_amount": 0.0,
            "risk_pct": 0.0,
            "capital": validate_capital(config),
            "mode": "legacy",
            "reason": "OK",
        }

    capital = validate_capital(config)

    # capital_pct — old "deploy the whole capital" behaviour, but bounded.
    if mode == "capital_pct":
        if entry_price <= 0:
            return {
                "quantity": max(1, fallback_qty), "risk_amount": 0.0,
                "risk_pct": 0.0, "capital": capital, "mode": "capital_pct",
                "reason": "INVALID_ENTRY_PRICE",
            }
        qty = max(1, int(capital // entry_price))
        return {
            "quantity": qty,
            "risk_amount": 0.0,   # unknown without SL
            "risk_pct": 0.0,
            "capital": capital,
            "mode": "capital_pct",
            "reason": "OK",
        }

    # risk_based — the recommended mode.
    if entry_price <= 0:
        return {
            "quantity": 0, "risk_amount": 0.0, "risk_pct": 0.0,
            "capital": capital, "mode": "risk_based",
            "reason": "INVALID_ENTRY_PRICE",
        }

    risk_per_unit = abs(float(entry_price) - float(stop_loss or 0.0))
    if risk_per_unit <= 0:
        # No usable SL → degrade to capital_pct with a warning.
        log.warning(
            "risk_limits: entry=%.2f == stop_loss=%.2f, cannot risk-size — "
            "falling back to capital_pct.",
            entry_price, stop_loss,
        )
        qty = max(1, int(capital // entry_price))
        return {
            "quantity": qty, "risk_amount": 0.0, "risk_pct": 0.0,
            "capital": capital, "mode": "risk_based_fallback_capital_pct",
            "reason": "SL_EQUALS_ENTRY",
        }

    try:
        risk_pct = float(lim.get("risk_per_trade_pct", 1.0))
    except (TypeError, ValueError):
        risk_pct = 1.0

    if risk_pct <= 0:
        return {
            "quantity": 0, "risk_amount": 0.0, "risk_pct": 0.0,
            "capital": capital, "mode": "risk_based",
            "reason": "RISK_PCT_NON_POSITIVE",
        }

    # ---- Sprint 3: grade multiplier ---------------------------------------
    # Scales the risk budget by signal conviction. Returns 1.0 unless
    # ``grade_multiplier_enabled`` is true, so this is a no-op by default.
    grade_mult = get_grade_multiplier(grade, config)
    effective_risk_pct = risk_pct * grade_mult

    risk_budget = capital * (effective_risk_pct / 100.0)
    raw_qty = risk_budget / risk_per_unit
    qty = int(raw_qty)   # floor — never over-risk

    if qty < 1:
        return {
            "quantity": 0,
            "risk_amount": 0.0,
            "risk_pct": 0.0,
            "capital": capital,
            "mode": "risk_based",
            "grade": grade,
            "grade_multiplier": grade_mult,
            "reason": (
                f"RISK_BUDGET_TOO_SMALL "
                f"(budget=₹{risk_budget:.2f}, per-share risk=₹{risk_per_unit:.2f})"
            ),
        }

    # Also guard against notional > capital (wide SL + high price case).
    notional = qty * entry_price
    if notional > capital and not lim.get("capital_allow_unlimited", False):
        qty = max(0, int(capital // entry_price))
        if qty < 1:
            return {
                "quantity": 0, "risk_amount": 0.0, "risk_pct": 0.0,
                "capital": capital, "mode": "risk_based",
                "grade": grade, "grade_multiplier": grade_mult,
                "reason": f"NOTIONAL_EXCEEDS_CAPITAL (share ₹{entry_price:.2f} > capital ₹{capital:.0f})",
            }

    actual_risk_amount = qty * risk_per_unit
    actual_risk_pct = (actual_risk_amount / capital) * 100.0 if capital > 0 else 0.0

    return {
        "quantity": qty,
        "risk_amount": round(actual_risk_amount, 2),
        "risk_pct": round(actual_risk_pct, 3),
        "capital": capital,
        "mode": "risk_based",
        "grade": grade,
        "grade_multiplier": grade_mult,
        "base_risk_pct": round(risk_pct, 3),
        "effective_risk_pct": round(effective_risk_pct, 3),
        "reason": "OK",
    }



# ---------------------------------------------------------------------------
# Sprint 3: Portfolio-level exposure cap
# ---------------------------------------------------------------------------

def compute_portfolio_exposure(
    config: dict,
    open_positions: Optional[list[dict]] = None,
) -> dict:
    """Return current open-notional exposure against the exposure budget.

    Exposure is measured as **entry notional** (``quantity × entry_price``)
    summed across open positions, not mark-to-market value. Entry notional is
    the deterministic figure — it needs no live quotes, so this check can run
    inside the scanner's hot loop without extra broker calls.

    The budget is ``capital × max_portfolio_exposure_pct / 100``. Note
    ``validate_capital`` returns the *per-trade* capital; with the default
    ``max_portfolio_exposure_pct: 300`` the bot may hold up to 3× that in open
    notional, i.e. roughly three fully-deployed ideas at once.

    Returns
    -------
    dict
        exposure_rupees : float — current open entry notional
        budget_rupees   : float — cap (0.0 when the check is disabled)
        exposure_pct    : float — exposure as % of per-trade capital
        max_pct         : float | None — configured cap, None when disabled
        positions       : int
        enabled         : bool
    """
    lim = _limits(config)
    max_pct_raw = lim.get("max_portfolio_exposure_pct")

    try:
        max_pct = float(max_pct_raw) if max_pct_raw is not None else None
    except (TypeError, ValueError):
        log.warning(
            "risk_limits: max_portfolio_exposure_pct=%r not numeric — check disabled.",
            max_pct_raw,
        )
        max_pct = None

    if open_positions is None:
        try:
            open_positions = trade_db.get_open_positions()
        except Exception as exc:
            log.warning("risk_limits: exposure DB read failed (%s).", exc)
            open_positions = []

    exposure = 0.0
    for p in (open_positions or []):
        try:
            qty = float(p.get("quantity") or 0.0)
            entry = float(p.get("entry_price") or 0.0)
            if qty > 0 and entry > 0:
                exposure += qty * entry
        except (TypeError, ValueError):
            # A malformed row must not abort the whole calculation.
            continue

    capital = validate_capital(config)
    budget = capital * (max_pct / 100.0) if (max_pct is not None and max_pct > 0) else 0.0

    return {
        "exposure_rupees": round(exposure, 2),
        "budget_rupees": round(budget, 2),
        "exposure_pct": round(exposure / capital * 100.0, 2) if capital > 0 else 0.0,
        "max_pct": max_pct,
        "positions": len(open_positions or []),
        "enabled": bool(max_pct is not None and max_pct > 0),
    }


def check_portfolio_exposure(
    config: dict,
    new_notional: float = 0.0,
    open_positions: Optional[list[dict]] = None,
) -> tuple[bool, str]:
    """Would adding *new_notional* breach the portfolio exposure cap?

    Returns
    -------
    (ok, reason) : tuple[bool, str]
        Same contract as ``check_can_open_new`` / ``regime_gate.check_signal_allowed``.

    Disabled (always passes) unless ``risk_limits.enabled`` is true AND
    ``max_portfolio_exposure_pct`` is set to a positive number. This mirrors
    every other limit in the module: absent config means no behaviour change.

    Why this exists alongside ``max_concurrent_positions``
    -----------------------------------------------------
    A position count treats a ₹5,000 trade and a ₹2,00,000 trade identically.
    Once grade multipliers start varying size by up to 3×, count-based caps stop
    bounding real capital at risk. This gate bounds the rupee figure directly.
    """
    lim = _limits(config)
    if not lim.get("enabled", False):
        return True, ""

    snap = compute_portfolio_exposure(config, open_positions)
    if not snap["enabled"]:
        return True, ""

    try:
        add = max(0.0, float(new_notional or 0.0))
    except (TypeError, ValueError):
        add = 0.0

    projected = snap["exposure_rupees"] + add
    budget = snap["budget_rupees"]

    if projected > budget:
        return False, (
            f"portfolio exposure cap: ₹{projected:,.0f} projected "
            f"(open ₹{snap['exposure_rupees']:,.0f} + new ₹{add:,.0f}) "
            f"exceeds ₹{budget:,.0f} ({snap['max_pct']:.0f}% of ₹{validate_capital(config):,.0f})"
        )

    return True, ""

