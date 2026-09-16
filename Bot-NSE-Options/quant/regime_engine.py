"""
quant/regime_engine.py
=======================
Deterministic market regime classification engine.
Produces MarketRegime from pre-computed quant signals.
The LLM receives this regime as context — it does NOT compute the regime.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import MarketDataModel, TechnicalData, VolatilityData, OISignals
from ai.schemas.regime import MarketRegime, TimeframeAlignment
from ai.schemas.settings import PlatformSettings

log = logging.getLogger("UTBotSRChannelsScanner")


def classify_regime(
    market_data: MarketDataModel,
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> MarketRegime:
    """
    Classify the current market regime from pre-computed market data.
    Returns MarketRegime. Fail-open — returns NEUTRAL regime on any error.
    This is the most important function: it can output NO_TRADE.
    """
    if settings and not settings.quant_regime_engine:
        log.debug("[Regime] quant_regime_engine toggle is OFF — returning NEUTRAL")
        return _neutral_regime()

    try:
        regime_cfg = cfg.get("regime", {})
        tech = market_data.technicals
        vol = market_data.volatility
        oi = market_data.oi_signals

        # ── Trend alignment ───────────────────────────────────────
        tf_alignment = _compute_tf_alignment(tech)
        trend_strength = _compute_trend_strength(tech, tf_alignment)
        trend_direction = _classify_trend_direction(trend_strength)

        # ── Volatility regime ─────────────────────────────────────
        vol_regime = vol.vix_regime if settings is None or settings.quant_vix_regime else "NORMAL"
        iv_regime = vol.iv_regime

        # ── No-trade detection (Rule 13: system must say NO TRADE) ─
        no_trade, no_trade_reason = _check_no_trade_conditions(
            tech, vol, oi, tf_alignment, regime_cfg, settings
        )

        if no_trade:
            log.info("[Regime] NO_TRADE: %s", no_trade_reason)
            return MarketRegime(
                regime="NO_TRADE",
                trend_direction=trend_direction,
                trend_strength=trend_strength,
                timeframe_alignment=tf_alignment,
                vol_regime=vol_regime,
                iv_regime=iv_regime,
                price_above_vwap=tech.price_vs_vwap == "ABOVE",
                price_above_ema20=tech.price_vs_ema20 == "ABOVE",
                price_above_ema50=tech.price_vs_ema50 == "ABOVE",
                no_trade=True,
                no_trade_reason=no_trade_reason,
                regime_confidence=0,
                favors_selling=False,
                favors_buying=False,
                favors_neutral=False,
            )

        # ── Primary regime classification ─────────────────────────
        strong_bull = float(regime_cfg.get("strong_bullish_threshold", 75))
        bull = float(regime_cfg.get("bullish_threshold", 55))
        bear = float(regime_cfg.get("bearish_threshold", 45))
        strong_bear = float(regime_cfg.get("strong_bearish_threshold", 25))

        if vol_regime == "HIGH":
            regime = "HIGH_VOL"
        elif trend_strength >= strong_bull:
            regime = "STRONG_BULLISH"
        elif trend_strength >= bull:
            regime = "BULLISH"
        elif trend_strength <= strong_bear:
            regime = "STRONG_BEARISH"
        elif trend_strength <= bear:
            regime = "BEARISH"
        else:
            regime = "NEUTRAL"

        # Strategy suitability hints
        favors_selling = iv_regime == "HIGH" and regime in ("NEUTRAL", "BULLISH", "BEARISH")
        favors_buying = iv_regime == "LOW" and regime in ("STRONG_BULLISH", "STRONG_BEARISH")
        favors_neutral = regime == "NEUTRAL" and vol_regime != "HIGH"

        confidence = _compute_confidence(trend_strength, tf_alignment, vol)

        return MarketRegime(
            regime=regime,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            timeframe_alignment=tf_alignment,
            vol_regime=vol_regime,
            iv_regime=iv_regime,
            price_above_vwap=tech.price_vs_vwap == "ABOVE",
            price_above_ema20=tech.price_vs_ema20 == "ABOVE",
            price_above_ema50=tech.price_vs_ema50 == "ABOVE",
            no_trade=False,
            no_trade_reason="",
            regime_confidence=confidence,
            favors_selling=favors_selling,
            favors_buying=favors_buying,
            favors_neutral=favors_neutral,
        )

    except Exception as exc:
        log.warning("[Regime] classify_regime failed: %s — returning NEUTRAL", exc)
        return _neutral_regime()


# ── helpers ──────────────────────────────────────────────────────────────────

def _compute_tf_alignment(tech: TechnicalData) -> TimeframeAlignment:
    trends = [tech.trend_5m, tech.trend_15m, tech.trend_1h, tech.trend_day]
    bullish_count = trends.count("BULLISH")
    bearish_count = trends.count("BEARISH")
    if bullish_count >= 3:
        aligned, direction, score = True, "BULLISH", bullish_count
    elif bearish_count >= 3:
        aligned, direction, score = True, "BEARISH", bearish_count
    else:
        aligned, direction, score = False, "NEUTRAL", max(bullish_count, bearish_count)
    return TimeframeAlignment(
        tf_5m=tech.trend_5m,
        tf_15m=tech.trend_15m,
        tf_1h=tech.trend_1h,
        tf_day=tech.trend_day,
        aligned=aligned,
        aligned_direction=direction,
        alignment_score=score,
    )


def _compute_trend_strength(tech: TechnicalData, tf_alignment: TimeframeAlignment) -> int:
    """Composite trend strength 0-100."""
    score = 0
    # Price vs EMAs (20 points each)
    if tech.price_vs_ema20 == "ABOVE":
        score += 20
    if tech.price_vs_ema50 == "ABOVE":
        score += 20
    # Price vs VWAP (15 points)
    if tech.price_vs_vwap == "ABOVE":
        score += 15
    # RSI momentum (15 points)
    if tech.rsi14 > 60:
        score += 15
    elif tech.rsi14 > 50:
        score += 8
    elif tech.rsi14 < 40:
        score -= 15
    elif tech.rsi14 < 50:
        score -= 8
    # TF alignment bonus (30 points)
    score += tf_alignment.alignment_score * 7
    return max(0, min(100, score))


def _classify_trend_direction(trend_strength: int) -> str:
    if trend_strength >= 55:
        return "BULLISH"
    elif trend_strength <= 45:
        return "BEARISH"
    return "NEUTRAL"


def _check_no_trade_conditions(
    tech: TechnicalData,
    vol: VolatilityData,
    oi: OISignals,
    tf_alignment: TimeframeAlignment,
    regime_cfg: dict,
    settings: Optional[PlatformSettings],
) -> tuple[bool, str]:
    """
    Check conditions that should trigger NO_TRADE output.
    Returns (should_no_trade: bool, reason: str).
    """
    if not regime_cfg.get("no_trade_on_contradictory_signals", True):
        return False, ""

    # Severely contradictory signals: multiple timeframes in direct opposition
    bullish_tfs = sum([
        tf_alignment.tf_5m == "BULLISH",
        tf_alignment.tf_15m == "BULLISH",
        tf_alignment.tf_1h == "BULLISH",
        tf_alignment.tf_day == "BULLISH",
    ])
    bearish_tfs = sum([
        tf_alignment.tf_5m == "BEARISH",
        tf_alignment.tf_15m == "BEARISH",
        tf_alignment.tf_1h == "BEARISH",
        tf_alignment.tf_day == "BEARISH",
    ])
    if bullish_tfs >= 2 and bearish_tfs >= 2:
        return True, "Contradictory multi-timeframe signals — 2+ bullish and 2+ bearish TFs simultaneously"

    # Extreme VIX (circuit breaker condition)
    if vol.india_vix > 30:
        return True, f"Extreme VIX level ({vol.india_vix:.1f}) — market in crisis, no new strategies"

    # Very low spot data quality
    if tech.spot_ltp <= 0:
        return True, "Spot LTP unavailable or zero — data quality insufficient"

    return False, ""


def _compute_confidence(trend_strength: int, tf_alignment: TimeframeAlignment, vol: VolatilityData) -> int:
    """Compute regime confidence 0-100."""
    base = trend_strength
    # TF alignment bonus
    if tf_alignment.aligned:
        base = min(100, base + 15)
    # VIX penalty for uncertainty
    if vol.vix_regime == "HIGH":
        base = max(0, base - 20)
    return base


def _neutral_regime() -> MarketRegime:
    return MarketRegime(
        regime="NEUTRAL",
        trend_direction="NEUTRAL",
        trend_strength=50,
        timeframe_alignment=TimeframeAlignment(),
        vol_regime="NORMAL",
        iv_regime="NORMAL",
        no_trade=False,
        no_trade_reason="",
        regime_confidence=50,
    )
