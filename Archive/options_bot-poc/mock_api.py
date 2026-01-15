import pandas as pd
import numpy as np
import logging

class MockAPI:
    def __init__(self, api_key=None, host=None):
        self.logger = logging.getLogger("MockAPI")
        self.logger.info("Initialized Mock OpenAlgo API")
        
    def get_profile(self):
        return {"status": "success", "name": "Trader"}
        
    def history(self, symbol, resolution, start, end):
        """Generates random OHLC data for testing."""
        # resolution is in minutes strings usually
        rows = 500
        dates = pd.date_range(end=pd.Timestamp.now(), periods=rows, freq=f'{resolution}min')
        
        df = pd.DataFrame(index=dates)
        df['open'] = np.random.uniform(23000, 23500, size=rows)
        df['high'] = df['open'] + np.random.uniform(0, 50, size=rows)
        df['low'] = df['open'] - np.random.uniform(0, 50, size=rows)
        df['close'] = np.random.uniform(df['low'], df['high'])
        df['volume'] = np.random.randint(1000, 50000, size=rows)
        
        return df
        
    def get_quotes(self, symbol):
        return {"ltp": 23450.0}
