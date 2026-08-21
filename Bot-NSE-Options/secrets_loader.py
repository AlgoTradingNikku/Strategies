"""
===============================================================================
  secrets_loader.py — Environment / .env secret overrides (Sprint 5)
===============================================================================
Lets operators keep API keys and Telegram tokens *out* of `config.yml` (which
lives in the repo). At startup we mutate the loaded cfg in-place so downstream
code stays unchanged.

Precedence (highest wins):
    1. Environment variable
    2. `.env` file (if python-dotenv installed & file exists)
    3. `config.yml`

Recognised environment variables:
    OPENALGO_APIKEY        -> openalgo.apikey
    OPENALGO_USERNAME      -> openalgo.username
    OPENALGO_BASE_URL      -> openalgo.base_url
    OPENALGO_WS_URL        -> openalgo.ws_url
    TELEGRAM_BOT_TOKEN     -> telegram.bot_token
    TELEGRAM_CHAT_ID       -> telegram.chat_id

Fail-open on every step — missing dotenv, missing .env, missing env var all
leave cfg untouched.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Tuple

log = logging.getLogger("UTBotSRChannelsScanner")

_bot_dir = Path(__file__).resolve().parent
_DOTENV_PATH = _bot_dir / ".env"

# Mapping of env-var name -> (section, key) inside cfg
_ENV_MAP: Dict[str, Tuple[str, str]] = {
    "OPENALGO_APIKEY": ("openalgo", "apikey"),
    "OPENALGO_USERNAME": ("openalgo", "username"),
    "OPENALGO_BASE_URL": ("openalgo", "base_url"),
    "OPENALGO_WS_URL": ("openalgo", "ws_url"),
    "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
}

_dotenv_loaded = False


def _try_load_dotenv() -> None:
    """Load .env into os.environ if python-dotenv is available. Idempotent."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True  # set first so retries don't hammer imports

    if not _DOTENV_PATH.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=str(_DOTENV_PATH), override=False)
        log.info("[secrets] Loaded .env from %s", _DOTENV_PATH)
    except ImportError:
        # dotenv not installed — parse minimal KEY=VALUE ourselves.
        try:
            with open(_DOTENV_PATH, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            log.info("[secrets] Loaded .env (fallback parser) from %s", _DOTENV_PATH)
        except Exception as exc:
            log.debug("[secrets] .env fallback parse failed: %s", exc)
    except Exception as exc:
        log.debug("[secrets] load_dotenv failed: %s", exc)


def apply_env_overrides(cfg: dict) -> dict:
    """
    Mutate `cfg` in place, replacing broker/telegram credentials with any
    matching environment-variable values. Returns the same dict for chaining.
    """
    if not isinstance(cfg, dict):
        return cfg
    _try_load_dotenv()

    applied: list[str] = []
    for env_name, (section, key) in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if not val:
            continue
        cfg.setdefault(section, {})
        if not isinstance(cfg[section], dict):
            continue
        cfg[section][key] = val
        applied.append(f"{section}.{key}")

    if applied:
        log.info("[secrets] Applied %d env override(s): %s", len(applied), ", ".join(applied))
    return cfg


def summarize_secret_sources(cfg: dict) -> dict:
    """
    Report where each critical secret came from — useful in /api/health.
    Values are the source label only ("env" | "config" | "missing"), never
    the secret itself.
    """
    out: Dict[str, str] = {}
    for env_name, (section, key) in _ENV_MAP.items():
        env_val = os.environ.get(env_name)
        cfg_val = ((cfg or {}).get(section, {}) or {}).get(key, "")
        label = f"{section}.{key}"
        if env_val:
            out[label] = "env"
        elif cfg_val:
            out[label] = "config"
        else:
            out[label] = "missing"
    return out
