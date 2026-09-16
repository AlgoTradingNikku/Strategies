"""
ai/workflow/settings_service.py
================================
Settings service — persists platform toggle state to SQLite.
config.yml toggles: section = factory defaults.
SQLite settings table = live runtime state.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")

DB_PATH = _bot_dir / "trades.db"

# Keys that are permanently locked (cannot be changed via toggle API)
_LOCKED_KEYS = {"risk_auto_execution"}


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_settings_table():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


_init_settings_table()


class SettingsService:
    """
    Manages platform toggle state with SQLite persistence.
    Config.yml provides factory defaults; DB holds live state.
    """

    def __init__(self, cfg: dict = None):
        self._cfg = cfg
        if cfg:
            self._ensure_defaults_loaded(cfg)

    def _ensure_defaults_loaded(self, cfg: dict):
        """Load config.yml toggle defaults into DB if not already set."""
        defaults = cfg.get("toggles", {})
        from datetime import datetime
        with _get_conn() as conn:
            for key, value in defaults.items():
                existing = conn.execute(
                    "SELECT key FROM platform_settings WHERE key=?", (key,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO platform_settings (key, value, updated_at) VALUES (?,?,?)",
                        (key, json.dumps(value), datetime.now().isoformat())
                    )
            conn.commit()

    def get_settings(self) -> "PlatformSettings":
        """Load all toggle states from DB and return as PlatformSettings."""
        from ai.schemas.settings import PlatformSettings
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM platform_settings"
                ).fetchall()
            db_values = {row["key"]: json.loads(row["value"]) for row in rows}
            # Enforce locked keys
            db_values["risk_auto_execution"] = False
            return PlatformSettings(**{
                k: v for k, v in db_values.items()
                if k in PlatformSettings.model_fields
            })
        except Exception as exc:
            log.warning("[Settings] get_settings failed: %s — using defaults", exc)
            from ai.schemas.settings import PlatformSettings
            return PlatformSettings()

    def update_toggle(self, key: str, value: bool) -> "PlatformSettings":
        """Update a single toggle. Raises ValueError for locked keys."""
        if key in _LOCKED_KEYS:
            raise ValueError(f"Toggle '{key}' is locked and cannot be changed")
        from datetime import datetime
        from ai.schemas.settings import PlatformSettings
        if key not in PlatformSettings.model_fields:
            raise ValueError(f"Unknown toggle key: '{key}'")
        with _get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO platform_settings (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(bool(value)), datetime.now().isoformat()))
            conn.commit()
        log.info("[Settings] Toggle updated: %s = %s", key, value)
        return self.get_settings()

    def reset_to_defaults(self, cfg: dict = None) -> "PlatformSettings":
        """Reset all toggles to config.yml defaults."""
        c = cfg or self._cfg
        if c is None:
            import yaml
            c = yaml.safe_load(open(_bot_dir / "config.yml"))
        defaults = c.get("toggles", {})
        from datetime import datetime
        with _get_conn() as conn:
            for key, value in defaults.items():
                conn.execute("""
                    INSERT OR REPLACE INTO platform_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, json.dumps(value), datetime.now().isoformat()))
            conn.commit()
        log.info("[Settings] All toggles reset to config.yml defaults")
        return self.get_settings()

    def get_all_as_dict(self) -> dict:
        """Return all toggle states as a plain dict for API responses."""
        settings = self.get_settings()
        return settings.model_dump()


_service: SettingsService = None


def get_settings_service() -> SettingsService:
    global _service
    if _service is None:
        import yaml
        cfg = yaml.safe_load(open(_bot_dir / "config.yml"))
        _service = SettingsService(cfg=cfg)
    return _service
