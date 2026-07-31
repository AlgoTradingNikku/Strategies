"""
===============================================================================
  Bot-Options / core / option_filters.py
  Option-specific signal filtering and scoring — IV scores, OI momentum,
  and time-decay penalty calculations.
===============================================================================
"""

import logging
from typing import tuple

log = logging.getLogger(__name__)

def calculate_iv_score(
    iv: float,
    filters_cfg: dict
) -> tuple[float, str]:
    """
    Score the implied volatility. High IV is bad for buying options (overpriced),
    low IV is favorable (cheap premiums).

    Returns
    -------
    (score_adjustment, reason_string)
    """
    if not filters_cfg.get("iv_score_enabled", True):
        return 0.0, ""

    # Safe float conversion
    try:
        iv_val = float(iv)
    except (ValueError, TypeError):
        return 0.0, ""

    if iv_val <= 0.0:
        return 0.0, ""

    # Thresholds (India VIX / IV references)
    if iv_val > 25.0:
        return -15.0, f"Implied Volatility very high ({iv_val:.1f}%) — Buying is expensive (-15 pts)"
    elif iv_val > 20.0:
        return -5.0, f"Implied Volatility elevated ({iv_val:.1f}%) — Mild buying penalty (-5 pts)"
    elif iv_val < 12.0:
        return 5.0, f"Implied Volatility cheap ({iv_val:.1f}%) — Favorable for buying (+5 pts)"

    return 0.0, ""


def calculate_oi_momentum_score(
    current_oi: int,
    prev_oi: int,
    price_change_pct: float,
    filters_cfg: dict
) -> tuple[float, str]:
    """
    Evaluate OI Momentum.
    - Rising OI + Rising Price = Long Buildup (Very Bullish for premium)
    - Rising OI + Falling Price = Short Buildup (Bearish)
    """
    if not filters_cfg.get("oi_momentum_score_enabled", True):
        return 0.0, ""

    if not current_oi or not prev_oi or prev_oi <= 0:
        return 0.0, ""

    oi_change_pct = ((current_oi - prev_oi) / prev_oi) * 100.0

    # Conviction thresholds
    if oi_change_pct > 5.0 and price_change_pct > 2.0:
        return 10.0, f"Significant Long Buildup: OI up {oi_change_pct:.1f}% with rising premium (+10 pts)"
    elif oi_change_pct > 5.0 and price_change_pct < -2.0:
        return -10.0, f"Short Buildup detected: OI up {oi_change_pct:.1f}% on falling premium (-10 pts)"
    
    return 0.0, ""


def calculate_time_decay_penalty(
    days_left: int,
    filters_cfg: dict
) -> tuple[float, str]:
    """
    Time Decay (Theta) Penalty.
    Buying options close to expiry faces rapid time decay and high zero-value risk.
    """
    if not filters_cfg.get("time_decay_penalty_enabled", True):
        return 0.0, ""

    threshold = int(filters_cfg.get("time_decay_threshold_days", 3))
    
    if days_left < 0:
        return -100.0, "Option is already expired (-100 pts)"
    elif days_left == 0:
        return -35.0, "Expiry day today! Extremely high theta decay / zero-value risk (-35 pts)"
    elif days_left == 1:
        return -20.0, "Only 1 day left to expiry. High theta decay (-20 pts)"
    elif days_left < threshold:
        return -10.0, f"Close to expiry ({days_left} days left). Accelerating theta decay (-10 pts)"

    return 0.0, ""
