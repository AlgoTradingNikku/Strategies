"""
Market Data Cache - TTL-based caching to reduce API calls.

Caches price data, historical data, and indicator calculations to improve performance.
"""

from cachetools import TTLCache
from typing import Optional, Dict, Any
import pandas as pd
from datetime import datetime


class MarketDataCache:
    """
    Time-To-Live based caching for market data.
    
    Reduces redundant API calls by caching:
    - Live prices (1s TTL)
    - Historical data (60s TTL)
    - HTF data (3min TTL)
    
    Example:
        cache = MarketDataCache()
        
        # Try to get cached price
        price = cache.get_price("NIFTY50")
        if price is None:
            price = fetch_from_api("NIFTY50")
            cache.set_price("NIFTY50", price)
    """
    
    def __init__(self):
        """Initialize caches with TTL settings"""
        # Live prices: 1 second TTL (very fresh)
        self._price_cache = TTLCache(maxsize=100, ttl=1)
        
        # Historical data: 60 second TTL
        self._history_cache = TTLCache(maxsize=50, ttl=60)
        
        # HTF data: 3 minute TTL (changes less frequently)
        self._htf_cache = TTLCache(maxsize=50, ttl=180)
        
        # Indicator calculations: 5 second TTL
        self._indicator_cache = TTLCache(maxsize=100, ttl=5)
        
        # Stats for monitoring
        self._stats = {
            "price_hits": 0,
            "price_misses": 0,
            "history_hits": 0,
            "history_misses": 0,
        }
    
    # === PRICE CACHE ===
    
    def get_price(self, symbol: str) -> Optional[float]:
        """
        Get cached live price.
        
        Args:
            symbol: Symbol to look up
            
        Returns:
            Cached price or None if not found/expired
        """
        if symbol in self._price_cache:
            self._stats["price_hits"] += 1
            return self._price_cache[symbol]
        
        self._stats["price_misses"] += 1
        return None
    
    def set_price(self, symbol: str, price: float):
        """Cache a live price"""
        self._price_cache[symbol] = price
    
    def update_prices(self, prices: Dict[str, float]):
        """Batch update multiple prices"""
        for symbol, price in prices.items():
            self._price_cache[symbol] = price
    
    # === HISTORY CACHE ===
    
    def get_history(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """
        Get cached historical data.
        
        Args:
            symbol: Symbol to look up
            timeframe: Timeframe (e.g., "3m", "15m")
            
        Returns:
            Cached DataFrame or None
        """
        key = f"{symbol}_{timeframe}"
        if key in self._history_cache:
            self._stats["history_hits"] += 1
            return self._history_cache[key]
        
        self._stats["history_misses"] += 1
        return None
    
    def set_history(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """Cache historical data"""
        key = f"{symbol}_{timeframe}"
        self._history_cache[key] = df.copy()  # Store a copy to avoid mutations
    
    # === HTF CACHE ===
    
    def get_htf(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Get cached Higher TimeFrame data"""
        key = f"htf_{symbol}_{timeframe}"
        if key in self._htf_cache:
            return self._htf_cache[key]
        return None
    
    def set_htf(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """Cache HTF data"""
        key = f"htf_{symbol}_{timeframe}"
        self._htf_cache[key] = df.copy()
    
    # === INDICATOR CACHE ===
    
    def get_indicator(self, symbol: str, indicator_name: str, params: str) -> Optional[Any]:
        """
        Get cached indicator result.
        
        Args:
            symbol: Symbol
            indicator_name: Indicator name (e.g., "utbot")
            params: Stringified params for cache key
            
        Returns:
            Cached indicator signal or None
        """
        key = f"{symbol}_{indicator_name}_{params}"
        return self._indicator_cache.get(key)
    
    def set_indicator(self, symbol: str, indicator_name: str, params: str, result: Any):
        """Cache indicator result"""
        key = f"{symbol}_{indicator_name}_{params}"
        self._indicator_cache[key] = result
    
    # === MASTER CONTRACT CACHE ===
    
    def get_master_info(self, symbol: str) -> Optional[Dict]:
        """Get cached master info (lot size, etc.)"""
        # Master cache is simple dict (no TTL needed for session)
        if not hasattr(self, "_master_cache"):
            self._master_cache = {}
        return self._master_cache.get(symbol)

    def set_master_info(self, symbol: str, info: Dict):
        """Cache master info"""
        if not hasattr(self, "_master_cache"):
            self._master_cache = {}
        self._master_cache[symbol] = info
    
    # === UTILITIES ===
    
    def invalidate_symbol(self, symbol: str):
        """Invalidate all caches for a symbol (e.g., on data source change)"""
        # Remove from price cache
        self._price_cache.pop(symbol, None)
        
        # Remove from history/htf caches
        keys_to_remove = [k for k in self._history_cache.keys() if symbol in k]
        for key in keys_to_remove:
            self._history_cache.pop(key, None)
        
        keys_to_remove = [k for k in self._htf_cache.keys() if symbol in k]
        for key in keys_to_remove:
            self._htf_cache.pop(key, None)
        
        keys_to_remove = [k for k in self._indicator_cache.keys() if symbol in k]
        for key in keys_to_remove:
            self._indicator_cache.pop(key, None)
    
    def clear_all(self):
        """Clear all caches"""
        self._price_cache.clear()
        self._history_cache.clear()
        self._htf_cache.clear()
        self._indicator_cache.clear()
    
    def get_stats(self) -> dict:
        """
        Get cache performance statistics.
        
        Returns:
            Dict with hit/miss ratios
        """
        total_price = self._stats["price_hits"] + self._stats["price_misses"]
        total_history = self._stats["history_hits"] + self._stats["history_misses"]
        
        return {
            **self._stats,
            "price_hit_rate": (
                self._stats["price_hits"] / total_price if total_price > 0 else 0
            ),
            "history_hit_rate": (
                self._stats["history_hits"] / total_history if total_history > 0 else 0
            ),
            "price_cache_size": len(self._price_cache),
            "history_cache_size": len(self._history_cache),
        }
