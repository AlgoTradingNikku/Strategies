"""
UTBot (UT Bot) Trend Following Indicator

This is the EXACT logic extracted from the original live_trader.py:202-283.
No algorithm changes - just wrapped in the BaseIndicator plugin interface.

The UTBot uses ATR-based trailing stops to identify trend direction and generate signals.
"""

from .base import BaseIndicator, IndicatorSignal
import pandas as pd
from typing import Dict, Any


class UTBotIndicator(BaseIndicator):
    """
    UT Bot Trend Following Indicator.
    
    Based on ATR (Average True Range) trailing stops to identify trends.
    Generates signals when price crosses the trailing stop level.
    
    Supports both regular candles and Heikin Ashi candles.
    
    Parameters:
        sensitivity: ATR multiplier for trail distance (default: 1.0)
                    Lower = more sensitive (more signals)
                    Higher = less sensitive (fewer signals)
        atr_period: Period for ATR calculation (default: 10)
                   Standard is 10-14 for intraday
    
    Signals:
        1: Fresh Buy (price crosses above trail)
        -1: Fresh Sell (price crosses below trail)
        2: Pullback Buy (still bullish, red-to-green bounce)
        -2: Pullback Sell (still bearish, green-to-red reversal)
        0: No signal
    """
    
    @property
    def required_params(self) -> list[str]:
        return ["sensitivity", "atr_period"]
    
    @property
    def warmup_period(self) -> int:
        return self.params.get("atr_period", 10) + 5
    
    def calculate(self, df: pd.DataFrame, use_ha: bool = True, **kwargs) -> IndicatorSignal:
        """
        Calculate UTBot trend and signals.
        
        Args:
            df: OHLC DataFrame with standard and HA candles
            use_ha: If True, use Heikin Ashi values for calculation
            **kwargs: Override sensitivity and atr_period
        """
        # Get parameters with overrides
        sensitivity = kwargs.get("sensitivity", self.params.get("sensitivity", 2.0))
        atr_period = kwargs.get("atr_period", self.params.get("atr_period", 1))
        
        # === DATA VALIDATION ===
        required_cols = ["Open", "High", "Low", "Close"]
        if use_ha:
            required_cols.extend(["HA_Open", "HA_High", "HA_Low", "HA_Close"])
        
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")
        
        if len(df) < self.warmup_period:
            raise ValueError(
                f"Insufficient data: {len(df)} bars, need {self.warmup_period}"
            )
        
        # === SOURCE SELECTION (HA or Regular) ===
        src = df['HA_Close'] if use_ha else df['Close']
        high = df['HA_High'] if use_ha else df['High']
        low = df['HA_Low'] if use_ha else df['Low']
        open_ = df['HA_Open'] if use_ha else df['Open']
        close = df['HA_Close'] if use_ha else df['Close']
        
        # === ATR CALCULATION (RMA version to match TradingView) ===
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()
        nLoss = sensitivity * atr
        
        # === UTBOT TRAIL AND POSITION CALCULATION ===
        trail = [0.0] * len(df)
        pos = [0] * len(df)
        signals = [0] * len(df)
        
        for i in range(atr_period, len(df)):
            s = src.iloc[i]
            prev_s = src.iloc[i-1]
            loss = nLoss.iloc[i]
            prev_trail = trail[i-1]
            
            # Trail calculation (exact original logic from live_trader.py)
            if s > prev_trail and prev_s > prev_trail:
                curr_trail = max(prev_trail, s - loss)
            elif s < prev_trail and prev_s < prev_trail:
                curr_trail = min(prev_trail, s + loss)
            elif s > prev_trail:
                curr_trail = s - loss
            else:
                curr_trail = s + loss
            
            trail[i] = curr_trail
            
            # Position & Signal Calculation
            prev_p = pos[i-1]
            
            # 1. Fresh Crossover Detection
            if prev_s < prev_trail and s > prev_trail:
                pos[i] = 1
                signals[i] = 1  # Fresh BUY
            elif prev_s > prev_trail and s < prev_trail:
                pos[i] = -1
                signals[i] = -1  # Fresh SELL
            else:
                # 3. Carry forward trend or Initialize (Fix for Stuck Trend Bug)
                if prev_p == 0:
                    pos[i] = 1 if s > prev_trail else -1
                else:
                    pos[i] = prev_p
                
                # Pullback Detection (Still Bullish / Still Bearish)
                # Logic: Current is Bullish/Bearish State AND (Prev Candle was Opposite Color) 
                # AND (Curr Candle is My Color)
                # This catches the 'Still Bullish' (Red-to-Green bounce) and 
                # 'Still Bearish' (Green-to-Red pivot)
                
                # Check for color using chosen source (HA or Standard)
                curr_is_green = src.iloc[i] > open_.iloc[i]
                prev_is_green = src.iloc[i-1] > open_.iloc[i-1]
                
                if prev_p == 1:  # Already Bullish
                    # A pullback is a Red candle followed by a Green candle bounce
                    if not prev_is_green and curr_is_green:
                        signals[i] = 2  # Still Bullish (Pullback Entry)
                elif prev_p == -1:  # Already Bearish
                    # A pullback is a Green candle followed by a Red candle reversal
                    if prev_is_green and not curr_is_green:
                        signals[i] = -2  # Still Bearish (Pullback Entry)
        
        # === RETURN STANDARDIZED SIGNAL ===
        return IndicatorSignal(
            trend=pos[-1],
            signal=signals[-1],
            strength=1.0,  # UTBot is binary (always confident when it signals)
            metadata={
                "stop_level": trail[-1],
                "atr": atr.iloc[-1],
                "trend_series": pd.Series(pos, index=df.index),  # For trend age calculation
                "signal_series": pd.Series(signals, index=df.index),  # For debugging
                "trail_series": pd.Series(trail, index=df.index),  # For visualization
            }
        )
    
    def get_trend_age(self, indicator_signal: IndicatorSignal) -> int:
        """
        Calculate how many candles the current trend has been active.
        
        Args:
            indicator_signal: Previous IndicatorSignal containing trend_series
            
        Returns:
            Number of consecutive candles in current trend
        """
        if "trend_series" not in indicator_signal.metadata:
            return 0
        
        pos_series = indicator_signal.metadata["trend_series"]
        if len(pos_series) < 2:
            return 0
        
        curr = pos_series.iloc[-2]  # Use confirmed candle
        count = 0
        for i in range(2, len(pos_series) + 1):
            if pos_series.iloc[-i] == curr:
                count += 1
            else:
                break
        return count
