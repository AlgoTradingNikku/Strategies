"""
HalfTrend Indicator

Python implementation of the HalfTrend indicator by Alex Orekhov (everget).
Original PineScript: https://www.tradingview.com/script/... (GPL-3.0)

The HalfTrend uses amplitude-based highest/lowest price detection combined with
SMA channels to identify trend direction. ATR-based channel deviation provides
dynamic support/resistance bands.

Signal convention matches UTBot for drop-in replacement:
    1: Fresh Buy  (trend flips from bearish to bullish)
   -1: Fresh Sell (trend flips from bullish to bearish)
    2: Pullback Buy  (still bullish, red-to-green candle bounce)
   -2: Pullback Sell (still bearish, green-to-red candle reversal)
    0: No signal
"""

from .base import BaseIndicator, IndicatorSignal
import pandas as pd
import numpy as np
from typing import Dict, Any


class HalfTrendIndicator(BaseIndicator):
    """
    HalfTrend Trend Following Indicator.
    
    Uses amplitude-period highest/lowest bars and SMA channels to detect trends.
    ATR(100)/2 is used for channel deviation bands.
    
    Supports both regular candles and Heikin Ashi candles.
    
    Parameters:
        amplitude: Period for highest/lowest bar detection (default: 2)
        channel_deviation: Multiplier for ATR-based channel width (default: 2)
    
    Signals:
        1: Fresh Buy (trend flips 1→0, i.e. bearish→bullish)
       -1: Fresh Sell (trend flips 0→1, i.e. bullish→bearish)
        2: Pullback Buy (still bullish, red-to-green bounce)
       -2: Pullback Sell (still bearish, green-to-red reversal)
        0: No signal
    """
    
    @property
    def required_params(self) -> list[str]:
        return ["amplitude", "channel_deviation"]
    
    @property
    def warmup_period(self) -> int:
        # ATR(100) uses RMA (exponential smoothing, alpha=1/100) which converges quickly.
        # Unlike SMA, RMA doesn't need 100 full bars — it starts producing values from bar 1.
        # We need enough bars for: amplitude window + some ATR stabilization + buffer.
        # 20 bars is practical minimum; matches PineScript behavior where signals appear early.
        return max(20, self.params.get("amplitude", 2) + 15)
    
    def calculate(self, df: pd.DataFrame, use_ha: bool = True, **kwargs) -> IndicatorSignal:
        """
        Calculate HalfTrend trend and signals.
        
        Args:
            df: OHLC DataFrame with standard and HA candles
            use_ha: If True, use Heikin Ashi values for calculation
            **kwargs: Override amplitude and channel_deviation
        """
        # Get parameters with overrides
        amplitude = kwargs.get("amplitude", self.params.get("amplitude", 2))
        channel_deviation = kwargs.get("channel_deviation", self.params.get("channel_deviation", 2))
        
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
        high = df['HA_High'].values if use_ha else df['High'].values
        low = df['HA_Low'].values if use_ha else df['Low'].values
        close = df['HA_Close'].values if use_ha else df['Close'].values
        open_ = df['HA_Open'].values if use_ha else df['Open'].values
        
        n = len(df)
        
        # === ATR(100) / 2 ===
        # True Range
        tr = np.empty(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1])
            )
        
        # ATR using RMA (Wilder's smoothing, alpha=1/100) to match TradingView
        atr_period = 100
        atr = np.empty(n)
        atr[0] = tr[0]
        alpha = 1.0 / atr_period
        for i in range(1, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]
        
        atr2 = atr / 2.0
        dev = channel_deviation * atr2
        
        # === PRECOMPUTE: Highest high and lowest low over amplitude period ===
        # Equivalent to: high[abs(ta.highestbars(amplitude))] and low[abs(ta.lowestbars(amplitude))]
        # ta.highestbars returns offset of highest bar in last `amplitude` bars (including current)
        # So highPrice = high at the index of the highest high in the window
        # lowPrice  = low  at the index of the lowest low   in the window
        highPrice = np.empty(n)
        lowPrice = np.empty(n)
        
        for i in range(n):
            start = max(0, i - amplitude + 1)
            window_high = high[start:i + 1]
            window_low = low[start:i + 1]
            # highest bar's high value
            highPrice[i] = high[start + np.argmax(window_high)]
            # lowest bar's low value
            lowPrice[i] = low[start + np.argmin(window_low)]
        
        # SMA of high and low over amplitude period
        highma = np.empty(n)
        lowma = np.empty(n)
        for i in range(n):
            start = max(0, i - amplitude + 1)
            highma[i] = np.mean(high[start:i + 1])
            lowma[i] = np.mean(low[start:i + 1])
        
        # === HALFTREND CORE LOGIC ===
        # State variables (matching PineScript var declarations)
        trend_arr = np.zeros(n, dtype=int)
        nextTrend_arr = np.zeros(n, dtype=int)
        maxLowPrice_arr = np.zeros(n)
        minHighPrice_arr = np.zeros(n)
        up_arr = np.zeros(n)
        down_arr = np.zeros(n)
        atrHigh_arr = np.zeros(n)
        atrLow_arr = np.zeros(n)
        
        # Arrow signals (NaN = no signal)
        arrowUp_arr = np.full(n, np.nan)
        arrowDown_arr = np.full(n, np.nan)
        
        # Initialize bar 0
        maxLowPrice_arr[0] = low[0]
        minHighPrice_arr[0] = high[0]
        
        # Output arrays
        pos = np.zeros(n, dtype=int)  # 1=Bullish, -1=Bearish
        signals = np.zeros(n, dtype=int)
        ht = np.zeros(n)
        
        for i in range(1, n):
            # Carry forward state from previous bar
            trend = trend_arr[i - 1]
            nextTrend = nextTrend_arr[i - 1]
            maxLowP = maxLowPrice_arr[i - 1]
            minHighP = minHighPrice_arr[i - 1]
            prev_up = up_arr[i - 1]
            prev_down = down_arr[i - 1]
            
            arrowUp = np.nan
            arrowDown = np.nan
            
            # --- nextTrend logic ---
            if nextTrend == 1:
                maxLowP = max(lowPrice[i], maxLowP)
                
                if highma[i] < maxLowP and close[i] < (low[i - 1] if i > 0 else low[i]):
                    trend = 1
                    nextTrend = 0
                    minHighP = highPrice[i]
            else:  # nextTrend == 0
                minHighP = min(highPrice[i], minHighP)
                
                if lowma[i] > minHighP and close[i] > (high[i - 1] if i > 0 else high[i]):
                    trend = 0
                    nextTrend = 1
                    maxLowP = lowPrice[i]
            
            # --- up/down calculation ---
            prev_trend = trend_arr[i - 1]
            
            if trend == 0:
                if prev_trend != 0:
                    # Trend just flipped to bullish
                    cur_up = prev_down if not np.isnan(prev_down) and prev_down != 0 else prev_down
                    arrowUp = cur_up - atr2[i]
                else:
                    cur_up = max(maxLowP, prev_up) if prev_up != 0 and not np.isnan(prev_up) else maxLowP
                
                up_arr[i] = cur_up
                down_arr[i] = prev_down  # carry forward
                atrHigh_arr[i] = cur_up + dev[i]
                atrLow_arr[i] = cur_up - dev[i]
                ht[i] = cur_up
            else:  # trend == 1 (bearish in PineScript convention)
                if prev_trend != 1:
                    # Trend just flipped to bearish
                    cur_down = prev_up if not np.isnan(prev_up) and prev_up != 0 else prev_up
                    arrowDown = cur_down + atr2[i]
                else:
                    cur_down = min(minHighP, prev_down) if prev_down != 0 and not np.isnan(prev_down) else minHighP
                
                down_arr[i] = cur_down
                up_arr[i] = prev_up  # carry forward
                atrHigh_arr[i] = cur_down + dev[i]
                atrLow_arr[i] = cur_down - dev[i]
                ht[i] = cur_down
            
            # Store state
            trend_arr[i] = trend
            nextTrend_arr[i] = nextTrend
            maxLowPrice_arr[i] = maxLowP
            minHighPrice_arr[i] = minHighP
            arrowUp_arr[i] = arrowUp
            arrowDown_arr[i] = arrowDown
            
            # === SIGNAL GENERATION ===
            # PineScript: buySignal = not na(arrowUp) and trend == 0 and trend[1] == 1
            # PineScript: sellSignal = not na(arrowDown) and trend == 1 and trend[1] == 0
            buySignal = (not np.isnan(arrowUp)) and (trend == 0) and (prev_trend == 1)
            sellSignal = (not np.isnan(arrowDown)) and (trend == 1) and (prev_trend == 0)
            
            # Map to standardized signal convention:
            # trend==0 in PineScript = Bullish → pos=1
            # trend==1 in PineScript = Bearish → pos=-1
            if trend == 0:
                pos[i] = 1  # Bullish
            else:
                pos[i] = -1  # Bearish
            
            if buySignal:
                signals[i] = 1   # Fresh Buy
            elif sellSignal:
                signals[i] = -1  # Fresh Sell
            else:
                # Pullback detection (same logic as UTBot for compatibility)
                curr_is_green = close[i] > open_[i]
                prev_is_green = close[i - 1] > open_[i - 1]
                
                if pos[i] == 1:  # Already Bullish
                    if not prev_is_green and curr_is_green:
                        signals[i] = 2  # Pullback Buy
                elif pos[i] == -1:  # Already Bearish
                    if prev_is_green and not curr_is_green:
                        signals[i] = -2  # Pullback Sell
        
        # === RETURN STANDARDIZED SIGNAL ===
        return IndicatorSignal(
            trend=int(pos[-1]),
            signal=int(signals[-1]),
            strength=1.0,  # HalfTrend is binary (always confident when it signals)
            metadata={
                "stop_level": float(ht[-1]),
                "atr": float(atr2[-1]),  # ATR/2 value (matches atr2 in PineScript)
                "atr_high": float(atrHigh_arr[-1]),
                "atr_low": float(atrLow_arr[-1]),
                "trend_series": pd.Series(pos.tolist(), index=df.index),
                "signal_series": pd.Series(signals.tolist(), index=df.index),
                "trail_series": pd.Series(ht.tolist(), index=df.index),
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
