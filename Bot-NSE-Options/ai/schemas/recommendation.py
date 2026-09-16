"""
ai/schemas/recommendation.py
=============================
LLM output contract and order execution models.
The LLMRecommendation is the ONLY structured output the LLM produces.
All other calculations come from the Python quant/strategy engines.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class LLMRecommendation(BaseModel):
    """
    Structured output from the LLM interpretation node.
    Validated against the chain snapshot before being surfaced to the dashboard.
    The LLM must NOT invent values — it only interprets pre-computed data.
    """
    market_view: str = Field(..., description="bullish | bearish | neutral | volatile | no_trade")
    confidence: int = Field(..., ge=0, le=100, description="LLM confidence 0-100")
    selected_strategy_type: str = Field(..., description="Must match a STRATEGY_TYPES value")
    reasoning: list[str] = Field(default_factory=list, description="Bullet points explaining the recommendation")
    risks: list[str] = Field(default_factory=list, description="Key risks to monitor")
    conflicts: list[str] = Field(default_factory=list, description="Contradictory signals identified")
    no_trade_recommended: bool = Field(False, description="True if LLM agrees with NO_TRADE regime output")
    no_trade_reason: str = Field("", description="LLM's reason for recommending no trade")
    dashboard_summary: str = Field("", description="One-line human-readable summary for dashboard display")
    prompt_version: str = Field("1.0")
    llm_provider: str = Field("")
    llm_model: str = Field("")
    generated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("market_view")
    @classmethod
    def validate_market_view(cls, v: str) -> str:
        allowed = {"bullish", "bearish", "neutral", "volatile", "no_trade"}
        if v.lower() not in allowed:
            raise ValueError(f"market_view must be one of {allowed}")
        return v.lower()

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("confidence must be 0-100")
        return v


class OrderValidationResult(BaseModel):
    """Result of the pre-execution safety validation layer."""
    valid: bool
    errors: list[str] = Field(default_factory=list, description="Blocking errors — order cannot proceed")
    warnings: list[str] = Field(default_factory=list, description="Non-blocking warnings")
    market_open: bool = False
    quotes_fresh: bool = False
    margin_sufficient: Optional[bool] = None
    position_limit_ok: bool = False
    duplicate_check_ok: bool = False
    spot_price_at_validation: float = 0.0
    spot_drift_pct: float = 0.0


class ExecutionRequest(BaseModel):
    """
    The exact multi-leg order to be sent to OpenAlgo optionsmultiorder.
    Built from an approved ScoredCandidate after order validation passes.
    """
    analysis_id: str
    underlying: str
    exchange: str
    expiry_date: str
    strategy_type: str
    legs: list[dict] = Field(..., description="List of leg dicts for optionsmultiorder API")
    lot_count: int = Field(1)
    strategy_name: str = Field("AI_Options_Platform")
    product: str = Field("NRML")
    price_type: str = Field("MARKET")
    created_at: datetime = Field(default_factory=datetime.now)
    confirmation_token: str = Field("", description="User confirmation token — must be non-empty before execution")
