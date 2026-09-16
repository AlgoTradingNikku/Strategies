"""
persistence/settings_db.py
===========================
SQLite CRUD helpers for the platform_settings table.
Thin wrapper re-exported so other modules can import from here.
The actual table is initialised by settings_service.py on first import.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("UTBotSRChannelsScanner")
DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_table():
    """Ensure platform_settings table exists."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


def get_all() -> Dict[str, Any]:
    """Return all settings as {key: value} dict. Values are JSON-decoded."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM platform_settings").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except Exception:
            result[row["key"]] = row["value"]
    return result


def set_value(key: str, value: Any) -> None:
    """Persist a single key-value pair."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO platform_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), now),
        )
        conn.commit()


def reset_to_defaults(defaults: Dict[str, Any]) -> None:
    """Replace all settings with the supplied defaults dict."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute("DELETE FROM platform_settings")
        conn.executemany(
            "INSERT INTO platform_settings (key, value, updated_at) VALUES (?, ?, ?)",
            [(k, json.dumps(v), now) for k, v in defaults.items()],
        )
        conn.commit()
