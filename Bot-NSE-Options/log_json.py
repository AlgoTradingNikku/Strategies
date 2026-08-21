"""
===============================================================================
  log_json.py — Optional JSON log formatter for machine ingestion (Sprint 6)
===============================================================================
Attaches a *second* RotatingFileHandler to the `UTBotSRChannelsScanner` logger
that emits one JSON object per line. Ideal for Loki / ELK / CloudWatch Logs
consumers.

Enabled via cfg:
    bot.log_json: true                  # master toggle (default false)
    bot.log_json_file: logs/bot.jsonl   # optional path override

Fields per record:
    ts        : ISO-8601 UTC timestamp with millisecond precision
    level     : DEBUG / INFO / WARNING / ERROR / CRITICAL
    logger    : record.name
    msg       : formatted message (%-args already substituted)
    module    : record.module
    func      : record.funcName
    line      : record.lineno
    exc_info  : optional traceback string (only when the record had one)

Idempotent — re-invocation replaces the prior JSON handler in-place.
Fail-open — any error is logged at DEBUG and startup continues.
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

_LOGGER_NAME = "UTBotSRChannelsScanner"
_MARK = "_bot_nse_json_handler"

_bot_dir = Path(__file__).resolve().parent


class JsonFormatter(logging.Formatter):
    """Serialize each LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        # Convert the record's create-time to an ISO-8601 string (UTC, ms).
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)

        payload: Dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Preserve any structured `extra=` fields attached by callers.
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_"):
                continue
            if k in ("args", "asctime", "created", "exc_info", "exc_text",
                     "filename", "levelname", "levelno", "lineno", "message",
                     "module", "msecs", "msg", "name", "pathname", "process",
                     "processName", "relativeCreated", "stack_info", "thread",
                     "threadName", "funcName"):
                continue
            try:
                json.dumps(v)  # only include JSON-safe extras
                payload[k] = v
            except Exception:
                payload[k] = str(v)

        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            # Last-ditch fallback so we never drop a record.
            return json.dumps({"ts": ts, "level": record.levelname, "msg": str(msg)})


def setup_json_logging(cfg: Optional[Dict[str, Any]] = None) -> Optional[logging.Handler]:
    """
    Attach the JSON rotating handler if enabled. Idempotent.
    Returns the handler (or None if disabled / on failure).
    """
    cfg = cfg or {}
    bot_cfg = cfg.get("bot", {}) if isinstance(cfg, dict) else {}
    if not bool(bot_cfg.get("log_json", False)):
        return None

    log = logging.getLogger(_LOGGER_NAME)

    # Remove any prior JSON handler we owned.
    for h in list(log.handlers):
        if getattr(h, _MARK, False):
            try:
                h.close()
            except Exception:
                pass
            log.removeHandler(h)

    try:
        rel_path = str(bot_cfg.get("log_json_file", "logs/bot.jsonl"))
        json_path = (_bot_dir / rel_path).resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            max_bytes = int(bot_cfg.get("log_max_bytes", 10 * 1024 * 1024))
        except Exception:
            max_bytes = 10 * 1024 * 1024
        try:
            backup_count = int(bot_cfg.get("log_backup_count", 5))
        except Exception:
            backup_count = 5

        handler = RotatingFileHandler(
            filename=str(json_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter())
        handler.setLevel(log.level or logging.INFO)
        setattr(handler, _MARK, True)
        log.addHandler(handler)
        log.info("[log_json] JSON handler attached: %s (max=%dB backups=%d)",
                 json_path, max_bytes, backup_count)
        return handler
    except Exception as exc:
        log.debug("[log_json] setup failed: %s", exc)
        return None


def get_json_log_path(cfg: Optional[Dict[str, Any]] = None) -> Path:
    """Return the resolved JSON log path (does not create it)."""
    cfg = cfg or {}
    bot_cfg = cfg.get("bot", {}) if isinstance(cfg, dict) else {}
    rel_path = str(bot_cfg.get("log_json_file", "logs/bot.jsonl"))
    return (_bot_dir / rel_path).resolve()
