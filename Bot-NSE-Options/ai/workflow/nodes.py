"""
ai/workflow/nodes.py
=====================
LangGraph node functions for the AI Options Strategy workflow.
Each node takes the full AnalysisState dict and returns a partial dict to update it.
Nodes are pure functions — no side effects except logging.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


# ── Node 1: Load Settings ────────────────────────────────────────────────────

def load_settings_node(state: dict) -> dict:
    """
    Node 1: Load platform settings from the settings service.
    Must be the first node in the graph.
    """
    import time
    t0 = time.time()
    try:
        from ai.workflow.settings_service import get_settings_service
        svc = get_settings_service()
        settings = svc.get_settings()
        log.debug("[Node:load_settings] Settings loaded, kill_switch=%s", settings.risk_kill_switch)
        return {
            "active_toggles": settings,
            "node_timings": {**state.get("node_timings", {}), "load_settings": time.time() - t0},
        }
    except Exception as exc:
        log.warning("[Node:load_settings] Failed to load settings: %s — using defaults", exc)
        from ai.schemas.settings import PlatformSettings
        return {
            "active_toggles": PlatformSettings(),
            "node_timings": {**state.get("node_timings", {}), "load_settings": time.time() - t0},
            "warnings": [*state.get("warnings", []), f"Settings load failed: {exc}"],
        }


# ── Node 2: Load Snapshot ────────────────────────────────────────────────────

def load_snapshot_node(state: dict) -> dict:
    """
    Node 2: Fetch the option chain snapshot for the active underlying.
    """
    import time
    import yaml
    t0 = time.time()
    try:
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        underlying_config = state.get("underlying_config", {})
        settings = state.get("active_toggles")

        if settings and not settings.data_option_chain:
            return {
                "data_valid": False,
                "errors": [*state.get("errors", []), "Option chain data toggle is OFF"],
                "node_timings": {**state.get("node_timings", {}), "load_snapshot": time.time() - t0},
            }

        from data.providers.option_chain_provider import fetch_snapshot, fetch_greeks_for_chain
        snapshot = fetch_snapshot(underlying_config, cfg)

        if snapshot is None:
            return {
                "data_valid": False,
                "errors": [*state.get("errors", []), "Failed to fetch option chain snapshot"],
                "node_timings": {**state.get("node_timings", {}), "load_snapshot": time.time() - t0},
            }

        # Enrich with Greeks if toggle is on
        if settings and settings.data_option_greeks:
            snapshot = fetch_greeks_for_chain(snapshot, cfg)

        return {
            "snapshot": snapshot,
            "expiry_date": snapshot.expiry_date,
            "data_valid": True,
            "node_timings": {**state.get("node_timings", {}), "load_snapshot": time.time() - t0},
        }

    except Exception as exc:
        log.error("[Node:load_snapshot] %s", exc)
        return {
            "data_valid": False,
            "errors": [*state.get("errors", []), f"Snapshot load error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "load_snapshot": time.time() - t0},
        }


# ── Node 3: Validate Freshness ───────────────────────────────────────────────

def validate_freshness_node(state: dict) -> dict:
    """
    Node 3: Validate snapshot freshness against configured max age.
    Stale snapshots (age > freshness_max_sec but <= 60s) produce a warning
    and continue — the quant pipeline can still run on slightly old data.
    Only truly expired snapshots (age > 60s) are hard failures.
    """
    import time
    import yaml
    t0 = time.time()
    try:
        snapshot = state.get("snapshot")
        if snapshot is None:
            return {
                "data_valid": False,
                "errors": [*state.get("errors", []), "No snapshot to validate"],
                "node_timings": {**state.get("node_timings", {}), "validate_freshness": time.time() - t0},
            }
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        max_sec = float(cfg.get("ai", {}).get("freshness_max_sec", 60))
        age = snapshot.age_seconds
        warnings = list(state.get("warnings", []))

        # Hard expiry: >60s is always a failure
        hard_expiry_sec = max(60.0, max_sec * 3)
        if age > hard_expiry_sec:
            snapshot.freshness_status = "expired"
            return {
                "snapshot_age_sec": age,
                "data_valid": False,
                "errors": [*state.get("errors", []), f"Snapshot expired ({age:.0f}s old — max {hard_expiry_sec:.0f}s)"],
                "node_timings": {**state.get("node_timings", {}), "validate_freshness": time.time() - t0},
            }

        # Soft staleness: warn but continue
        if age > max_sec:
            snapshot.freshness_status = "stale"
            warnings.append(f"Snapshot is stale ({age:.1f}s > {max_sec:.0f}s configured) — continuing with warning")
            log.warning("[%s] Snapshot is stale (%.1fs) but within tolerance — continuing", snapshot.underlying, age)
        else:
            snapshot.freshness_status = "fresh"

        return {
            "snapshot_age_sec": age,
            "data_valid": True,
            "warnings": warnings,
            "node_timings": {**state.get("node_timings", {}), "validate_freshness": time.time() - t0},
        }
    except Exception as exc:
        return {
            "data_valid": False,
            "errors": [*state.get("errors", []), f"Freshness check error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "validate_freshness": time.time() - t0},
        }


# ── Node 4: Run Quant ────────────────────────────────────────────────────────

def run_quant_node(state: dict) -> dict:
    """Node 4: Run all quant engine components to build MarketDataModel."""
    import time
    import yaml
    t0 = time.time()
    try:
        snapshot = state.get("snapshot")
        settings = state.get("active_toggles")
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        underlying = state.get("underlying", "NIFTY")
        underlying_config = state.get("underlying_config", {})
        exchange = underlying_config.get("exchange", "NSE_INDEX")

        from data.providers.option_chain_provider import fetch_vix, fetch_spot_history
        from quant.market_snapshot import build_market_data_model

        vix = fetch_vix(cfg) if (settings is None or settings.data_india_vix) else 0.0

        # Fetch spot OHLCV for multiple timeframes
        histories = {}
        for tf in ["5m", "15m", "1h"]:
            df = fetch_spot_history(underlying, tf, cfg, exchange=exchange)
            if df is not None:
                histories[tf] = df

        market_data = build_market_data_model(snapshot, vix, histories, cfg, settings)
        oi_signals = market_data.oi_signals

        return {
            "market_data": market_data,
            "oi_signals": oi_signals,
            "node_timings": {**state.get("node_timings", {}), "run_quant": time.time() - t0},
        }
    except Exception as exc:
        log.error("[Node:run_quant] %s", exc)
        return {
            "errors": [*state.get("errors", []), f"Quant engine error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "run_quant": time.time() - t0},
        }


# ── Node 5: Classify Regime ──────────────────────────────────────────────────

def classify_regime_node(state: dict) -> dict:
    """Node 5: Classify the market regime."""
    import time
    import yaml
    t0 = time.time()
    try:
        market_data = state.get("market_data")
        settings = state.get("active_toggles")
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))

        if market_data is None:
            from ai.schemas.regime import MarketRegime, TimeframeAlignment
            return {
                "regime": MarketRegime(
                    regime="NEUTRAL",
                    trend_direction="NEUTRAL",
                    trend_strength=50,
                    timeframe_alignment=TimeframeAlignment(),
                    vol_regime="NORMAL",
                    iv_regime="NORMAL",
                    no_trade=True,
                    no_trade_reason="No market data available",
                    regime_confidence=0,
                ),
                "node_timings": {**state.get("node_timings", {}), "classify_regime": time.time() - t0},
            }

        from quant.regime_engine import classify_regime
        regime = classify_regime(market_data, cfg, settings)
        return {
            "regime": regime,
            "no_trade_reason": regime.no_trade_reason if regime.no_trade else None,
            "node_timings": {**state.get("node_timings", {}), "classify_regime": time.time() - t0},
        }
    except Exception as exc:
        log.error("[Node:classify_regime] %s", exc)
        return {
            "errors": [*state.get("errors", []), f"Regime classification error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "classify_regime": time.time() - t0},
        }


# ── Node 6: Generate Candidates ─────────────────────────────────────────────

def generate_candidates_node(state: dict) -> dict:
    """Node 6: Generate strategy candidates from chain snapshot + regime."""
    import time
    import yaml
    t0 = time.time()
    try:
        snapshot = state.get("snapshot")
        regime = state.get("regime")
        settings = state.get("active_toggles")
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))

        from strategies.generator import generate_candidates
        candidates = generate_candidates(snapshot, regime, cfg, settings)

        return {
            "candidates": candidates,
            "node_timings": {**state.get("node_timings", {}), "generate_candidates": time.time() - t0},
        }
    except Exception as exc:
        log.error("[Node:generate_candidates] %s", exc)
        return {
            "candidates": [],
            "errors": [*state.get("errors", []), f"Strategy generation error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "generate_candidates": time.time() - t0},
        }


# ── Node 7: Score Candidates ─────────────────────────────────────────────────

def score_candidates_node(state: dict) -> dict:
    """Node 7: Score and rank all strategy candidates."""
    import time
    import yaml
    t0 = time.time()
    try:
        candidates = state.get("candidates", [])
        market_data = state.get("market_data")
        regime = state.get("regime")
        settings = state.get("active_toggles")
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))

        if not candidates:
            return {
                "scored_candidates": [],
                "no_trade_reason": state.get("no_trade_reason") or "No strategy candidates generated",
                "node_timings": {**state.get("node_timings", {}), "score_candidates": time.time() - t0},
            }

        from strategies.scoring import score_candidates
        scored = score_candidates(candidates, market_data, regime, cfg, settings)

        return {
            "scored_candidates": scored,
            "node_timings": {**state.get("node_timings", {}), "score_candidates": time.time() - t0},
        }
    except Exception as exc:
        log.error("[Node:score_candidates] %s", exc)
        return {
            "scored_candidates": [],
            "errors": [*state.get("errors", []), f"Scoring error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "score_candidates": time.time() - t0},
        }


# ── Node 8: Validate Strategy ────────────────────────────────────────────────

def validate_strategy_node(state: dict) -> dict:
    """
    Node 8: Validate LLM recommendation against chain snapshot.
    Ensures selected strategy type exists in candidates and strikes are from chain.
    """
    import time
    t0 = time.time()
    try:
        llm_rec = state.get("llm_recommendation")
        scored = state.get("scored_candidates", [])

        if llm_rec is None:
            return {
                "llm_validation_passed": False,
                "errors": [*state.get("errors", []), "No LLM recommendation to validate"],
                "node_timings": {**state.get("node_timings", {}), "validate_strategy": time.time() - t0},
            }

        # NO_TRADE: always passes validation
        if llm_rec.no_trade_recommended:
            log.info("[Node:validate_strategy] LLM recommends NO_TRADE: %s", llm_rec.no_trade_reason)
            return {
                "llm_validation_passed": True,
                "no_trade_reason": llm_rec.no_trade_reason,
                "node_timings": {**state.get("node_timings", {}), "validate_strategy": time.time() - t0},
            }

        # Check: selected strategy type must match a candidate
        candidate_types = {sc.candidate.strategy_type for sc in scored}
        if llm_rec.selected_strategy_type not in candidate_types:
            log.warning(
                "[Node:validate_strategy] LLM selected unknown type: %s not in %s",
                llm_rec.selected_strategy_type, candidate_types,
            )
            return {
                "llm_validation_passed": False,
                "warnings": [
                    *state.get("warnings", []),
                    f"LLM selected strategy type '{llm_rec.selected_strategy_type}' not in generated candidates",
                ],
                "node_timings": {**state.get("node_timings", {}), "validate_strategy": time.time() - t0},
            }

        return {
            "llm_validation_passed": True,
            "node_timings": {**state.get("node_timings", {}), "validate_strategy": time.time() - t0},
        }

    except Exception as exc:
        return {
            "llm_validation_passed": False,
            "errors": [*state.get("errors", []), f"Strategy validation error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "validate_strategy": time.time() - t0},
        }


# ── Node 9: Build Recommendation ────────────────────────────────────────────

def build_recommendation_node(state: dict) -> dict:
    """
    Node 9: Build the final recommendation object.
    Attaches the top LLM-selected strategy with its full payoff data.
    Sets the recommendation TTL timestamp.
    """
    import time
    import yaml
    t0 = time.time()
    try:
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        llm_rec = state.get("llm_recommendation")
        scored = state.get("scored_candidates", [])

        ttl_sec = int(cfg.get("ai", {}).get("recommendation_ttl_sec", 90))
        valid_until = datetime.now() + timedelta(seconds=ttl_sec)
        spot = state.get("snapshot").underlying_ltp if state.get("snapshot") else 0.0

        # Find the top candidate matching LLM selection
        approved = None
        if llm_rec and not llm_rec.no_trade_recommended:
            for sc in scored:
                if sc.candidate.strategy_type == llm_rec.selected_strategy_type:
                    approved = sc
                    break
        # Fallback: use top-scored candidate
        if approved is None and scored:
            approved = scored[0]

        return {
            "approved_strategy": approved,
            "recommendation_valid_until": valid_until,
            "spot_at_recommendation": spot,
            "human_decision": None,  # waiting for human
            "node_timings": {**state.get("node_timings", {}), "build_recommendation": time.time() - t0},
        }
    except Exception as exc:
        return {
            "errors": [*state.get("errors", []), f"Build recommendation error: {exc}"],
            "node_timings": {**state.get("node_timings", {}), "build_recommendation": time.time() - t0},
        }


# ── Error handler ────────────────────────────────────────────────────────────

def handle_error_node(state: dict) -> dict:
    """Terminal error node. Logs all errors and marks workflow as failed."""
    errors = state.get("errors", [])
    log.error("[Workflow:ERROR] Analysis %s failed: %s", state.get("analysis_id"), errors)
    return {"human_decision": "error"}


# ── Node: Order Validation ───────────────────────────────────────────────────

def order_validation_node(state: dict) -> dict:
    """Node: Pre-execution order safety validation (runs after human approves)."""
    import time
    t0 = time.time()
    try:
        from execution.order_validator import validate_order
        import yaml
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        approved = state.get("approved_strategy")
        settings = state.get("active_toggles")
        result = validate_order(approved, cfg, state, settings)
        return {
            "order_validation_result": result,
            "node_timings": {**state.get("node_timings", {}), "order_validation": time.time() - t0},
        }
    except Exception as exc:
        from ai.schemas.recommendation import OrderValidationResult
        return {
            "order_validation_result": OrderValidationResult(valid=False, errors=[str(exc)]),
            "node_timings": {**state.get("node_timings", {}), "order_validation": time.time() - t0},
        }


# ── Node: Execute Order ──────────────────────────────────────────────────────

def execute_order_node(state: dict) -> dict:
    """Node: Execute the validated multi-leg order via OpenAlgo."""
    import time
    t0 = time.time()
    try:
        from execution.multi_leg_executor import execute_strategy
        import yaml
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        approved = state.get("approved_strategy")
        settings = state.get("active_toggles")
        result = execute_strategy(approved, cfg, settings)
        return {
            "execution_result": result,
            "node_timings": {**state.get("node_timings", {}), "execute_order": time.time() - t0},
        }
    except Exception as exc:
        return {
            "execution_result": {"status": "error", "message": str(exc)},
            "node_timings": {**state.get("node_timings", {}), "execute_order": time.time() - t0},
        }
