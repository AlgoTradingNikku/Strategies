"""
Historical Data Fetcher for Backtesting

Fetches historical candle data from OpenAlgo API and prepares it for simulation.
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import sys
import os

# Add parent directory to path to import from main bot
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class DataFetcher:
    """Fetches and caches historical market data from OpenAlgo"""
    
    def __init__(self, api_client):
        """
        Args:
            api_client: OpenAlgoREST instance
        """
        self.api = api_client
        self.logger = logging.getLogger("DataFetcher")
        self.cache = {}
        
    def fetch_historical_data(self, 
                             symbol: str,
                             exchange: str,
                             interval: str,
                             start_date: str,
                             end_date: str) -> pd.DataFrame:
        """
        Fetch historical OHLC data for a symbol.
        
        Args:
            symbol: e.g., "NIFTY", "BANKNIFTY"
            exchange: e.g., "NSE_INDEX"
            interval: e.g., "3m", "15m", "1h"
            start_date: "YYYY-MM-DD"
            end_date: "YYYY-MM-DD"
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        cache_key = f"{symbol}_{interval}_{start_date}_{end_date}"
        
        if cache_key in self.cache:
            self.logger.info(f"Using cached data for {cache_key}")
            return self.cache[cache_key]
        
        self.logger.info(f"Fetching {symbol} {interval} data from {start_date} to {end_date}...")
        
        try:
            # Use the existing OpenAlgoREST.history method
            df = self.api.history(
                symbol=symbol,
                resolution=interval,
                start=start_date,
                end=end_date,
                exchange=exchange
            )
            
            if df.empty:
                self.logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()
            
            # Ensure lowercase column names for consistency
            df.columns = df.columns.str.lower()
            
            # Convert timestamp to datetime if it's a string
            if 'timestamp' in df.columns or 'time' in df.columns:
                time_col = 'timestamp' if 'timestamp' in df.columns else 'time'
                df['timestamp'] = pd.to_datetime(df[time_col])
                df.sort_values('timestamp', inplace=True)
                df.reset_index(drop=True, inplace=True)
            
            self.logger.info(f"Fetched {len(df)} candles for {symbol} ({interval})")
            
            # Cache it
            self.cache[cache_key] = df.copy()
            
            return df
            
        except Exception as e:
            self.logger.error(f"Error fetching data: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()
    
    def prepare_data_for_backtest(self,
                                  symbol: str,
                                  htf_interval: str,
                                  ltf_interval: str,
                                  start_date: str,
                                  end_date: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch both HTF and LTF data needed for the strategy.
        
        Returns:
            Dictionary with 'htf' and 'ltf' DataFrames
        """
        exchange = "NSE_INDEX" if symbol in ["NIFTY", "BANKNIFTY"] else "NSE"
        
        htf_data = self.fetch_historical_data(symbol, exchange, htf_interval, start_date, end_date)
        ltf_data = self.fetch_historical_data(symbol, exchange, ltf_interval, start_date, end_date)
        
        return {
            'htf': htf_data,
            'ltf': ltf_data
        }
    
    def get_trading_days(self, start_date: str, end_date: str) -> List[str]:
        """
        Get list of trading days (excluding weekends and holidays).
        For now, just excludes weekends. Can be enhanced with holiday calendar.
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        trading_days = []
        current = start
        
        while current <= end:
            # Exclude Saturday (5) and Sunday (6)
            if current.weekday() < 5:
                trading_days.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        
        return trading_days
