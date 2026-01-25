"""
Helper Utilities - Common patterns extracted for reuse

Reduces code duplication across the bot.
"""

import asyncio
from typing import Callable, Any, TypeVar
from functools import wraps
import logging
import threading


logger = logging.getLogger(__name__)
T = TypeVar('T')


async def async_wrap(sync_func: Callable[..., T], *args, **kwargs) -> T:
    """
    Wrap synchronous function for async execution.
    
    Eliminates duplicate executor pattern:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, func, args)
    
    Example:
        result = await async_wrap(client.quotes, symbol="NIFTY", exchange="NSE")
    
    Args:
        sync_func: Synchronous function to execute
        *args, **kwargs: Arguments to pass to function
        
    Returns:
        Function result
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))


def retry_on_error(retries: int = 3, delay: float = 0.5, exceptions=(Exception,)):
    """
    Decorator for automatic retry on failure.
    
    Example:
        @retry_on_error(retries=3, delay=1.0)
        def fetch_data():
            return api.get_data()
    
    Args:
        retries: Number of retry attempts
        delay: Delay between retries (seconds)
        exceptions: Tuple of exceptions to catch
        
    Returns:
        Decorated function
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt < retries - 1:
                        logger.warning(f"Retry {attempt + 1}/{retries} for {func.__name__}: {e}")
                        import time
                        time.sleep(delay)
                    else:
                        logger.error(f"Failed after {retries} attempts: {func.__name__}")
                        raise
        return wrapper
    return decorator


class ThreadSafeFileWriter:
    """
    Thread-safe file writer for CSV reporting.
    
    Prevents race conditions when multiple trades exit simultaneously.
    
    Example:
        writer = ThreadSafeFileWriter("trades.csv")
        writer.write(["2026-01-25", "NIFTY25500CE", "100", "PROFIT"])
    """
    
    def __init__(self, filepath: str):
        """
        Initialize thread-safe file writer.
        
        Args:
            filepath: Path to file
        """
        self.filepath = filepath
        self.lock = threading.Lock()
    
    def write(self, row: list, header: list = None):
        """
        Write row to CSV file (thread-safe).
        
        Args:
            row: List of values to write
            header: Optional header row (only written if file doesn't exist)
        """
        import csv
        import os
        
        with self.lock:
            file_exists = os.path.isfile(self.filepath)
            
            with open(self.filepath, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Write header if file is new and header provided
                if not file_exists and header:
                    writer.writerow(header)
                
                # Write data row
                writer.writerow(row)
    
    async def async_write(self, row: list, header: list = None):
        """
        Async version of write (runs in executor to avoid blocking).
        
        Args:
            row: List of values to write
            header: Optional header row
        """
        await async_wrap(self.write, row, header)


def get_source_columns(df, use_ha: bool = False):
    """
    Get source column names based on HA mode.
    
    Eliminates duplicate code in indicators.
    
    Args:
        df: DataFrame
        use_ha: If True, return HA column names
        
    Returns:
        dict: {"open": col, "high": col, "low": col, "close": col}
    """
    if use_ha:
        return {
            "open": "HA_Open",
            "high": "HA_High",
            "low": "HA_Low",
            "close": "HA_Close"
        }
    else:
        return {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close"
        }


def format_error_message(error: Exception, context: str = "") -> str:
    """
    Format error message with context for better debugging.
    
    Args:
        error: Exception object
        context: Additional context (e.g., "fetching data for NIFTY25500CE")
        
    Returns:
        Formatted error string
    """
    error_type = type(error).__name__
    error_msg = str(error)
    
    if context:
        return f"{context} | {error_type}: {error_msg}"
    else:
        return f"{error_type}: {error_msg}"