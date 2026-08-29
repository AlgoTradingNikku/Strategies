"""
Circuit Breaker Pattern
========================
Stops operations after consecutive failures to prevent cascading errors.

States:
- CLOSED: Normal operation
- OPEN: Failures exceeded threshold, blocking all calls
- HALF_OPEN: Testing if system recovered

Configuration:
    Use programmatically (no config needed):
    breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=300)
"""

import time
import logging
from enum import Enum
from typing import Callable, Any

log = logging.getLogger("UTBotSRChannelsScanner")


class CircuitState(Enum):
    CLOSED = "CLOSED"         # Normal operation
    OPEN = "OPEN"             # Blocking calls
    HALF_OPEN = "HALF_OPEN"   # Testing recovery


class CircuitBreaker:
    """
    Circuit breaker implementation.
    
    After `failure_threshold` consecutive failures, enters OPEN state
    and blocks all calls for `timeout_seconds`. Then tries one call
    (HALF_OPEN). If succeeds, returns to CLOSED. If fails, stays OPEN.
    """
    
    def __init__(self, failure_threshold: int = 3, timeout_seconds: float = 300):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Raises
        ------
        Exception
            If circuit is OPEN or function raises
        """
        if self.state == CircuitState.OPEN:
            # Check if timeout elapsed
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) > self.timeout_seconds:
                log.info("Circuit breaker timeout elapsed, entering HALF_OPEN state")
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception(
                    f"Circuit breaker OPEN - blocked after {self.failure_count} failures. "
                    f"Retry after {self.timeout_seconds}s timeout."
                )
        
        try:
            result = func(*args, **kwargs)
            
            # Success - reset failures
            if self.state == CircuitState.HALF_OPEN:
                log.info("Circuit breaker test call succeeded, returning to CLOSED")
                self.state = CircuitState.CLOSED
            
            self.failure_count = 0
            self.last_failure_time = None
            return result
            
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                log.critical(
                    "🚨 CIRCUIT BREAKER OPEN after %d consecutive failures. "
                    "Last error: %s", 
                    self.failure_count, str(e)
                )
            
            raise
    
    def reset(self):
        """Manually reset circuit breaker."""
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        log.info("Circuit breaker manually reset to CLOSED")
    
    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
