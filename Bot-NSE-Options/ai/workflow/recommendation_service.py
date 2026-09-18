"""
ai/workflow/recommendation_service.py
=======================================
Service orchestrating the full analysis lifecycle:
  run_analysis() → triggers LangGraph → returns analysis_id
  approve(analysis_id) → validates freshness → triggers execution
  reject(analysis_id) → records rejection
All results persisted to recommendation_db.
"""
from __future__ import annotations

import logging
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


def _load_cfg():
    return yaml.safe_load(open(_bot_dir / "config.yml"))


def _get_underlying_config(underlying: str, cfg: dict) -> Optional[dict]:
    """Get UnderlyingConfig for the given symbol from config."""
    for u in cfg.get("underlyings", []):
        if u.get("symbol") == underlying and u.get("enabled", False):
            return u
    return None


class RecommendationService:
    """
    High-level service for AI analysis runs.
    Wraps LangGraph workflow invocation and persistence.
    """

    def __init__(self):
        self._active_states: dict[str, dict] = {}  # analysis_id -> final state

    def run_analysis(
        self,
        underlying: str = "NIFTY",
        expiry_date: str = "",
        trigger: str = "user_request",
    ) -> dict:
        """
        Run a full analysis for the given underlying.
        Returns dict with analysis_id and status.
        The graph runs synchronously (async wrapper in FastAPI routes).
        """
        cfg = _load_cfg()
        underlying_config = _get_underlying_config(underlying, cfg)
        if underlying_config is None:
            return {
                "status": "error",
                "message": f"Underlying '{underlying}' not found or disabled in config",
            }

        # Override expiry if provided
        if expiry_date:
            underlying_config = {**underlying_config, "expiry_date": expiry_date}

        from ai.workflow.graph import get_graph, create_initial_state
        graph = get_graph()
        initial_state = create_initial_state(underlying, underlying_config, trigger)
        analysis_id = initial_state["analysis_id"]

        log.info("[RecommendationService] Starting analysis %s for %s", analysis_id[:8], underlying)

        try:
            config = {"configurable": {"thread_id": analysis_id}}
            final_state = graph.invoke(initial_state, config=config)
            # With TypedDict state, these keys are preserved — but guard anyway
            # in case the graph routes to an early error before setting them.
            patch = {}
            if not final_state.get("analysis_id"):
                patch["analysis_id"] = analysis_id
            if not final_state.get("underlying"):
                patch["underlying"] = underlying
            if patch:
                final_state = {**final_state, **patch}
            self._active_states[analysis_id] = final_state

            # Persist recommendation
            from persistence.recommendation_db import save_recommendation
            save_recommendation(final_state)

            # Build response
            regime = final_state.get("regime")
            scored = final_state.get("scored_candidates", [])
            llm_rec = final_state.get("llm_recommendation")
            errors = final_state.get("errors", [])
            no_trade = final_state.get("no_trade_reason")
            rec_until = final_state.get("recommendation_valid_until")

            return {
                "status": "ready" if (scored and not errors) else ("no_trade" if no_trade else "error"),
                "analysis_id": analysis_id,
                "underlying": underlying,
                "regime": regime.regime if regime else "UNKNOWN",
                "regime_confidence": regime.regime_confidence if regime else 0,
                "no_trade": bool(no_trade),
                "no_trade_reason": no_trade or "",
                "candidate_count": len(scored),
                "top_strategy": scored[0].candidate.strategy_type if scored else None,
                "top_score": scored[0].score if scored else None,
                "llm_view": llm_rec.market_view if llm_rec else None,
                "llm_confidence": llm_rec.confidence if llm_rec else None,
                "recommendation_valid_until": rec_until.isoformat() if rec_until else None,
                "errors": errors,
                "warnings": final_state.get("warnings", []),
            }

        except Exception as exc:
            log.error("[RecommendationService] Analysis failed: %s", exc)
            return {
                "status": "error",
                "analysis_id": analysis_id,
                "message": str(exc),
            }

    def get_recommendation(self, analysis_id: str) -> Optional[dict]:
        """Return the full recommendation response for a given analysis_id."""
        # 1. Check in-memory cache first (fastest, same process)
        state = self._active_states.get(analysis_id)
        if state:
            return self._build_recommendation_response(state)
        # 2. DB fallback — returns full_state_json which is already the response format
        from persistence.recommendation_db import get_recommendation
        stored = get_recommendation(analysis_id)
        if stored is None:
            return None
        # If the stored dict looks like a full state (has 'scored_candidates' key),
        # wrap it through _build_recommendation_response for consistent formatting.
        if "scored_candidates" in stored or "regime" in stored:
            return self._build_recommendation_response(stored)
        return stored

    def approve(self, analysis_id: str, lot_count: int = 1) -> dict:
        """
        Human approves the recommendation.
        Validates freshness, runs order validation, executes if valid.
        """
        state = self._active_states.get(analysis_id)
        # DB fallback: if state not in memory (e.g. after restart), load from persistence
        if state is None:
            from persistence.recommendation_db import get_recommendation
            db_state = get_recommendation(analysis_id)
            if db_state and "scored_candidates" in db_state:
                state = db_state
                self._active_states[analysis_id] = state  # cache it
        if state is None:
            return {"status": "error", "message": f"Analysis {analysis_id} not found"}

        # Check TTL — valid_until may be datetime (live) or ISO string (DB-restored)
        valid_until = state.get("recommendation_valid_until")
        if valid_until:
            if isinstance(valid_until, str):
                try:
                    valid_until = datetime.fromisoformat(valid_until)
                except Exception:
                    valid_until = None
        if valid_until and datetime.now() > valid_until:
            return {
                "status": "stale",
                "message": "Recommendation has expired. Please re-run analysis.",
            }

        # Check spot price drift
        cfg = _load_cfg()
        stale_threshold = float(cfg.get("ai", {}).get("stale_price_threshold_pct", 0.3))
        spot_at_rec = state.get("spot_at_recommendation", 0.0)
        if spot_at_rec > 0:
            try:
                from trading_adapter import get_ltp
                underlying = state.get("underlying", "NIFTY")
                uc = state.get("underlying_config", {})
                current_spot = get_ltp(cfg, underlying, exchange=uc.get("exchange", "NSE_INDEX"))
                drift_pct = abs(current_spot - spot_at_rec) / spot_at_rec * 100
                if drift_pct > stale_threshold:
                    return {
                        "status": "stale",
                        "message": f"Spot moved {drift_pct:.2f}% since recommendation. Please re-run analysis.",
                        "drift_pct": drift_pct,
                    }
            except Exception as exc:
                log.warning("[RecommendationService] Spot drift check failed: %s", exc)

        # Run order validation
        from execution.order_validator import validate_order
        approved = _ensure_scored_candidate(state.get("approved_strategy"))
        settings = _ensure_platform_settings(state.get("active_toggles"))

        # Apply the user-selected lot_count to the candidate before it is
        # validated/executed. Without this, the candidate's default
        # lot_count=1 (set by strategies/generator.py) is always used,
        # silently ignoring the caller-provided lot sizing.
        if approved is not None:
            try:
                requested_lots = max(1, int(lot_count))
            except (TypeError, ValueError):
                requested_lots = 1
            candidate_obj = approved.candidate if hasattr(approved, "candidate") else approved.get("candidate")
            if candidate_obj is not None:
                if hasattr(candidate_obj, "lot_count"):
                    candidate_obj.lot_count = requested_lots
                elif isinstance(candidate_obj, dict):
                    candidate_obj["lot_count"] = requested_lots

        validation = validate_order(approved, cfg, state, settings)

        if not validation.valid:
            from persistence.recommendation_db import update_decision
            update_decision(analysis_id, "validation_failed")
            return {
                "status": "validation_failed",
                "errors": validation.errors,
                "warnings": validation.warnings,
            }

        # Execute
        from execution.multi_leg_executor import execute_strategy
        execution_result = execute_strategy(approved, cfg, settings)

        # Update state and persist
        state["human_decision"] = "approved"
        state["execution_result"] = execution_result
        self._active_states[analysis_id] = state

        from persistence.recommendation_db import update_decision
        update_decision(analysis_id, "approved", execution_result)

        # Send Telegram alert
        try:
            from telegram import send_telegram_alert
            if approved:
                strat_type = approved.candidate.strategy_type if hasattr(approved, "candidate") else str(approved)
                msg = (
                    f"[AI Options] Order Placed\n"
                    f"Strategy: {strat_type}\n"
                    f"Underlying: {state.get('underlying')}\n"
                    f"Status: {execution_result.get('status','unknown')}"
                )
                send_telegram_alert(cfg, msg)
        except Exception:
            pass

        return {
            "status": execution_result.get("status", "unknown"),
            "analysis_id": analysis_id,
            "execution_result": execution_result,
        }

    def reject(self, analysis_id: str, reason: str = "") -> dict:
        """Record human rejection of a recommendation."""
        state = self._active_states.get(analysis_id)
        if state:
            state["human_decision"] = "rejected"

        from persistence.recommendation_db import update_decision
        update_decision(analysis_id, "rejected")
        log.info("[RecommendationService] Analysis %s rejected: %s", analysis_id[:8], reason)
        return {"status": "rejected", "analysis_id": analysis_id}

    def get_history(self, limit: int = 20) -> list:
        from persistence.recommendation_db import get_recent_recommendations
        return get_recent_recommendations(limit)

    def _build_recommendation_response(self, state: dict) -> dict:
        """Build a rich response dict from a state."""
        scored = state.get("scored_candidates", [])
        llm_rec = state.get("llm_recommendation")
        regime = state.get("regime")
        snap = state.get("snapshot")
        rec_until = state.get("recommendation_valid_until")
        now = datetime.now()
        ttl_remaining = max(0, (rec_until - now).total_seconds()) if rec_until else 0
        is_stale = ttl_remaining <= 0

        def _dump(obj):
            """Normalise to a plain JSON-serialisable dict."""
            if obj is None:
                return None
            if isinstance(obj, dict):
                return obj           # already plain — from DB deserialization
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if hasattr(obj, "dict"):
                return obj.dict()
            return obj

        def _get(obj, key, default=None):
            """Get a value from either a dict or a Pydantic object."""
            if obj is None:
                return default
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Snapshot LTP/ATM — may be object or dict
        snap_ltp = _get(snap, "underlying_ltp") if snap else None
        snap_atm = _get(snap, "atm_strike") if snap else None
        # Also check if stored as floats directly in state (from DB reconstruction)
        if snap_ltp is None:
            snap_ltp = state.get("snapshot_ltp")
        if snap_atm is None:
            snap_atm = state.get("snapshot_atm")

        return {
            "analysis_id": state.get("analysis_id"),
            "underlying": state.get("underlying"),
            "expiry_date": state.get("expiry_date"),
            "status": state.get("human_decision") or ("no_trade" if state.get("no_trade_reason") else "pending_approval"),
            "is_stale": is_stale,
            "ttl_remaining_sec": int(ttl_remaining),
            "regime": _dump(regime),
            "market_data": _dump(state.get("market_data")),
            "scored_candidates": [_dump(sc) for sc in scored[:5]],
            "llm_recommendation": _dump(llm_rec),
            "no_trade_reason": state.get("no_trade_reason"),
            "snapshot_ltp": snap_ltp,
            "snapshot_atm": snap_atm,
            "errors": state.get("errors", []),
            "warnings": state.get("warnings", []),
            "node_timings": state.get("node_timings", {}),
        }


