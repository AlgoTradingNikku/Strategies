"""
===============================================================================
  rate_limiter.py — In-memory token-bucket rate limiter (Sprint 5)
===============================================================================
Simple per-client-IP throttle for /api/* endpoints. Zero external deps.

Design:
  * One bucket per client IP; capacity = `per_minute`, refills at
    `per_minute / 60` tokens per second.
  * Each request consumes 1 token. If bucket empty -> HTTP 429.
  * Bucket state is process-local (fine for single-node deployment).
  * Health/root paths are always exempt so the dashboard never self-DoSes.
  * Fail-open: any internal error skips throttling for that request.

Enable via config:
    bot.rate_limit.enabled     : bool (default false)
    bot.rate_limit.per_minute  : int  (default 120)
    bot.rate_limit.exempt_paths: list[str] (defaults below)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Tuple

log = logging.getLogger("UTBotSRChannelsScanner")

_DEFAULT_EXEMPT = ("/", "/api/health", "/api/metrics", "/static", "/favicon.ico")


class TokenBucketLimiter:
    """Thread-safe token bucket keyed by client identifier (usually IP)."""

    def __init__(self, per_minute: int, exempt_paths: Tuple[str, ...] = _DEFAULT_EXEMPT):
        self.rate_per_sec: float = max(0.1, float(per_minute) / 60.0)
        self.capacity: float = float(max(1, per_minute))
        self.exempt_paths: Tuple[str, ...] = tuple(exempt_paths or ())
        self._lock = threading.Lock()
        self._state: Dict[str, Tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def is_exempt(self, path: str) -> bool:
        path = path or ""
        for p in self.exempt_paths:
            if path == p or path.startswith(p.rstrip("/") + "/"):
                return True
        return False

    def allow(self, key: str) -> bool:
        """Consume 1 token for `key`. Return True if allowed, False if throttled."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (self.capacity, now))
            # Refill based on time elapsed
            tokens = min(self.capacity, tokens + (now - last) * self.rate_per_sec)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - 1.0, now)
            return True


def build_rate_limit_middleware(cfg: dict) -> Callable | None:
    """
    Return an ASGI middleware factory if rate-limiting is enabled in cfg,
    otherwise None. Caller (app.py) should skip `app.middleware` when None.
    """
    r = (cfg or {}).get("bot", {}).get("rate_limit", {}) or {}
    if not bool(r.get("enabled", False)):
        return None
    try:
        per_min = int(r.get("per_minute", 120))
    except Exception:
        per_min = 120
    exempt: List[str] = list(r.get("exempt_paths") or _DEFAULT_EXEMPT)

    limiter = TokenBucketLimiter(per_minute=per_min, exempt_paths=tuple(exempt))

    async def middleware(request, call_next):
        try:
            path = request.url.path
            if limiter.is_exempt(path):
                return await call_next(request)
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                # Local import so this module can be imported without FastAPI.
                from fastapi.responses import JSONResponse
                log.warning("[ratelimit] 429 client=%s path=%s", client, path)
                # [Sprint-6] Metric — fail-open import.
                try:
                    import metrics as _m
                    _m.record_rate_limit_block()
                except Exception:
                    pass
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate_limit_exceeded", "per_minute": per_min},
                )
        except Exception as exc:
            # Fail-open: never block a real request because of our own bug.
            log.debug("[ratelimit] middleware error (allowing request): %s", exc)
        return await call_next(request)

    log.info("[ratelimit] enabled: %d req/min per IP (exempt=%s)", per_min, exempt)
    return middleware
