"""
api/ai_routes.py
=================
FastAPI routes for the AI Options Strategy platform.
All routes are mounted at /api/ai/* by app.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")

router = APIRouter()


# ── Request/Response models ───────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    underlying: str = "NIFTY"
    expiry_date: str = ""
    trigger_source: str = "user_request"


class ApproveRequest(BaseModel):
    lot_count: int = 1


class RejectRequest(BaseModel):
    reason: str = ""


class ToggleRequest(BaseModel):
    key: str
    value: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def trigger_analysis(req: AnalyzeRequest):
    """
    POST /api/ai/analyze
    Trigger a new AI analysis run for the given underlying.
    Returns analysis_id immediately; full result via GET /recommendation/{id}.
    """
    try:
        from ai.workflow.recommendation_service import get_recommendation_service
        svc = get_recommendation_service()
        # Run in thread pool to avoid blocking the event loop
        from fastapi.concurrency import run_in_threadpool
        result = await run_in_threadpool(
            svc.run_analysis,
            req.underlying,
            req.expiry_date,
            req.trigger_source,
        )
        return JSONResponse(content=result)
    except Exception as exc:
        log.error("[AI Routes] /analyze error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/recommendation/{analysis_id}")
async def get_recommendation(analysis_id: str):
    """
    GET /api/ai/recommendation/{analysis_id}
    Returns full recommendation including strategies, regime, LLM output, TTL.
    """
    try:
        from ai.workflow.recommendation_service import get_recommendation_service
        svc = get_recommendation_service()
        rec = svc.get_recommendation(analysis_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found")
        return JSONResponse(content=rec, media_type="application/json")
    except HTTPException:
        raise
    except Exception as exc:
        log.error("[AI Routes] /recommendation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/approve/{analysis_id}")
async def approve_recommendation(analysis_id: str, req: ApproveRequest):
    """
    POST /api/ai/approve/{analysis_id}
    Human approves the recommendation. Triggers order validation + execution.
    """
    try:
        from ai.workflow.recommendation_service import get_recommendation_service
        svc = get_recommendation_service()
        from fastapi.concurrency import run_in_threadpool
        result = await run_in_threadpool(svc.approve, analysis_id, req.lot_count)
        return JSONResponse(content=result)
    except Exception as exc:
        log.error("[AI Routes] /approve error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reject/{analysis_id}")
async def reject_recommendation(analysis_id: str, req: RejectRequest):
    """
    POST /api/ai/reject/{analysis_id}
    Record human rejection of a recommendation.
    """
    try:
        from ai.workflow.recommendation_service import get_recommendation_service
        svc = get_recommendation_service()
        result = svc.reject(analysis_id, req.reason)
        return JSONResponse(content=result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/history")
async def get_history(limit: int = Query(default=20, le=100)):
    """
    GET /api/ai/history
    Returns recent recommendations with outcomes.
    """
    try:
        from persistence.recommendation_db import get_recent_recommendations
        rows = get_recent_recommendations(limit)
        return JSONResponse(content={"history": rows})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/chain")
async def get_chain(underlying: str = Query(default="NIFTY")):
    """
    GET /api/ai/chain
    Returns the current option chain snapshot for dashboard display.
    """
    try:
        import yaml
        from fastapi.concurrency import run_in_threadpool
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        uc = next((u for u in cfg.get("underlyings", []) if u.get("symbol") == underlying), None)
        if uc is None:
            raise HTTPException(status_code=400, detail=f"Unknown underlying: {underlying}")
        from data.providers.option_chain_provider import fetch_snapshot
        snap = await run_in_threadpool(fetch_snapshot, uc, cfg)
        if snap is None:
            return JSONResponse(content={"status": "unavailable", "underlying": underlying})
        return JSONResponse(content={
            "status": "ok",
            "underlying": snap.underlying,
            "underlying_ltp": snap.underlying_ltp,
            "atm_strike": snap.atm_strike,
            "expiry_date": snap.expiry_date,
            "age_seconds": snap.age_seconds,
            "strikes": [
                {
                    "strike": s.strike,
                    "ce": {"ltp": s.ce.ltp, "oi": s.ce.oi, "iv": s.ce.iv, "delta": s.ce.delta} if s.ce else None,
                    "pe": {"ltp": s.pe.ltp, "oi": s.pe.oi, "iv": s.pe.iv, "delta": s.pe.delta} if s.pe else None,
                }
                for s in snap.chain
            ],
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/settings")
async def get_settings():
    """GET /api/ai/settings — returns full toggle state."""
    try:
        from ai.workflow.settings_service import get_settings_service
        svc = get_settings_service()
        return JSONResponse(content=svc.get_all_as_dict())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/settings/toggle")
async def update_toggle(req: ToggleRequest):
    """POST /api/ai/settings/toggle — update a single toggle."""
    try:
        from ai.workflow.settings_service import get_settings_service
        svc = get_settings_service()
        updated = svc.update_toggle(req.key, req.value)
        return JSONResponse(content=updated.model_dump())
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/settings/reset")
async def reset_settings():
    """POST /api/ai/settings/reset — reset all toggles to config.yml defaults."""
    try:
        from ai.workflow.settings_service import get_settings_service
        svc = get_settings_service()
        import yaml
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        updated = svc.reset_to_defaults(cfg)
        return JSONResponse(content=updated.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """
    WebSocket /api/ai/ws/stream
    Streams real-time analysis progress updates to the dashboard.
    Client can send: {"action": "analyze", "underlying": "NIFTY"}
    Server sends: {"type": "progress"|"complete"|"error", "data": {...}}
    """
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            if action == "analyze":
                underlying = msg.get("underlying", "NIFTY")
                await websocket.send_json({"type": "progress", "stage": "starting", "underlying": underlying})

                try:
                    from ai.workflow.recommendation_service import get_recommendation_service
                    svc = get_recommendation_service()
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, svc.run_analysis, underlying, "", "user_request"
                    )
                    await websocket.send_json({"type": "complete", "data": result})
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        log.debug("[WS] Client disconnected from /ws/stream")
    except Exception as exc:
        log.warning("[WS] Stream error: %s", exc)
