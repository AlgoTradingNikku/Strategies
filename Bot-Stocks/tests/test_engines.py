"""
Test script for Momentum & Mean Reversion engines.
Validates both engines can process sample data without errors.
"""

import pandas as pd
import numpy as np
from signals import compute_momentum_signals, compute_mean_reversion_signals, ENGINE_REGISTRY

# Create sample OHLCV data
np.random.seed(42)
n = 100
dates = pd.date_range('2024-01-01', periods=n, freq='5min')

# Generate realistic price data with trend
base_price = 100
trend = np.linspace(0, 10, n)
noise = np.random.randn(n) * 2
close = base_price + trend + noise

df = pd.DataFrame({
    'datetime': dates,
    'open': close + np.random.randn(n) * 0.5,
    'high': close + np.abs(np.random.randn(n)) * 1.5,
    'low': close - np.abs(np.random.randn(n)) * 1.5,
    'close': close,
    'volume': np.random.randint(10000, 100000, n)
})

df['high'] = df[['open', 'close', 'high']].max(axis=1)
df['low'] = df[['open', 'close', 'low']].min(axis=1)

print("=" * 70)
print("MOMENTUM & MEAN REVERSION ENGINE TEST")
print("=" * 70)

# Test 1: Momentum Engine
print("\n[Test 1] Momentum Engine")
print("-" * 70)
momentum_cfg = {
    "enabled": True,
    "min_momentum_score": 70,
    "rsi_enabled": True,
    "rsi_period": 14,
    "rsi_buy_zone": [40, 70],
    "rsi_sell_zone": [30, 60],
    "rsi_weight": 20,
    "volume_enabled": True,
    "volume_sma_period": 20,
    "volume_surge_min": 1.5,
    "volume_weight": 20,
    "adx_enabled": True,
    "adx_period": 14,
    "adx_min_threshold": 20.0,
    "adx_weight": 15,
    "ema_enabled": True,
    "ema_period": 50,
    "ema_weight": 15,
    "bb_enabled": True,
    "bb_period": 20,
    "bb_std_dev": 2.0,
    "bb_expansion_min_pct": 150.0,
    "bb_weight": 15,
    "roc_enabled": True,
    "roc_period": 10,
    "roc_min_threshold": 3.0,
    "roc_weight": 15
}

try:
    df_momentum = compute_momentum_signals(df.copy(), momentum_cfg)
    
    # Check required columns exist
    required_cols = ["momentum_buy", "momentum_sell", "momentum_score_buy", "momentum_score_sell"]
    missing = [col for col in required_cols if col not in df_momentum.columns]
    
    if missing:
        print(f"[FAIL] FAILED: Missing columns: {missing}")
    else:
        buy_signals = df_momentum["momentum_buy"].sum()
        sell_signals = df_momentum["momentum_sell"].sum()
        avg_buy_score = df_momentum["momentum_score_buy"].mean()
        avg_sell_score = df_momentum["momentum_score_sell"].mean()
        
        print(f"[OK] PASSED")
        print(f"   BUY signals:  {buy_signals}")
        print(f"   SELL signals: {sell_signals}")
        print(f"   Avg BUY score:  {avg_buy_score:.1f}")
        print(f"   Avg SELL score: {avg_sell_score:.1f}")
        print(f"   Added columns: {[col for col in df_momentum.columns if col.startswith('momentum_')]}")
        
except Exception as e:
    print(f"[FAIL] FAILED with error: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Mean Reversion Engine
print("\n[Test 2] Mean Reversion Engine")
print("-" * 70)
mr_cfg = {
    "enabled": True,
    "min_mr_score": 70,
    "bb_enabled": True,
    "bb_period": 20,
    "bb_std_dev": 2.0,
    "bb_touch_threshold": 0.02,
    "bb_weight": 25,
    "rsi_div_enabled": True,
    "rsi_div_period": 14,
    "rsi_div_lookback": 15,
    "rsi_div_weight": 30,
    "rsi_extreme_enabled": True,
    "rsi_extreme_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "rsi_extreme_weight": 20,
    "stochastic_enabled": True,
    "stoch_k_period": 14,
    "stoch_d_period": 3,
    "stoch_smooth_k": 3,
    "stoch_oversold": 20,
    "stoch_overbought": 80,
    "stoch_weight": 15,
    "zscore_enabled": True,
    "zscore_period": 20,
    "zscore_buy_threshold": -2.0,
    "zscore_sell_threshold": 2.0,
    "zscore_weight": 25,
    "vol_climax_enabled": True,
    "vol_climax_period": 20,
    "vol_climax_threshold": 2.5,
    "vol_climax_weight": 15
}

try:
    df_mr = compute_mean_reversion_signals(df.copy(), mr_cfg)
    
    # Check required columns exist
    required_cols = ["mr_buy", "mr_sell", "mr_score_buy", "mr_score_sell"]
    missing = [col for col in required_cols if col not in df_mr.columns]
    
    if missing:
        print(f"[FAIL] FAILED: Missing columns: {missing}")
    else:
        buy_signals = df_mr["mr_buy"].sum()
        sell_signals = df_mr["mr_sell"].sum()
        avg_buy_score = df_mr["mr_score_buy"].mean()
        avg_sell_score = df_mr["mr_score_sell"].mean()
        
        print(f"[OK] PASSED")
        print(f"   BUY signals:  {buy_signals}")
        print(f"   SELL signals: {sell_signals}")
        print(f"   Avg BUY score:  {avg_buy_score:.1f}")
        print(f"   Avg SELL score: {avg_sell_score:.1f}")
        print(f"   Added columns: {[col for col in df_mr.columns if col.startswith('mr_')]}")
        
except Exception as e:
    print(f"[FAIL] FAILED with error: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Engine Registry
print("\n[Test 3] Engine Registry")
print("-" * 70)
print(f"Total engines registered: {len(ENGINE_REGISTRY)}")
for engine in ENGINE_REGISTRY:
    comp_count = len(engine.get("components", []))
    comp_str = f" ({comp_count} components)" if comp_count > 0 else ""
    print(f"  [OK] {engine['label']:20s} [{engine['key']:15s}]{comp_str}")

print("\n" + "=" * 70)
print("ALL TESTS COMPLETED")
print("=" * 70)
