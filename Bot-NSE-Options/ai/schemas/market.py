"""
ai/schemas/market.py
====================
Canonical market data models for the AI Options Strategy Platform.
These are the authoritative data shapes — quant engine writes them,
LLM receives only pre-computed versions, never raw chain data.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class OptionLeg(BaseModel):
    """A single option contract leg (one row in an option chain strike)."""
    symbol: str = Field(..., description="Full OpenAlgo symbol e.g. NIFTY24JUL2524900CE")
    ltp: float = Field(0.0, description="Last traded price")
    bid: float = Field(0.0, description="Best bid price")
    ask: float = Field(0.0, description="Best ask price")
    volume: int = Field(0, description="Day volume")
    oi: int = Field(0, description="Open interest")
    oi_change: int = Field(0, description="Change in OI from previous session")
    iv: float = Field(0.0, description="Implied volatility (from optiongreeks API)")
    delta: float = Field(0.0, description="Delta Greek")
    gamma: float = Field(0.0, description="Gamma Greek")
    theta: float = Field(0.0, description="Theta Greek (daily decay)")
    vega: float = Field(0.0, description="Vega Greek")
    lot_size: int = Field(0, description="Contract lot size")
    label: str = Field("", description="ATM/ITM1/OTM2 etc from chain response")


class OptionStrike(BaseModel):
    """One row in the option chain — a strike with CE and PE legs."""
    strike: float
    ce: Optional[OptionLeg] = None
    pe: Optional[OptionLeg] = None


class OptionChainSnapshot(BaseModel):
    """
    The atomic unit of analysis — a frozen, timestamped option chain.
    Fetched once per analysis cycle. All downstream components use this
    snapshot; they never call the broker API directly.
    """
    underlying: str
    exchange: str
    expiry_date: str
    underlying_ltp: float
    atm_strike: float
    chain: list[OptionStrike]
    fetched_at: datetime = Field(default_factory=datetime.now)
    freshness_status: str = Field("unknown", description="fresh | stale | expired")

    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.fetched_at).total_seconds()

    def get_strike(self, strike: float) -> Optional[OptionStrike]:
        for s in self.chain:
            if s.strike == strike:
                return s
        return None

    def available_strikes(self) -> list[float]:
        return [s.strike for s in self.chain]


class VolatilityData(BaseModel):
    """Volatility metrics derived from chain + VIX data."""
    india_vix: float = Field(0.0, description="India VIX level")
    vix_change_pct: float = Field(0.0, description="VIX % change from prev close")
    vix_regime: str = Field("NORMAL", description="LOW | NORMAL | HIGH")
    atm_iv: float = Field(0.0, description="ATM option implied volatility")
    iv_rank: float = Field(0.0, description="IV Rank 0-100 (52-week range)")
    iv_percentile: float = Field(0.0, description="IV Percentile 0-100")
    expected_move_abs: float = Field(0.0, description="1-SD expected move in points")
    expected_move_pct: float = Field(0.0, description="1-SD expected move as %")
    iv_regime: str = Field("NORMAL", description="LOW | NORMAL | HIGH based on IV Rank")


class TechnicalData(BaseModel):
    """Technical indicator snapshot for the underlying."""
    spot_ltp: float = Field(0.0)
    vwap: float = Field(0.0)
    ema9: float = Field(0.0)
    ema20: float = Field(0.0)
    ema50: float = Field(0.0)
    ema200: float = Field(0.0)
    rsi14: float = Field(0.0)
    atr14: float = Field(0.0)
    day_high: float = Field(0.0)
    day_low: float = Field(0.0)
    prev_close: float = Field(0.0)
    gap_pct: float = Field(0.0, description="Gap from prev close %")
    price_vs_vwap: str = Field("", description="ABOVE | BELOW | AT")
    price_vs_ema20: str = Field("", description="ABOVE | BELOW")
    price_vs_ema50: str = Field("", description="ABOVE | BELOW")
    trend_5m: str = Field("NEUTRAL", description="BULLISH | BEARISH | NEUTRAL")
    trend_15m: str = Field("NEUTRAL")
    trend_1h: str = Field("NEUTRAL")
    trend_day: str = Field("NEUTRAL")


class OISignals(BaseModel):
    """Derived OI interpretation signals — pre-computed before LLM."""
    pcr: float = Field(0.0, description="Put-Call Ratio by OI")
    pcr_volume: float = Field(0.0, description="Put-Call Ratio by volume")
    near_atm_pcr: float = Field(0.0, description="PCR for ±2 strikes around ATM")
    max_call_oi_strike: float = Field(0.0, description="Strike with highest call OI")
    max_put_oi_strike: float = Field(0.0, description="Strike with highest put OI")
    call_writing_zones: list[float] = Field(default_factory=list, description="Strikes with probable call writing")
    put_writing_zones: list[float] = Field(default_factory=list, description="Strikes with probable put writing")
    call_unwinding_zones: list[float] = Field(default_factory=list)
    put_unwinding_zones: list[float] = Field(default_factory=list)
    oi_support_levels: list[float] = Field(default_factory=list, description="S/R from max put OI concentration")
    oi_resistance_levels: list[float] = Field(default_factory=list, description="S/R from max call OI concentration")
    oi_trend: str = Field("NEUTRAL", description="BULLISH | BEARISH | NEUTRAL based on OI change")


class MarketDataModel(BaseModel):
    """
    Complete pre-computed market context passed to the strategy engine and LLM.
    Python is the source of truth for all values here.
    """
    underlying: str
    expiry_date: str
    snapshot_age_sec: float
    technicals: TechnicalData
    volatility: VolatilityData
    oi_signals: OISignals
    computed_at: datetime = Field(default_factory=datetime.now)
