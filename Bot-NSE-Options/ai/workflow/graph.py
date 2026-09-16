"""
ai/workflow/graph.py
=====================
LangGraph StateGraph for the AI Options Strategy analysis workflow.
Defines all nodes, edges, conditional routing, and the human-in-the-loop interrupt.
"""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


def build_graph():
    """
    Build and compile the analysis workflow StateGraph.
    Returns the compiled graph ready for invocation.
    """
    try:
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as exc:
        raise ImportError(
            f"LangGraph not installed: {exc}. Run: pip install langgraph>=0.2.0"
        )

    from ai.workflow.nodes import (
        load_settings_node,
        load_snapshot_node,
        validate_freshness_node,
        run_quant_node,
        classify_regime_node,
        generate_candidates_node,
        score_candidates_node,
        handle_error_node,
        build_recommendation_node,
        validate_strategy_node,
        order_validation_node,
        execute_order_node,
    )
    from ai.workflow.llm_node import llm_interpretation_node
    from ai.schemas.state import AnalysisState

    # ── Build the graph ───────────────────────────────────────────────────────
    builder = StateGraph(AnalysisState)

    # ── Add all nodes ─────────────────────────────────────────────────────────
    builder.add_node("load_settings", load_settings_node)
    builder.add_node("load_snapshot", load_snapshot_node)
    builder.add_node("validate_freshness", validate_freshness_node)
    builder.add_node("run_quant", run_quant_node)
    builder.add_node("classify_regime", classify_regime_node)
    builder.add_node("generate_candidates", generate_candidates_node)
    builder.add_node("score_candidates", score_candidates_node)
    builder.add_node("llm_interpretation", llm_interpretation_node)
    builder.add_node("validate_strategy", validate_strategy_node)
    builder.add_node("build_recommendation", build_recommendation_node)
    builder.add_node("order_validation", order_validation_node)
    builder.add_node("execute_order", execute_order_node)
    builder.add_node("handle_error", handle_error_node)

    # ── Set entry point ───────────────────────────────────────────────────────
    builder.set_entry_point("load_settings")

    # ── Linear pipeline edges ─────────────────────────────────────────────────
    builder.add_edge("load_settings", "load_snapshot")
    builder.add_edge("run_quant", "classify_regime")
    builder.add_edge("classify_regime", "generate_candidates")
    builder.add_edge("generate_candidates", "score_candidates")
    builder.add_edge("score_candidates", "llm_interpretation")
    builder.add_edge("llm_interpretation", "validate_strategy")
    builder.add_edge("validate_strategy", "build_recommendation")

    # ── Conditional: after snapshot load ─────────────────────────────────────
    def route_after_snapshot(
        state: dict,
    ) -> Literal["validate_freshness", "handle_error"]:
        if state.get("data_valid", False) and state.get("snapshot") is not None:
            return "validate_freshness"
        return "handle_error"

    builder.add_conditional_edges("load_snapshot", route_after_snapshot)

    # ── Conditional: after freshness check ───────────────────────────────────
    def route_after_freshness(
        state: dict,
    ) -> Literal["run_quant", "handle_error"]:
        if state.get("data_valid", False):
            return "run_quant"
        return "handle_error"

    builder.add_conditional_edges("validate_freshness", route_after_freshness)

    # ── Conditional: after recommendation built → WAIT FOR HUMAN ─────────────
    # The graph pauses here; human_decision is set by the dashboard in a
    # separate invocation to resume the execution branch.
    def route_after_recommendation(
        state: dict,
    ) -> Literal["__end__", "handle_error"]:
        errors = state.get("errors", [])
        if errors:
            return "handle_error"
        # Always end here — human approval resumes the graph separately
        return "__end__"

    builder.add_conditional_edges("build_recommendation", route_after_recommendation)

    # ── After human approves: order validation → execution ────────────────────
    def route_after_validation(
        state: dict,
    ) -> Literal["execute_order", "handle_error"]:
        result = state.get("order_validation_result")
        if result and result.valid:
            return "execute_order"
        return "handle_error"

    builder.add_conditional_edges("order_validation", route_after_validation)

    # ── Terminal edges ────────────────────────────────────────────────────────
    builder.add_edge("execute_order", END)
    builder.add_edge("handle_error", END)

    # ── Compile with in-memory checkpointer ──────────────────────────────────
    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    log.info("[Graph] LangGraph analysis workflow compiled successfully")
    return graph


# ── Singleton compiled graph ──────────────────────────────────────────────────

_graph = None


def get_graph():
    """Return the compiled graph, building it once on first call."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def create_initial_state(
    underlying: str,
    underlying_config: dict,
    trigger: str = "user_request",
) -> dict:
    """Create the initial state dict for a new analysis run."""
    return {
        "underlying": underlying,
        "underlying_config": underlying_config,
        "expiry_date": underlying_config.get("expiry_date", ""),
        "analysis_id": str(uuid.uuid4()),
        "triggered_by": trigger,
        "active_toggles": None,
        "snapshot": None,
        "snapshot_age_sec": 0.0,
        "data_valid": False,
        "market_data": None,
        "regime": None,
        "oi_signals": None,
        "candidates": [],
        "scored_candidates": [],
        "llm_recommendation": None,
        "llm_validation_passed": False,
        "prompt_version": "1.0",
        "human_decision": None,
        "approved_strategy": None,
        "recommendation_valid_until": None,
        "spot_at_recommendation": 0.0,
        "execution_request": None,
        "order_validation_result": None,
        "execution_result": None,
        "errors": [],
        "warnings": [],
        "no_trade_reason": None,
        "node_timings": {},
    }
