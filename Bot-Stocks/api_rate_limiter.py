"""
API Rate Limiter — Token Bucket Algorithm
==========================================
Prevents broker API rate-limit violations by throttling requests.

Configuration in config.yml:
    api_rate_limit:
      enabled: true
      max_requests_per_second: 10  # Max calls per second
      burst_size: 15               # Allow short bursts (optional)

Usage:
    from api_rate_limiter import get_rate_limiter
    
    limiter = get_rate_limiter(config)
    with limiter:  # Blocks until token available
        response = requests.get(broker_api_url)
"""

import threading
import time
import logging
from typing import Optional

log = logging.getLogger("UTBotSRChannelsScanner")


class TokenBucketRateLimiter:
    """
    Thread-safe token bucket rate limiter.
    
    Algorithm:
    - Bucket holds up to `capacity` tokens
    - Tokens refill at `rate` tokens/second
    - Each API call consumes 1 token
    - If no tokens available, blocks until refill
    """
    
    def __init__(self, rate: float = 10.0, capacity: Optional[float] = None):
        """
        Parameters
        ----------
        rate : float
            Maximum requests per second (default: 10)
        capacity : float, optional
            Burst capacity (default: 1.5× rate, allows short bursts)
        """
        self.rate = max(0.1, rate)  # Minimum 0.1 req/sec
        self.capacity = capacity if capacity else rate * 1.5
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()
        
        log.info("Rate limiter initialized: %.1f req/sec, capacity=%.1f", 
                self.rate, self.capacity)
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # Add tokens proportional to time elapsed
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now
    
    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire one token (consume for one API call).
        
        Parameters
        ----------
        blocking : bool
            If True, wait until token available. If False, return immediately.
        timeout : float, optional
            Max seconds to wait (only if blocking=True)
        
        Returns
        -------
        bool
            True if token acquired, False if not available (non-blocking mode)
        """
        deadline = None
        if blocking and timeout is not None:
            deadline = time.monotonic() + timeout
        
        while True:
            with self.lock:
                self._refill()
                
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                
                if not blocking:
                    return False
                
                # Calculate wait time until next token
                wait_time = (1.0 - self.tokens) / self.rate
                
                if deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait_time = min(wait_time, remaining)
            
            # Sleep outside the lock
            time.sleep(min(wait_time, 0.1))  # Wake up every 100ms to check
    
    def __enter__(self):
        """Context manager support: with limiter: ..."""
        self.acquire(blocking=True)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        return False


class NoOpRateLimiter:
    """Dummy rate limiter when rate limiting is disabled."""
    
    def acquire(self, blocking=True, timeout=None):
        return True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


# Global rate limiter instance (one per process)
_global_limiter: Optional[TokenBucketRateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter(config: dict):
    """
    Get the global rate limiter instance (singleton).
    
    Creates on first call, reuses on subsequent calls.
    """
    global _global_limiter
    
    rl_cfg = config.get("api_rate_limit", {}) or {}
    enabled = rl_cfg.get("enabled", True)  # Default: enabled
    
    if not enabled:
        return NoOpRateLimiter()
    
    with _limiter_lock:
        if _global_limiter is None:
            rate = float(rl_cfg.get("max_requests_per_second", 10))
            burst = rl_cfg.get("burst_size")
            
            if burst:
                _global_limiter = TokenBucketRateLimiter(rate=rate, capacity=float(burst))
            else:
                _global_limiter = TokenBucketRateLimiter(rate=rate)
        
        return _global_limiter


def reset_rate_limiter():
    """Reset the global limiter (useful for tests or config reload)."""
    global _global_limiter
    with _limiter_lock:
        _global_limiter = None
