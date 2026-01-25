"""
Market Data Provider - Async wrapper around OpenAlgo API.

Provides non-blocking data fetching for prices and historical data.
"""

import asyncio
from typing import Optional, Dict, List
import pandas as pd
from datetime import datetime, timedelta
from .cache import MarketDataCache


class MarketDataProvider:
    """
    Async data provider for market data.
    
    Wraps the OpenAlgo API client with async methods and caching.
    Uses MarketDataCache to reduce redundant API calls.
    
    Example:
        provider = MarketDataProvider(api_client, cache)
        
        # Async fetch
        price = await provider.get_live_price("NIFTY50")
        df = await provider.fetch_history("NIFTY50", "3m", 100)
    """
    
    def __init__(self, api_client, cache: Optional[MarketDataCache] = None, config: Optional[dict] = None):
        """
        Initialize data provider.
        
        Args:
            api_client: OpenAlgo API client instance
            cache: Optional MarketDataCache instance
            config: Optional bot configuration dict
        """
        self.client = api_client
        self.cache = cache or MarketDataCache()
        self.config = config or {}
    
    async def close(self):
        """Cleanup resources (placeholder for future async resources)"""
        pass
    
    # === LIVE PRICES ===
    
    async def get_live_price(self, symbol: str, exchange: str = "NSE") -> Optional[float]:
        """
        Get live price for a symbol.
        
        Checks cache first, falls back to API if miss.
        
        Args:
            symbol: Symbol to fetch
            exchange: Exchange (NSE, NFO, etc.)
            
        Returns:
            Current price or None if error
        """
        # Check cache first
        cached = self.cache.get_price(symbol)
        if cached is not None:
            return cached
        
        # Fetch from API (async)
        try:
            # Run sync API call in executor to avoid blocking
            loop = asyncio.get_event_loop()
            quote = await loop.run_in_executor(
                None, 
                lambda: self.client.quotes(symbol=symbol, exchange=exchange)
            )
            
            if quote and 'lp' in quote:
                price = float(quote['lp'])
                self.cache.set_price(symbol, price)
                return price
            
        except Exception as e:
            print(f"Error fetching price for {symbol}: {e}")
        
        return None
    
    async def get_multiple_prices(self, symbols: List[str], exchange: str = "NSE") -> Dict[str, float]:
        """
        Fetch prices for multiple symbols concurrently.
        
        Args:
            symbols: List of symbols
            exchange: Exchange
            
        Returns:
            Dict mapping symbol -> price
        """
        tasks = [self.get_live_price(sym, exchange) for sym in symbols]
        prices = await asyncio.gather(*tasks, return_exceptions=True)
        
        result = {}
        for symbol, price in zip(symbols, prices):
            if isinstance(price, (int, float)):
                result[symbol] = price
        
        return result
    
    # === HISTORICAL DATA ===
    
    async def fetch_history(
        self,
        symbol: str,
        timeframe: str,
        bars: int = 100,
        exchange: str = "NSE"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLC data.
        
        Args:
            symbol: Symbol to fetch
            timeframe: Timeframe (e.g., "3m", "15m", "1h")
            bars: Number of bars to fetch
            exchange: Exchange
            
        Returns:
            DataFrame with OHLC + HA columns or None if error
        """
        # Check cache first
        cached = self.cache.get_history(symbol, timeframe)
        if cached is not None and len(cached) >= bars:
            return cached.tail(bars)
        
        # Fetch from API
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)  # Adjust based on timeframe
            
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(
                None,
                self._fetch_history_sync,
                symbol,
                exchange,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
                timeframe
            )
            
            if df is not None and len(df) > 0:
                # Add Heikin Ashi columns
                df = self._add_heikin_ashi(df)
                
                # Cache it
                self.cache.set_history(symbol, timeframe, df)
                
                return df.tail(bars)
            
        except Exception as e:
            print(f"Error fetching history for {symbol}: {e}")
        
        return None
    
    def _fetch_history_sync(
        self, 
        symbol: str, 
        exchange: str, 
        start_date: str, 
        end_date: str,
        interval: str
    ) -> Optional[pd.DataFrame]:
        """Synchronous history fetch (called in executor)"""
        try:
            # Call OpenAlgo API with correct signature
            raw = self.client.history(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date
            )
            
            # Normalize Data (same logic as PureOptionsStrategy.py)
            df = pd.DataFrame()
            
            if isinstance(raw, pd.DataFrame):
                df = raw.copy()
            elif isinstance(raw, dict):
                if "data" in raw:
                    df = pd.DataFrame(raw["data"])
                else:
                    try:
                        df = pd.DataFrame(raw)
                    except:
                        return None
            elif isinstance(raw, list):
                df = pd.DataFrame(raw)
            
            if df.empty:
                return None
            
            # Format Columns & Index
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            elif "time" in df.columns:
                df["timestamp"] = pd.to_datetime(df["time"])
                df = df.set_index("timestamp")
            else:
                try:
                    df.index = pd.to_datetime(df.index)
                    df.index.name = "timestamp"
                except:
                    pass
            
            # Standardize Columns
            col_map = {
                "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
                "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
            }
            df.rename(columns=col_map, inplace=True)
            
            # Ensure required columns exist
            if not all(col in df.columns for col in ['Open', 'High', 'Low', 'Close']):
                return None
            
            # Ensure Numeric
            for col in ["Open", "High", "Low", "Close"]:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df.dropna(inplace=True)
            
            return df
            
        except Exception as e:
            print(f"Sync history fetch error: {e}")
            return None
    
    def _add_heikin_ashi(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Heikin Ashi columns to DataFrame"""
        df = df.copy()
        
        # HA Close
        df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
        
        # HA Open
        ha_open = [df['Open'].iloc[0]]
        for i in range(1, len(df)):
            ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
        df['HA_Open'] = ha_open
        
        # HA High and Low
        df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
        df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
        
        return df
    
    # === MASTER / INSTRUMENT DATA ===
    
    async def get_lot_size(self, symbol: str, exchange: str = "NFO") -> int:
        """
        Get lot size for a symbol from config.
        
        Args:
            symbol: Symbol (e.g., "NIFTY24JAN21500CE")
            exchange: Exchange
            
        Returns:
            Lot size (int) from config
        """
        # We now prefer lot size from config as requested
        lot_size = self.config.get("nifty_lot_size", 65)
        
        # print(f"[DEBUG] Using configured lot size for {symbol}: {lot_size}")
        return int(lot_size)
    
    # === WEBSOCKET (Future) ===
    
    async def subscribe_symbols(self, symbols: List[str]):
        """Subscribe to real-time updates (WebSocket - future implementation)"""
        # Placeholder for WebSocket implementation
        pass
    
    async def unsubscribe_symbols(self, symbols: List[str]):
        """Unsubscribe from real-time updates"""
        pass