def _ensure_scored_candidate(obj):
    """
    Ensure obj is a ScoredCandidate Pydantic model.
    If it's already a Pydantic model, return as-is.
    If it's a dict (from DB deserialization), reconstruct it.
    Returns None if obj is None or cannot be reconstructed.
    """
    if obj is None:
        return None
    if hasattr(obj, "candidate"):  # already a Pydantic model
        return obj
    if isinstance(obj, dict):
        try:
            from ai.schemas.strategy import ScoredCandidate
            return ScoredCandidate(**obj)
        except Exception as exc:
            log.warning("[RecommendationService] Could not reconstruct ScoredCandidate from dict: %s", exc)
            return None
    return None


def _ensure_platform_settings(obj):
    """
    Ensure obj is a PlatformSettings Pydantic model.
    If it's already a Pydantic model, return as-is.
    If it's a dict (from DB deserialization), reconstruct it.
    Returns default PlatformSettings if obj is None.
    """
    from ai.schemas.settings import PlatformSettings
    if obj is None:
        return PlatformSettings()
    if hasattr(obj, "risk_paper_trade"):  # already a Pydantic model
        return obj
    if isinstance(obj, dict):
        try:
            return PlatformSettings(**{k: v for k, v in obj.items() if k in PlatformSettings.model_fields})
        except Exception as exc:
            log.warning("[RecommendationService] Could not reconstruct PlatformSettings from dict: %s", exc)
            return PlatformSettings()
    return PlatformSettings()


# Singleton
_service: Optional[RecommendationService] = None

def get_recommendation_service() -> RecommendationService:
    global _service
    if _service is None:
        _service = RecommendationService()
    return _service
