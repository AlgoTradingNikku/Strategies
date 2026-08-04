"""
===============================================================================
  Bot-Options / core / option_filters.py
  Option-specific signal filtering and scoring — IV scores, OI momentum,
  and time-decay penalty calculations.
===============================================================================
"""

import logging
from typing import Tuple

log = logging.getLogger(__name__)

def calculate_iv_score(
    iv: float,
    filters_cfg: dict
) -> Tuple[float, str]:
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
) -> Tuple[float, str]:
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
) -> Tuple[float, str]:
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


def calculate_candle_pattern_score(
    df,
    option_type: str,
    sr_zones: list,
    filters_cfg: dict
) -> Tuple[float, str]:
    """
    Detect reversal candle patterns on the underlying chart near S/R zones.
    Enabled via candle_patterns_enabled: true in config.

    Patterns checked (last two closed candles):
      Bullish (for CE):  Hammer, Bullish Engulfing
      Bearish (for PE):  Shooting Star, Bearish Engulfing

    A pattern is only scored positively if the candle forms within
    proximity_pct of an S/R boundary (zone relevance check).

    Parameters
    ----------
    df          : OHLCV DataFrame with at least 3 rows
    option_type : 'CE' (looking for bullish patterns) or 'PE' (bearish)
    sr_zones    : List of S/R zone dicts from compute_sr_signals()
    filters_cfg : filters block from config

    Returns
    -------
    (score_adjustment, reason_string)
    """
    if not filters_cfg.get("candle_patterns_enabled", True):
        return 0.0, ""

    if df is None or len(df) < 3:
        return 0.0, ""

    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]

        o1, h1, l1, c1 = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])
        o2, h2, l2, c2 = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])

        body1 = abs(c1 - o1)
        body2 = abs(c2 - o2)
        range2 = h2 - l2 if (h2 - l2) > 0 else 1.0

        pattern_name = ""
        is_bullish_pattern = False
        is_bearish_pattern = False

        # --- Bullish patterns (relevant for CE) ---
        # Hammer: small body in upper third, long lower wick, near S/R support
        lower_wick2 = min(o2, c2) - l2
        upper_wick2 = h2 - max(o2, c2)
        if lower_wick2 >= 2 * body2 and upper_wick2 < 0.3 * range2:
            is_bullish_pattern = True
            pattern_name = "Hammer"

        # Bullish Engulfing: current candle body fully covers previous bearish candle
        if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1:
            is_bullish_pattern = True
            pattern_name = "Bullish Engulfing"

        # --- Bearish patterns (relevant for PE) ---
        # Shooting Star: small body in lower third, long upper wick
        if upper_wick2 >= 2 * body2 and lower_wick2 < 0.3 * range2:
            is_bearish_pattern = True
            pattern_name = "Shooting Star"

        # Bearish Engulfing: current candle body fully covers previous bullish candle
        if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1:
            is_bearish_pattern = True
            pattern_name = "Bearish Engulfing"

        # --- Zone proximity check ---
        near_zone = False
        current_price = c2
        if sr_zones:
            for zone in sr_zones:
                zone_low = float(zone.get("lower", zone.get("price", current_price)))
                zone_high = float(zone.get("upper", zone.get("price", current_price)))
                proximity_pct = float(filters_cfg.get("sr_proximity_pct", 0.3)) / 100.0
                if (zone_low * (1 - proximity_pct)) <= current_price <= (zone_high * (1 + proximity_pct)):
                    near_zone = True
                    break
        else:
            near_zone = True  # No zone data — don't penalise

        if option_type == "CE" and is_bullish_pattern and near_zone:
            return 8.0, f"Bullish candle pattern detected near S/R: {pattern_name} (+8 pts)"
        if option_type == "PE" and is_bearish_pattern and near_zone:
            return 8.0, f"Bearish candle pattern detected near S/R: {pattern_name} (+8 pts)"
        if option_type == "CE" and is_bearish_pattern:
            return -5.0, f"Bearish candle pattern contradicts CE trade: {pattern_name} (-5 pts)"
        if option_type == "PE" and is_bullish_pattern:
            return -5.0, f"Bullish candle pattern contradicts PE trade: {pattern_name} (-5 pts)"

    except Exception as e:
        log.debug("Candle pattern scoring failed: %s", e)

    return 0.0, ""
