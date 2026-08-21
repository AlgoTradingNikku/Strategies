"""
===============================================================================
  broker_retry.py — Exponential-backoff retry helper (Sprint 5)
===============================================================================
Thin wrapper for broker HTTP calls that may fail intermittently.

Usage:
    from broker_retry import with_retry

    def _do_place():
        return client.placeorder(...)

    result = with_retry(_do_place, cfg=cfg, op_name="place_order")

Behavior:
  * Reads `bot.retry.enabled`, `bot.retry.max_attempts`, `bot.retry.backoff_base_sec`
    from cfg. Fail-open: any read error uses safe defaults.
  * On non-retryable exception -> re-raises immediately.
  * On retryable exception -> logs at WARNING, sleeps backoff * 2^attempt,
    tries again up to max_attempts. Final failure re-raises the LAST exception.
  * Never swallows exceptions silently — caller sees either the return value
    or the original exception.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Iterable, Optional, Tuple, Type

log = logging.getLogger("UTBotSRChannelsScanner")

# [Sprint-6] Metrics — imported lazily-safe (module has no heavy imports itself).
try:
    import metrics as _metrics  # noqa
except Exception:  # pragma: no cover
    _metrics = None

# Default retryable exception types — network / IO / broker errors.
# We deliberately do NOT retry on ValueError / KeyError / TypeError (programming errors).
_DEFAULT_RETRYABLE: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# `requests` is an optional dependency in this codebase — import defensively.
try:
    import requests as _requests
    _DEFAULT_RETRYABLE = _DEFAULT_RETRYABLE + (
        _requests.exceptions.ConnectionError,
        _requests.exceptions.Timeout,
        _requests.exceptions.ChunkedEncodingError,
    )
except Exception:  # pragma: no cover
    pass


def _read_retry_cfg(cfg: Optional[dict]) -> Tuple[bool, int, float]:
    """Return (enabled, max_attempts, backoff_base_sec) with safe defaults."""
    try:
        r = (cfg or {}).get("bot", {}).get("retry", {}) or {}
        enabled = bool(r.get("enabled", True))
        max_attempts = max(1, int(r.get("max_attempts", 3)))
        backoff = max(0.0, float(r.get("backoff_base_sec", 0.5)))
        return enabled, max_attempts, backoff
    except Exception:
        return True, 3, 0.5


def with_retry(
    fn: Callable[[], Any],
    *,
    cfg: Optional[dict] = None,
    op_name: str = "broker_call",
    retryable: Optional[Iterable[Type[BaseException]]] = None,
    max_attempts: Optional[int] = None,
    backoff_base_sec: Optional[float] = None,
) -> Any:
    """
    Execute `fn()` with exponential-backoff retry.

    Parameters
    ----------
    fn                  : zero-arg callable to invoke
    cfg                 : full config dict (reads bot.retry.*)
    op_name             : short label used in log lines
    retryable           : iterable of exception classes considered transient;
                          defaults to network / IO errors
    max_attempts        : override cfg
    backoff_base_sec    : override cfg (first sleep is backoff_base * 1)
    """
    cfg_enabled, cfg_attempts, cfg_backoff = _read_retry_cfg(cfg)

    attempts = int(max_attempts if max_attempts is not None else cfg_attempts)
    backoff = float(backoff_base_sec if backoff_base_sec is not None else cfg_backoff)
    retry_types: Tuple[Type[BaseException], ...] = tuple(retryable) if retryable else _DEFAULT_RETRYABLE

    # If retries disabled globally, treat as single attempt.
    if not cfg_enabled:
        attempts = 1

    last_exc: Optional[BaseException] = None
    for i in range(attempts):
        try:
            result = fn()
            # [Sprint-6] Record how many attempts we used (i+1 == success attempt).
            if _metrics is not None:
                try:
                    _metrics.record_retry(op_name, i + 1, exhausted=False)
                except Exception:
                    pass
            return result
        except retry_types as exc:
            last_exc = exc
            if i >= attempts - 1:
                log.error(
                    "[retry] %s exhausted after %d attempt(s): %s",
                    op_name, attempts, exc,
                )
                # [Sprint-6] Record final failure — all attempts used.
                if _metrics is not None:
                    try:
                        _metrics.record_retry(op_name, attempts, exhausted=True)
                    except Exception:
                        pass
                raise
            # Exponential + jitter to avoid thundering-herd
            sleep_for = backoff * (2 ** i) + random.uniform(0, backoff / 2 if backoff else 0)
            log.warning(
                "[retry] %s attempt %d/%d failed (%s); sleeping %.2fs",
                op_name, i + 1, attempts, exc, sleep_for,
            )
            try:
                time.sleep(sleep_for)
            except Exception:
                pass
        except Exception:
            # Non-retryable — propagate immediately.
            raise

    # Unreachable, but keeps type-checkers happy.
    if last_exc is not None:
        raise last_exc
    return None
