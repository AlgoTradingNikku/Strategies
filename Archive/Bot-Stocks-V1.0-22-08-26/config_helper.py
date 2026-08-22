"""
config_helper.py
================
Typed, path-based accessors for the plain-dict `config` object used across
Bot-Stocks.

This module is purely additive — the existing dict interface (`cfg.get(...)`,
`cfg["trade_management"]["stop_loss_pct"]`) continues to work unchanged.  The
helpers below are opt-in conveniences intended to:

  1. Give a single source of truth for default values.
  2. Prevent silent typos (e.g. `scan_timeframe` vs `candle_timeframe`).
  3. Coerce values to the correct Python type at read time.

Usage
-----
    from config_helper import cfg_get, get_candle_timeframe

    tf   = get_candle_timeframe(config)                    # str
    sl   = cfg_get(config, "trade_management.stop_loss_pct", 1.0, float)
    tiers= cfg_get(config, "trade_management.partial_exit.tiers", [], list)

The functions in this module never mutate the input dict.
"""

from __future__ import annotations
from typing import Any, Callable, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Generic dotted-path getter with type coercion
# ---------------------------------------------------------------------------

def cfg_get(cfg: dict, path: str, default: T, coerce: Callable[[Any], T] | None = None) -> T:
    """Return ``cfg[a][b][c]`` for path ``"a.b.c"``, or ``default`` if missing.

    Parameters
    ----------
    cfg     : root config dict (may be ``None`` — treated as empty)
    path    : dotted key path, e.g. ``"trade_management.stop_loss_pct"``
    default : value returned when any segment is missing or the leaf is ``None``
    coerce  : optional callable applied to the resolved value.  When coercion
              raises, ``default`` is returned instead.

    Notes
    -----
    * Empty string path returns the full dict.
    * Numeric strings like ``"1.5"`` coerce cleanly to ``float``.
    * ``coerce=bool`` treats the strings ``"false"``, ``"0"``, ``""``, ``"no"``
      as ``False`` (case-insensitive); everything else follows Python truthiness.
    """
    if cfg is None:
        return default
    if not path:
        return cfg  # type: ignore[return-value]

    node: Any = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]

    if node is None:
        return default

    if coerce is None:
        return node  # type: ignore[return-value]

    try:
        if coerce is bool and isinstance(node, str):
            return (node.strip().lower() not in ("", "0", "false", "no", "off"))  # type: ignore[return-value]
        return coerce(node)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Named, commonly-used accessors — one canonical spelling per concept
# ---------------------------------------------------------------------------

def get_candle_timeframe(cfg: dict, default: str = "5m") -> str:
    """Return the scan candle timeframe.

    Accepts both `candle_timeframe` (preferred) and the legacy `scan_timeframe`
    key so old configs continue to work.
    """
    if not isinstance(cfg, dict):
        return default
    return str(cfg.get("candle_timeframe") or cfg.get("scan_timeframe") or default)


def get_exchange(cfg: dict, default: str = "NSE") -> str:
    return str((cfg or {}).get("exchange", default)).upper()


def get_exchange_tz_name(cfg: dict) -> str:
    """Return the IANA timezone name matching the configured exchange."""
    exch = get_exchange(cfg)
    return "Asia/Kolkata" if exch in ("NSE", "BSE") else "UTC"


def is_trade_management_enabled(cfg: dict) -> bool:
    return cfg_get(cfg, "trade_management.enabled", False, bool)


def get_stop_loss_pct(cfg: dict, default: float = 1.0) -> float:
    return cfg_get(cfg, "trade_management.stop_loss_pct", default, float)


def get_target_pct(cfg: dict, default: float = 2.0) -> float:
    return cfg_get(cfg, "trade_management.target_pct", default, float)


def get_allowed_actions(cfg: dict, default: str = "BOTH") -> str:
    return str(cfg_get(cfg, "openalgo.allowed_actions", default, str)).upper()


def get_trading_api_source(cfg: dict, default: str = "openalgo") -> str:
    return str((cfg or {}).get("trading_api_source", default)).lower()


def get_data_source(cfg: dict, default: str = "yfinance") -> str:
    return str((cfg or {}).get("data_source", default)).lower()
