"""
regime_gate.py
==============
Sprint 2: Regime Gate — decides whether a signal from a given engine should be
allowed through based on the current market regime.

This is a thin wrapper around ``regime.should_enable_engine()`` that adds:
  * A single kill-switch (``regime.gate_enabled``, default ``false``) so
    Sprint 1.5 continues its "tag but don't filter" behaviour by default,
    letting operators collect baseline win-rate-by-regime data before
    turning the gate on.
  * A stable ``(ok, reason)`` return contract that matches
    ``risk_limits.check_can_open_new()`` so both gates compose naturally at
    the scanner's auto-order block.

Design notes
------------
* When ``gate_enabled: false`` the gate ALWAYS passes with reason="". The
  scanner still logs the current regime (from Sprint 1.5) and stores it on
  every persisted signal — the difference is only whether we skip trades.
* When ``gate_enabled: true`` the gate calls
  ``regime.should_enable_engine(regime, engine, config)`` and skips the
  signal when it returns False.
* We deliberately DO NOT hard-code any per-engine × per-regime policy
  here — the policy lives in ``config.yml → regime.policy`` and is merged
  with defaults inside ``regime.should_enable_engine``.

Return contract
---------------
    check_signal_allowed(engine, regime_label, config) -> (ok: bool, reason: str)

    ok=True, reason=""                — allow the signal
    ok=False, reason="<explanation>"  — skip; caller must log/alert with reason
"""

from __future__ import annotations
import logging
from typing import Tuple

import regime as _regime_module

log = logging.getLogger("UTBotSRChannelsScanner")


def _gate_cfg(config: dict) -> dict:
    """Return the ``regime`` sub-dict (never None)."""
    return config.get("regime", {}) or {}


def is_gate_enabled(config: dict) -> bool:
    """True when the regime gate should actively block signals.

    Default is False so Sprint 1.5's "tag but don't gate" contract holds
    unless the user opts in via ``regime.gate_enabled: true``.
    """
    return bool(_gate_cfg(config).get("gate_enabled", False))


def check_signal_allowed(
    engine: str,
    regime_label: str,
    config: dict,
) -> Tuple[bool, str]:
    """Decide whether to allow a signal from ``engine`` under ``regime_label``.

    Parameters
    ----------
    engine
        Engine identifier — currently ``"utbot"`` or ``"sr"`` (case-insensitive).
    regime_label
        One of ``"trending_up"``, ``"trending_down"``, ``"chop"``,
        ``"high_vol_chop"``, or ``"unknown"``.
    config
        Full config dict (reads ``regime`` sub-section).

    Returns
    -------
    (ok, reason) : tuple[bool, str]
    """
    if not is_gate_enabled(config):
        return True, ""

    engine_norm = str(engine or "").strip().lower()
    regime_norm = str(regime_label or "unknown").strip().lower()

    # ``unknown`` regime — permissive by default so we don't accidentally
    # block everything on cold-start or when NIFTY data is missing. Users
    # who want to be strict can set regime.policy.unknown.<engine>: false.
    try:
        enabled = _regime_module.should_enable_engine(regime_norm, engine_norm, config)
    except Exception as exc:      # pragma: no cover — policy-lookup failure
        log.warning(
            "regime_gate: should_enable_engine(%r, %r) raised %s — allowing.",
            regime_norm, engine_norm, exc,
        )
        return True, ""

    if enabled:
        return True, ""

    return False, f"regime_gate: {engine_norm} disabled in regime={regime_norm}"
