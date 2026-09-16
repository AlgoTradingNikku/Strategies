"""
persistence/recommendation_db.py
==================================
SQLite persistence for AI analysis recommendations.
Every recommendation is stored for audit, backtesting, and LLM evaluation.
Follows the same pattern as trade_db.py.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("UTBotSRChannelsScanner")
DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                underlying TEXT NOT NULL,
                expiry_date TEXT,
                snapshot_age_sec REAL,
                regime TEXT,
                regime_confidence INTEGER,
                candidate_count INTEGER,
                top_strategy_type TEXT,
                top_score REAL,
                llm_market_view TEXT,
                llm_confidence INTEGER,
                llm_selected_type TEXT,
                llm_provider TEXT,
                llm_model TEXT,
                prompt_version TEXT,
                no_trade_flag INTEGER DEFAULT 0,
                no_trade_reason TEXT,
                human_decision TEXT,
                decided_at TEXT,
                order_validation_json TEXT,
                execution_result_json TEXT,
                scored_candidates_json TEXT,
                llm_response_json TEXT,
                market_snapshot_json TEXT,
                errors_json TEXT,
                node_timings_json TEXT,
                full_state_json TEXT
            )
        """)
        # Add full_state_json column to existing DBs (idempotent)
        try:
            conn.execute("ALTER TABLE recommendations ADD COLUMN full_state_json TEXT")
        except Exception:
            pass  # column already exists
        conn.commit()


init_db()


def save_recommendation(state: dict) -> int:
    """Persist a completed analysis state. Returns the row ID."""
    try:
        scored = state.get("scored_candidates", [])
        llm_rec = state.get("llm_recommendation")
        regime = state.get("regime")

        # Serialise complex objects to JSON
        def _to_json(obj) -> str:
            try:
                if obj is None:
                    return "null"
                if hasattr(obj, "model_dump"):
                    return json.dumps(obj.model_dump(), default=str)
                if isinstance(obj, list):
                    return json.dumps([
                        s.model_dump() if hasattr(s, "model_dump") else s for s in obj
                    ], default=str)
                return json.dumps(obj, default=str)
            except Exception:
                return "null"

        # Serialise the full state for retrieval by recommendation endpoint
        def _state_to_json(s: dict) -> str:
            out = {}
            for k, v in s.items():
                if k == "snapshot":
                    # Snapshot is too large and not needed for display
                    continue
                try:
                    if hasattr(v, "model_dump"):
                        out[k] = v.model_dump()
                    elif isinstance(v, list):
                        out[k] = [
                            x.model_dump() if hasattr(x, "model_dump") else x
                            for x in v
                        ]
                    else:
                        out[k] = v
                except Exception:
                    out[k] = str(v)
            return json.dumps(out, default=str)

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO recommendations (
                    analysis_id, timestamp, underlying, expiry_date,
                    snapshot_age_sec, regime, regime_confidence,
                    candidate_count, top_strategy_type, top_score,
                    llm_market_view, llm_confidence, llm_selected_type,
                    llm_provider, llm_model, prompt_version,
                    no_trade_flag, no_trade_reason,
                    human_decision, decided_at,
                    scored_candidates_json, llm_response_json,
                    errors_json, node_timings_json, full_state_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?
                )
            """, (
                state.get("analysis_id"),
                datetime.now().isoformat(),
                state.get("underlying"),
                state.get("expiry_date"),
                state.get("snapshot_age_sec", 0.0),
                regime.regime if regime else None,
                regime.regime_confidence if regime else None,
                len(scored),
                scored[0].candidate.strategy_type if scored else None,
                scored[0].score if scored else None,
                llm_rec.market_view if llm_rec else None,
                llm_rec.confidence if llm_rec else None,
                llm_rec.selected_strategy_type if llm_rec else None,
                llm_rec.llm_provider if llm_rec else None,
                llm_rec.llm_model if llm_rec else None,
                state.get("prompt_version"),
                1 if (regime and regime.no_trade) else 0,
                state.get("no_trade_reason"),
                state.get("human_decision"),
                datetime.now().isoformat() if state.get("human_decision") else None,
                _to_json(scored),
                _to_json(llm_rec),
                json.dumps(state.get("errors", []), default=str),
                json.dumps(state.get("node_timings", {}), default=str),
                _state_to_json(state),
            ))
            conn.commit()
            return cursor.lastrowid
    except Exception as exc:
        log.error("[RecDB] save_recommendation failed: %s", exc)
        return -1


def update_decision(analysis_id: str, decision: str, execution_result: Optional[dict] = None):
    """Update human decision and execution result on an existing recommendation."""
    try:
        with get_connection() as conn:
            conn.execute("""
                UPDATE recommendations
                SET human_decision=?, decided_at=?, execution_result_json=?
                WHERE analysis_id=?
            """, (
                decision,
                datetime.now().isoformat(),
                json.dumps(execution_result or {}, default=str),
                analysis_id,
            ))
            conn.commit()
    except Exception as exc:
        log.error("[RecDB] update_decision failed: %s", exc)


def get_recommendation(analysis_id: str) -> Optional[Dict]:
    """
    Fetch a single recommendation by analysis_id.
    Returns the full_state_json (rich format) if available,
    else a flat row dict for backward compat.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM recommendations WHERE analysis_id=?", (analysis_id,)
            ).fetchone()
            if not row:
                return None
            row_dict = dict(row)
            full_json = row_dict.get("full_state_json")
            if full_json:
                try:
                    return json.loads(full_json)
                except Exception:
                    pass
            return row_dict
    except Exception as exc:
        log.error("[RecDB] get_recommendation failed: %s", exc)
        return None


def get_recent_recommendations(limit: int = 20) -> List[Dict]:
    """Fetch the most recent recommendations for the history view."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendations ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.error("[RecDB] get_recent_recommendations failed: %s", exc)
        return []
