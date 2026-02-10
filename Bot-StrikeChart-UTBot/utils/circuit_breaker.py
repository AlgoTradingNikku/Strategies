"""
Circuit Breaker - Fail fast pattern for API failures

Prevents spamming API when service is down.
Automatically opens circuit after N consecutive failures.
"""

import time
from enum import Enum
from typing import Callable, Any, Optional
from datetime import datetime, timedelta
import logging


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Failing, rejecting calls
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker for API calls.
    
    States:
    - CLOSED: Normal operation, all calls go through
    - OPEN: Too many failures, reject calls immediately
    - HALF_OPEN: Testing recovery, allow limited calls
    
    Example:
        breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        
        result = breaker.call(lambda: api.get_data())
        if result is None:
            print("Circuit is OPEN, API is down")
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            timeout: Seconds to wait before attempting recovery (OPEN -> HALF_OPEN)
            expected_exception: Exception type to count as failure
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = CircuitState.CLOSED
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to call
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Function result or None if circuit is OPEN
        """
        # Check if circuit should transition to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                logger.info("[CIRCUIT] Attempting recovery (OPEN -> HALF_OPEN)")
            else:
                # Still in timeout period, reject call
                return None
        
        try:
            # Execute function
            result = func(*args, **kwargs)
            
            # Success! Reset failure counter
            if self.state == CircuitState.HALF_OPEN:
                self._on_success()
            elif self.failure_count > 0:
                self.failure_count = 0
                
            return result
            
        except self.expected_exception as e:
            # Failure detected
            self._on_failure()
            logger.warning(f"[CIRCUIT] Call failed: {e}")
            return None
    
    def _on_success(self):
        """Handle successful call"""
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logger.info("[CIRCUIT] Recovery successful (HALF_OPEN -> CLOSED)")
    
    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(
                f"[CIRCUIT] Too many failures ({self.failure_count}). "
                f"Opening circuit for {self.timeout}s"
            )
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self.last_failure_time is None:
            return True
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.timeout
    
    def reset(self):
        """Manually reset circuit breaker"""
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        logger.info("[CIRCUIT] Manually reset")
    
    def get_state(self) -> CircuitState:
        """Get current circuit state"""
        return self.state
    
    def is_available(self) -> bool:
        """Check if circuit allows calls"""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                return True  # Will transition to HALF_OPEN on next call
            return False
        return True