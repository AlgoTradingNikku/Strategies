#!/usr/bin/env python3
"""
Quick verification script for Signal Lookback Implementation
"""
import yaml
from pathlib import Path

config_path = Path(__file__).parent / "config.yml"

print("=" * 60)
print("Signal Lookback Implementation Verification")
print("=" * 60)

try:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    
    print("\n✅ Config file loaded successfully!\n")
    
    # Check new parameters
    opt_cfg = cfg.get("options", {})
    strat_cfg = cfg.get("strategy", {})
    
    lookback = opt_cfg.get("signal_lookback_candles", "NOT SET")
    closed_bar = strat_cfg.get("signal_on_closed_bar", "NOT SET")
    
    print(f"📊 Configuration Values:")
    print(f"  • signal_lookback_candles: {lookback}")
    print(f"  • signal_on_closed_bar: {closed_bar}")
    
    # Validation
    print(f"\n🔍 Validation:")
    if lookback == 2:
        print(f"  ✅ Lookback set to optimal value (2)")
    elif lookback == "NOT SET":
        print(f"  ❌ ERROR: signal_lookback_candles not found in config!")
    else:
        print(f"  ⚠️  Lookback set to {lookback} (2 is recommended)")
    
    if closed_bar == True:
        print(f"  ✅ Closed-bar mode enabled (TradingView parity)")
    elif closed_bar == "NOT SET":
        print(f"  ❌ ERROR: signal_on_closed_bar not found in config!")
    else:
        print(f"  ⚠️  Running-bar mode enabled (signals can flip mid-bar)")
    
    # Try importing signals module
    print(f"\n🔧 Module Imports:")
    try:
        import signals
        print(f"  ✅ signals.py imported successfully")
        
        # Check for new functions
        if hasattr(signals, "_is_last_candle_incomplete"):
            print(f"  ✅ _is_last_candle_incomplete() found")
        else:
            print(f"  ❌ ERROR: _is_last_candle_incomplete() not found!")
        
        if hasattr(signals, "_parse_timeframe_seconds"):
            print(f"  ✅ _parse_timeframe_seconds() found")
        else:
            print(f"  ❌ ERROR: _parse_timeframe_seconds() not found!")
            
    except Exception as e:
        print(f"  ❌ ERROR importing signals.py: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Verification Complete!")
    print("=" * 60)
    print("\nNext Steps:")
    print("  1. Review the implementation document:")
    print("     Bot-NSE-Options/SIGNAL_LOOKBACK_IMPLEMENTATION.md")
    print("  2. Run scanner to verify startup logs")
    print("  3. Monitor for 1-2 days in paper trading mode")
    print("=" * 60)
    
except FileNotFoundError:
    print(f"\n❌ ERROR: config.yml not found at {config_path}")
except Exception as e:
    print(f"\n❌ ERROR: {e}")
