"""
ai/schemas/settings.py
======================
Platform settings model — all toggle states as typed Pydantic fields.
Defaults match config.yml toggles: section.
Loaded from SQLite settings table at runtime; config.yml provides factory defaults.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class PlatformSettings(BaseModel):
    """
    Complete platform toggle state.
    All fields default to the values defined in config.yml toggles: section.
    The settings service loads the live state from SQLite and returns this model.
    """
    # ---- Level 1: Data Source Toggles ----
    data_option_chain: bool = True
    data_spot_futures: bool = True
    data_india_vix: bool = True
    data_option_greeks: bool = True
    data_futures_basis: bool = False       # MVP 2

    # ---- Level 2: Quant Engine Component Toggles ----
    quant_oi_analysis: bool = True
    quant_iv_engine: bool = True
    quant_greeks: bool = True
    quant_technicals: bool = True
    quant_regime_engine: bool = True
    quant_vix_regime: bool = True
    quant_session_weighting: bool = False  # Off by default

    # ---- Level 3: Strategy Type Toggles ----
    strategy_bull_put_spread: bool = True
    strategy_bear_call_spread: bool = True
    strategy_bull_call_spread: bool = True
    strategy_bear_put_spread: bool = True
    strategy_iron_condor: bool = True
    strategy_iron_fly: bool = True
    strategy_long_straddle: bool = True
    strategy_short_strangle: bool = True

    # ---- Level 4: Risk & Execution Toggles ----
    risk_kill_switch: bool = False
    risk_paper_trade: bool = False
    risk_auto_execution: bool = False      # LOCKED — always False until MVP 3
    risk_daily_loss_limit: bool = False
    risk_market_hours_check: bool = False
    risk_duplicate_guard: bool = False
    risk_recommendation_ttl: bool = True
    risk_consecutive_loss_breaker: bool = False

    def enabled_strategy_types(self) -> list[str]:
        """Return list of enabled strategy type identifiers."""
        mapping = {
            "strategy_bull_put_spread": "bull_put_spread",
            "strategy_bear_call_spread": "bear_call_spread",
            "strategy_bull_call_spread": "bull_call_spread",
            "strategy_bear_put_spread": "bear_put_spread",
            "strategy_iron_condor": "iron_condor",
            "strategy_iron_fly": "iron_fly",
            "strategy_long_straddle": "long_straddle",
            "strategy_short_strangle": "short_strangle",
        }
        return [v for k, v in mapping.items() if getattr(self, k, True)]

    def enabled_quant_components(self) -> list[str]:
        """Return list of enabled quant component names."""
        components = [
            "quant_oi_analysis", "quant_iv_engine", "quant_greeks",
            "quant_technicals", "quant_regime_engine", "quant_vix_regime",
            "quant_session_weighting",
        ]
        return [c for c in components if getattr(self, c, False)]

    def redistribute_weights(self, base_weights: dict[str, float]) -> dict[str, float]:
        """
        Redistribute scoring weights when quant components are disabled.
        Ensures weights always sum to 100.
        Maps quant toggles to scoring weight keys.
        """
        component_weight_map = {
            "quant_oi_analysis": "oi_structure",
            "quant_iv_engine": "iv_environment",
            "quant_technicals": "trend_alignment",
            "quant_session_weighting": None,  # Optional bonus — no base weight
        }
        disabled_weight = 0.0
        active_keys = set(base_weights.keys())

        for toggle_key, weight_key in component_weight_map.items():
            if weight_key and not getattr(self, toggle_key, True):
                disabled_weight += base_weights.get(weight_key, 0.0)
                active_keys.discard(weight_key)

        if not active_keys or disabled_weight == 0.0:
            return base_weights.copy()

        # Redistribute proportionally among active keys
        active_total = sum(base_weights[k] for k in active_keys if k in base_weights)
        result = {}
        for k, v in base_weights.items():
            if k in active_keys:
                result[k] = v + (v / active_total * disabled_weight) if active_total > 0 else v
            else:
                result[k] = 0.0
        return result
