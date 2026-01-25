"""
Utilities Module - Helper functions and common patterns

Exports:
- ConfigValidator: Schema validation for config.yaml
- CircuitBreaker: Fail-fast pattern for API failures
- ThreadSafeFileWriter: Thread-safe CSV writing
- async_wrap: Async wrapper for sync functions
- retry_on_error: Retry decorator
- get_source_columns: HA/Regular column selector
- format_error_message: Error formatting with context
"""

from .config_validator import ConfigValidator, ConfigValidationError
from .circuit_breaker import CircuitBreaker, CircuitState
from .helpers import (
    async_wrap,
    retry_on_error,
    ThreadSafeFileWriter,
    get_source_columns,
    format_error_message
)

__all__ = [
    'ConfigValidator',
    'ConfigValidationError',
    'CircuitBreaker',
    'CircuitState',
    'ThreadSafeFileWriter',
    'async_wrap',
    'retry_on_error',
    'get_source_columns',
    'format_error_message'
]