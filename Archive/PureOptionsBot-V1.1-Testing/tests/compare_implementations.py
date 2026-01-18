"""
Signal Comparison Tool - Validate new bot produces same signals as original.

This script runs UTBot indicator calculations on sample data using both:
1. Original calculate_utbot() from live_trader.py
2. New UTBotIndicator plugin from indicators/utbot.py

Then compares outputs to ensure 100% compatibility.
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Add paths
sys.path.insert(0, '.')

from indicators.utbot import UTBotIndicator
from indicators.base import IndicatorSignal


def create_sample_data(n=100, seed=42):
    """Create sample OHLC data"""
    np.random.seed(seed)
    
    # Realistic Nifty-like data
    base = 25000
    close = base + np.cumsum(np.random.randn(n) * 15)
    high = close + np.random.rand(n) * 25
    low = close - np.random.rand(n) * 25
    open_price = close + np.random.randn(n) * 10
    
    df = pd.DataFrame({
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": np.random.randint(5000, 50000, n)
    })
    
    # Add Heikin Ashi
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    return df


def calculate_utbot_original(df, sensitivity, atr_period, use_ha):
    """
    Original UTBot calculation from live_trader.py.
    
    This is the REFERENCE implementation to compare against.
    """
    src = df['HA_Close'] if use_ha else df['Close']
    high = df['HA_High'] if use_ha else df['High']
    low = df['HA_Low'] if use_ha else df['Low']
    close = df['HA_Close'] if use_ha else df['Close']
    
    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()
    nLoss = sensitivity * atr
    
    # Trail and position
    trail = [0.0] * len(df)
    pos = [0] * len(df)
    signals = [0] * len(df)
    
    for i in range(atr_period, len(df)):
        s = src.iloc[i]
        prev_s = src.iloc[i-1]
        loss = nLoss.iloc[i]
        prev_trail = trail[i-1]
        
        # Trail calculation
        if s > prev_trail and prev_s > prev_trail:
            curr_trail = max(prev_trail, s - loss)
        elif s < prev_trail and prev_s < prev_trail:
            curr_trail = min(prev_trail, s + loss)
        elif s > prev_trail:
            curr_trail = s - loss
        else:
            curr_trail = s + loss
        
        trail[i] = curr_trail
        
        # Position & Signal
        prev_p = pos[i-1]
        
        # Fresh crossover
        if prev_s < prev_trail and s > prev_trail:
            pos[i] = 1
            signals[i] = 1
        elif prev_s > prev_trail and s < prev_trail:
            pos[i] = -1
            signals[i] = -1
        else:
            # Carry forward or initialize
            if prev_p == 0:
                pos[i] = 1 if s > prev_trail else -1
            else:
                pos[i] = prev_p
            
            # Pullback detection
            curr_open, curr_close = df['Open'].iloc[i], df['Close'].iloc[i]
            prev_open, prev_close = df['Open'].iloc[i-1], df['Close'].iloc[i-1]
            
            if prev_p == 1:  # Bullish
                if prev_close < prev_open and curr_close > curr_open:
                    signals[i] = 2
            elif prev_p == -1:  # Bearish
                if prev_close > prev_open and curr_close < curr_open:
                    signals[i] = -2
    
    return pd.Series(pos, index=df.index), pd.Series(trail, index=df.index), pd.Series(signals, index=df.index)


def compare_implementations(df, sensitivity=1.0, atr_period=10, use_ha=True):
    """Compare original vs new implementation"""
    
    print(f"\n{'='*60}")
    print(f"Comparing UTBot Implementations")
    print(f"{'='*60}")
    print(f"Data: {len(df)} bars")
    print(f"Params: sensitivity={sensitivity}, atr_period={atr_period}, use_ha={use_ha}")
    
    # Original implementation
    print("\nRunning ORIGINAL implementation...")
    pos_old, trail_old, sig_old = calculate_utbot_original(df, sensitivity, atr_period, use_ha)
    
    # New plugin implementation
    print("Running NEW plugin implementation...")
    indicator = UTBotIndicator({"sensitivity": sensitivity, "atr_period": atr_period})
    signal_new = indicator.calculate(df, use_ha=use_ha)
    
    # Extract series from new implementation
    pos_new = signal_new.metadata["trend_series"]
    trail_new = signal_new.metadata["trail_series"]
    sig_new = signal_new.metadata["signal_series"]
    
    # Compare
    print(f"\n{'='*60}")
    print("COMPARISON RESULTS:")
    print(f"{'='*60}")
    
    # Compare trends
    trend_match = (pos_old == pos_new).sum()
    trend_total = len(pos_old)
    trend_pct = (trend_match / trend_total) * 100
    
    print(f"Trend Matching: {trend_match}/{trend_total} ({trend_pct:.2f}%)")
    
    # Compare signals
    signal_match = (sig_old == sig_new).sum()
    signal_total = len(sig_old)
    signal_pct = (signal_match / signal_total) * 100
    
    print(f"Signal Matching: {signal_match}/{signal_total} ({signal_pct:.2f}%)")
    
    # Compare trails
    trail_diff = (trail_old - trail_new).abs()
    max_trail_diff = trail_diff.max()
    avg_trail_diff = trail_diff.mean()
    
    print(f"Trail Stop Difference: Max={max_trail_diff:.4f}, Avg={avg_trail_diff:.4f}")
    
    # Final bar comparison
    print(f"\nFinal Bar Comparison:")
    print(f"  Original: Trend={pos_old.iloc[-1]}, Signal={sig_old.iloc[-1]}, Trail={trail_old.iloc[-1]:.2f}")
    print(f"  New:      Trend={signal_new.trend}, Signal={signal_new.signal}, Trail={signal_new.metadata['stop_level']:.2f}")
    
    # Verdict
    print(f"\n{'='*60}")
    if trend_pct == 100.0 and signal_pct == 100.0 and max_trail_diff < 0.01:
        print("[PASS] PASS: Implementations are IDENTICAL")
        print(f"{'='*60}")
        return True
    else:
        print(f"[FAIL] FAIL: Implementations differ!")
        print(f"{'='*60}")
        
        # Show first discrepancy
        trend_diff_idx = (pos_old != pos_new)
        if trend_diff_idx.any():
            first_diff = trend_diff_idx.idxmax()
            print(f"\nFirst trend discrepancy at index {first_diff}:")
            print(f"  Original: {pos_old.iloc[first_diff]}")
            print(f"  New: {pos_new.iloc[first_diff]}")
        
        return False


if __name__ == "__main__":
    print("UTBot Implementation Comparison Tool")
    print("=" * 60)
    
    # Test Case 1: Default params, HA enabled
    print("\n[TEST 1] Default params with Heikin Ashi")
    df1 = create_sample_data(100, seed=42)
    result1 = compare_implementations(df1, sensitivity=1.0, atr_period=10, use_ha=True)
    
    # Test Case 2: Different sensitivity
    print("\n\n[TEST 2] Higher sensitivity")
    df2 = create_sample_data(100, seed=123)
    result2 = compare_implementations(df2, sensitivity=2.0, atr_period=10, use_ha=True)
    
    # Test Case 3: Without HA
    print("\n\n[TEST 3] Regular candles (no HA)")
    df3 = create_sample_data(100, seed=456)
    result3 = compare_implementations(df3, sensitivity=1.0, atr_period=10, use_ha=False)
    
    # Test Case 4: Different ATR period
    print("\n\n[TEST 4] Different ATR period")
    df4 = create_sample_data(100, seed=789)
    result4 = compare_implementations(df4, sensitivity=1.0, atr_period=14, use_ha=True)
    
    # Summary
    print("\n\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    
    all_passed = all([result1, result2, result3, result4])
    
    if all_passed:
        print("[PASS] ALL TESTS PASSED!")
        print("\nThe new UTBotIndicator plugin produces IDENTICAL results")
        print("to the original calculate_utbot() implementation.")
        print("\n[PASS] SAFE TO USE IN PRODUCTION")
    else:
        print("[FAIL] SOME TESTS FAILED!")
        print("\nPlease review the differences above.")
        sys.exit(1)

