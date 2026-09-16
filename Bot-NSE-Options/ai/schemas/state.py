"""
ai/schemas/state.py
===================
LangGraph AnalysisState TypedDict — the shared state flowing through all nodes.
Every node takes AnalysisState and returns a partial dict to update it.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, TypedDict, Any

from ai.schemas.market import OptionChainSnapshot, MarketDataModel, OISignals
from ai.schemas.regime import MarketRegime
from ai.schemas.strategy import StrategyCandidate, ScoredCandidate
from ai.schemas.recommendation import LLMRecommendation, OrderValidationResult, ExecutionRequest
from ai.schemas.settings import PlatformSettings


class UnderlyingConfig(TypedDict, total=False):
    """Per-index configuration loaded from config.yml underlyings: list."""
    symbol: str             # "NIFTY" | "BANKNIFTY" | "FINNIFTY"
    exchange: str           # "NSE_INDEX"
    option_exchange: str    # "NFO"
    strike_gap: int         # 50 | 100
    lot_size: int           # 75 | 35 | 40
    expiry_date: str        # e.g. "24JUL25" or "" for auto
    strikes_each_side: int  # default 10
    enabled: bool


class AnalysisState(TypedDict, total=False):
    """
    Shared state for the LangGraph analysis workflow.
    All nodes read from and write partial updates to this state.
    Uses total=False so nodes only need to specify keys they update.
    """
    # ── Input ──────────────────────────────────────────────────────
    underlying: str                         # "NIFTY" | "BANKNIFTY"
    underlying_config: UnderlyingConfig     # Full index parameters
    expiry_date: str
    analysis_id: str                        # UUID for this analysis run
    triggered_by: str                       # "user_request" | "regime_change" | "oi_shift"

    # ── Settings (loaded first by load_settings_node) ──────────────
    active_toggles: PlatformSettings

    # ── Data layer ─────────────────────────────────────────────────
    snapshot: Optional[OptionChainSnapshot]
    snapshot_age_sec: float
    data_valid: bool

    # ── Quant layer ────────────────────────────────────────────────
    market_data: Optional[MarketDataModel]
    regime: Optional[MarketRegime]
    oi_signals: Optional[OISignals]

    # ── Strategy layer ─────────────────────────────────────────────
    candidates: list[StrategyCandidate]
    scored_candidates: list[ScoredCandidate]

    # ── LLM layer ──────────────────────────────────────────────────
    llm_recommendation: Optional[LLMRecommendation]
    llm_validation_passed: bool
    prompt_version: str

    # ── Human gate ─────────────────────────────────────────────────
    human_decision: Optional[str]           # "approve" | "reject" | None
    approved_strategy: Optional[ScoredCandidate]
    recommendation_valid_until: Optional[datetime]
    spot_at_recommendation: float           # spot price when recommendation was generated

    # ── Execution ──────────────────────────────────────────────────
    execution_request: Optional[ExecutionRequest]
    order_validation_result: Optional[OrderValidationResult]
    execution_result: Optional[dict]

    # ── Audit ──────────────────────────────────────────────────────
    errors: list[str]
    warnings: list[str]
    no_trade_reason: Optional[str]
    node_timings: dict[str, float]          # node_name -> execution seconds
