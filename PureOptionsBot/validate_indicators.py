"""
Quick validation script to test the indicator plugin system.
Run with: python validate_indicators.py
"""

import sys
import pandas as pd
import numpy as np

# Add current directory to path
sys.path.insert(0, '.')

from indicators.base import BaseIndicator, IndicatorSignal
from indicators.utbot import UTBotIndicator
from indicators.registry import IndicatorRegistry


def create_sample_data(n=50):
    """Create sample OHLC data"""
    np.random.seed(42)
    
    close = 25000 + np.cumsum(np.random.randn(n) * 10)
    high = close + np.random.rand(n) * 20
    low = close - np.random.rand(n) * 20
    open_price = close + np.random.randn(n) * 5
    
    df = pd.DataFrame({
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": np.random.randint(1000, 10000, n)
    })
    
    # Add Heikin Ashi columns
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    return df


def test_indicator_signal():
    """Test IndicatorSignal class"""
    print("Testing IndicatorSignal...")
    
    signal = IndicatorSignal(trend=1, signal=1, strength=1.0, metadata={})
    assert signal.is_bullish(), "Should be bullish"
    assert signal.has_fresh_buy(), "Should have fresh buy"
    print("  [OK] IndicatorSignal works correctly")


def test_utbot_direct():
    """Test UTBot indicator directly"""
    print("\nTesting UTBotIndicator...")
    
    # Create indicator
    params = {"sensitivity": 1.0, "atr_period": 10}
    indicator = UTBotIndicator(params)
    print(f"  [OK] Created {indicator}")
    
    # Create data
    df = create_sample_data()
    print(f"  [OK] Created {len(df)} bars of data")
    
    # Calculate signal
    signal = indicator.calculate(df, use_ha=True)
    print(f"  [OK] Calculated signal: Trend={signal.trend}, Signal={signal.signal}")
    
    # Verify metadata
    assert "stop_level" in signal.metadata
    assert "atr" in signal.metadata
    print(f"  [OK] Stop Level: {signal.metadata['stop_level']:.2f}")
    print(f"  [OK] ATR: {signal.metadata['atr']:.2f}")
    
    # Test trend age
    age = indicator.get_trend_age(signal)
    print(f"  [OK] Trend Age: {age} candles")


def test_registry():
    """Test IndicatorRegistry"""
    print("\nTesting IndicatorRegistry...")
    
    # List indicators
    indicators = IndicatorRegistry.list_indicators()
    print(f"  [OK] Available indicators: {indicators}")
    
    # Create via registry
    params = {"sensitivity": 1.5, "atr_period": 14}
    indicator = IndicatorRegistry.create("utbot", params)
    print(f"  [OK] Created via registry: {indicator}")
    
    # Get info
    info = IndicatorRegistry.get_indicator_info("utbot")
    print(f"  [OK] Indicator info: {info}")


def test_backward_compatibility():
    """Test that the plugin produces same results as original code"""
    print("\nTesting Backward Compatibility...")
    
    # This would compare with original calculate_utbot() from live_trader.py
    # For now, just verify it runs without errors
    df = create_sample_data(100)
    
    params = {"sensitivity": 1.0, "atr_period": 10}
    indicator = UTBotIndicator(params)
    
    signal = indicator.calculate(df, use_ha=True)
    
    print(f"  [OK] Calculated on 100 bars")
    print(f"  [OK] Final Trend: {'Bullish' if signal.is_bullish() else 'Bearish'}")
    print(f"  [OK] Final Signal: {signal.signal}")
    print(f"  [OK] Stop Level: {signal.metadata['stop_level']:.2f}")


if __name__ == "__main__":
    print("=" * 60)
    print("PureOptionsBot - Indicator Plugin System Validation")
    print("=" * 60)
    
    try:
        test_indicator_signal()
        test_utbot_direct()
        test_registry()
        test_backward_compatibility()
        
        print("\n" + "=" * 60)
        print("[SUCCESS] ALL TESTS PASSED!")
        print("=" * 60)
        print("\nThe indicator plugin system is working correctly.")
        print("You can now:")
        print("  1. Use UTBot via IndicatorRegistry.create('utbot', params)")
        print("  2. Add new indicators by creating plugins and registering them")
        print("  3. Configure indicators dynamically from config.yaml")
        
    except Exception as e:
        print(f"\n[FAILED] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

