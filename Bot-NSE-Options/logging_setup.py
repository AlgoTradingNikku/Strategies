"""
===============================================================================
  logging_setup.py — Centralized rotating logger (Sprint 5)
===============================================================================
Provides a single, idempotent `setup_logging(cfg)` entry point that:

  * Uses the same logger name as scanner.py (`UTBotSRChannelsScanner`)
    so every existing `logging.getLogger("UTBotSRChannelsScanner")` call
    picks up the new handlers automatically — zero touch to callers.
  * Attaches a RotatingFileHandler to `logs/bot.log` (default 10 MB × 5).
  * Keeps the flushing stdout handler for real-time console output.
  * Auto-creates the `logs/` directory.
  * Fail-open: any error in setup falls back to plain StreamHandler and
    never blocks app startup.
  * Idempotent: re-invocation replaces handlers instead of stacking them.

Config keys (all optional, safe defaults if missing):
  bot.log_level          : "DEBUG" | "INFO" | "WARNING" | "ERROR"   (default INFO)
  bot.log_max_bytes      : int rotation threshold                    (default 10 MB)
  bot.log_backup_count   : int rotated files to keep                 (default 5)
  bot.log_file           : override log path (relative to bot dir)   (default logs/bot.log)
"""

from __future__ import annotations

import io
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

_LOGGER_NAME = "UTBotSRChannelsScanner"
_bot_dir = Path(__file__).resolve().parent

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Sentinel attributes so we know which handlers we own and can safely replace.
_MARK_STREAM = "_bot_nse_stream_handler"
_MARK_FILE = "_bot_nse_rotating_handler"


class _FlushStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every record — matches scanner.py behavior."""

    def emit(self, record):  # pragma: no cover - trivial passthrough
        try:
            super().emit(record)
            self.flush()
        except Exception:
            pass


def _ensure_utf8_stdio() -> None:
    """Rewrap stdout/stderr in UTF-8 on Windows to prevent UnicodeEncodeError."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        enc = (getattr(stream, "encoding", "") or "").lower()
        if enc == "utf-8":
            continue
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


def setup_logging(cfg: Optional[Dict[str, Any]] = None) -> logging.Logger:
    """
    Configure the shared `UTBotSRChannelsScanner` logger. Safe to call more
    than once — replaces prior handlers owned by this module.

    Returns the configured logger.
    """
    cfg = cfg or {}
    bot_cfg = cfg.get("bot", {}) if isinstance(cfg, dict) else {}

    level_name = str(bot_cfg.get("log_level", "INFO")).upper().strip()
    level = _LEVELS.get(level_name, logging.INFO)

    try:
        max_bytes = int(bot_cfg.get("log_max_bytes", 10 * 1024 * 1024))
    except Exception:
        max_bytes = 10 * 1024 * 1024
    try:
        backup_count = int(bot_cfg.get("log_backup_count", 5))
    except Exception:
        backup_count = 5

    log_file_rel = str(bot_cfg.get("log_file", "logs/bot.log"))
    log_path = (_bot_dir / log_file_rel).resolve()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Directory creation failure -> fall back to bot dir root
        log_path = _bot_dir / "bot.log"

    _ensure_utf8_stdio()

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # Remove any prior handlers we installed so we don't double-log on re-setup.
    for h in list(logger.handlers):
        if getattr(h, _MARK_STREAM, False) or getattr(h, _MARK_FILE, False):
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    try:
        console = _FlushStreamHandler(sys.stdout)
        console.setFormatter(fmt)
        console.setLevel(level)
        setattr(console, _MARK_STREAM, True)
        logger.addHandler(console)
    except Exception:
        # Absolute worst-case fallback
        logger.addHandler(logging.StreamHandler())

    # Rotating file handler
    try:
        rot = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        rot.setFormatter(fmt)
        rot.setLevel(level)
        setattr(rot, _MARK_FILE, True)
        logger.addHandler(rot)
    except Exception as exc:
        logger.error("[logging_setup] Failed to attach rotating file handler: %s", exc)

    logger.debug(
        "[logging_setup] Level=%s file=%s max_bytes=%d backups=%d",
        level_name, log_path, max_bytes, backup_count,
    )
    return logger


def get_log_file_path(cfg: Optional[Dict[str, Any]] = None) -> Path:
    """Return the resolved log-file path (does not create it)."""
    cfg = cfg or {}
    bot_cfg = cfg.get("bot", {}) if isinstance(cfg, dict) else {}
    log_file_rel = str(bot_cfg.get("log_file", "logs/bot.log"))
    return (_bot_dir / log_file_rel).resolve()
