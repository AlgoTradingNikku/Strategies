"""
ai/schemas/regime.py
====================
Market regime classification models.
The regime engine produces a MarketRegime deterministically before any LLM call.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class TimeframeAlignment(BaseModel):
    """Trend direction across multiple timeframes."""
    tf_5m: str = Field("NEUTRAL", description="BULLISH | BEARISH | NEUTRAL")
    tf_15m: str = Field("NEUTRAL")
    tf_1h: str = Field("NEUTRAL")
    tf_day: str = Field("NEUTRAL")
    aligned: bool = Field(False, description="True if all timeframes agree")
    aligned_direction: str = Field("NEUTRAL", description="The agreed direction if aligned")
    alignment_score: int = Field(0, description="0-4 — how many TFs agree with dominant trend")


class MarketRegime(BaseModel):
    """
    Deterministic market regime classification.
    Produced by the Python regime engine — never by the LLM.
    The LLM receives this as context, not as something to compute.
    """
    # Primary regime
    regime: str = Field(..., description="STRONG_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONG_BEARISH | HIGH_VOL | LOW_VOL | EVENT_RISK | NO_TRADE")
    trend_direction: str = Field("NEUTRAL", description="BULLISH | BEARISH | NEUTRAL")
    trend_strength: int = Field(0, description="0-100 composite trend strength score")

    # Timeframe alignment
    timeframe_alignment: TimeframeAlignment

    # Volatility regime
    vol_regime: str = Field("NORMAL", description="LOW | NORMAL | HIGH")
    iv_regime: str = Field("NORMAL", description="LOW | NORMAL | HIGH based on IV Rank")

    # Price structure
    price_above_vwap: bool = False
    price_above_ema20: bool = False
    price_above_ema50: bool = False

    # NO_TRADE gate — most important field
    no_trade: bool = Field(False, description="True means system recommends no trade this cycle")
    no_trade_reason: Optional[str] = Field("", description="Human-readable reason for NO_TRADE")

    # Confidence
    regime_confidence: int = Field(0, description="0-100 confidence in the regime classification")

    # Strategy suitability hints (derived from regime — not LLM)
    favors_selling: bool = Field(False, description="High IV + stable regime favors premium selling")
    favors_buying: bool = Field(False, description="Low IV + trending market favors buying")
    favors_neutral: bool = Field(False, description="Range-bound market favors neutral strategies")
