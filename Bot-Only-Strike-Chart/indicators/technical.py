"""
Technical Indicator Suite
Provides EMA, RSI, and ADX calculations.
"""

from .base import BaseIndicator, IndicatorSignal
import pandas as pd
import numpy as np
from typing import Dict, Any


class TechnicalIndicator(BaseIndicator):
    """
    Suite of technical indicators: EMA, RSI, ADX.
    
    This indicator is designed to provide filtering metrics rather than 
    independent buy/sell signals.
    
    Parameters:
        ema_periods: List of periods for EMA calculation (e.g., [50, 200])
        rsi_period: Period for RSI (default: 14)
        adx_period: Period for ADX (default: 14)
    """
    
    @property
    def required_params(self) -> list[str]:
        return []  # Most are optional with defaults
    
    @property
    def warmup_period(self) -> int:
        return max(self.params.get("ema_periods", [200])) + 50
    
    def calculate(self, df: pd.DataFrame, use_ha: bool = False) -> IndicatorSignal:
        """Calculate technical metrics"""
        src = df['HA_Close'] if use_ha else df['Close']
        high = df['HA_High'] if use_ha else df['High']
        low = df['HA_Low'] if use_ha else df['Low']
        open_ = df['HA_Open'] if use_ha else df['Open']
        volume = df['Volume']
        
        results = {}
        
        # 1. EMA Calculations
        ema_periods = self.params.get("ema_periods", [9, 21, 50, 200])
        for p in ema_periods:
            results[f"ema_{p}"] = src.ewm(span=p, adjust=False).mean()
            
        # 2. RSI Calculation (Wilder's Smoothing)
        rsi_p = self.params.get("rsi_period", 14)
        delta = src.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/rsi_p, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/rsi_p, adjust=False).mean()
        rs = gain / loss
        results["rsi"] = 100 - (100 / (1 + rs))
        
        # 3. ADX Calculation (Wilder's Smoothing)
        adx_p = self.params.get("adx_period", 14)
        tr = pd.concat([
            high - low,
            (high - src.shift()).abs(),
            (low - src.shift()).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/adx_p, adjust=False).mean()
        results["atr"] = atr
        
        up_move = high.diff()
        down_move = low.shift() - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/adx_p, adjust=False).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/adx_p, adjust=False).mean() / atr)
        
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        results["adx"] = dx.ewm(alpha=1/adx_p, adjust=False).mean()
        
        # 4. VWAP Calculation (Anchored to start of dataframe for simplicity in this context)
        # Note: True VWAP anchors to session start. We approximate using the loaded data window.
        vwap = (df['Close'] * volume).cumsum() / volume.cumsum()
        results["vwap"] = vwap
        
        # 5. Volume SMA (5-period)
        results["vol_ma_5"] = volume.rolling(window=5).mean()
        
        # 6. Candle Analysis (Current Candle)
        body_size = (src - open_).abs()
        upper_wick = high - pd.concat([src, open_], axis=1).max(axis=1)
        lower_wick = pd.concat([src, open_], axis=1).min(axis=1) - low
        
        # Upper Wick > Body? (Rejection)
        results["wick_rejection"] = upper_wick > body_size
        
        # Higher Low? (Structure)
        results["higher_low"] = low > low.shift(1)
        
        return IndicatorSignal(
            trend=0,
            signal=0,
            strength=1.0,
            metadata={
                "emas": {p: results[f"ema_{p}"].iloc[-1] for p in ema_periods},
                "rsi": results["rsi"].iloc[-1],
                "adx": results["adx"].iloc[-1],
                "atr": results["atr"].iloc[-1],
                "vwap": results["vwap"].iloc[-1],
                "vol_ma_5": results["vol_ma_5"].iloc[-1],
                "volume": volume.iloc[-1],
                "wick_rejection": results["wick_rejection"].iloc[-1],
                "higher_low": results["higher_low"].iloc[-1],
                # Full series
                "rsi_series": results["rsi"],
                "adx_series": results["adx"],
                "atr_series": results["atr"]
            }
        )
