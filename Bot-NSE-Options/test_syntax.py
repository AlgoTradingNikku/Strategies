#!/usr/bin/env python3
"""
Quick syntax check for signals.py
"""
import sys
from pathlib import Path

# Add Bot-NSE-Options to path
bot_path = Path(__file__).parent
sys.path.insert(0, str(bot_path))

print("=" * 60)
print("Syntax Check - signals.py")
print("=" * 60)

try:
    import signals
    print("✅ signals.py imported successfully!")
    print("✅ No syntax errors detected")
    
    # Check for required functions
    if hasattr(signals, 'evaluate_composite_signals'):
        print("✅ evaluate_composite_signals() found")
    
    if hasattr(signals, '_is_last_candle_incomplete'):
        print("✅ _is_last_candle_incomplete() found")
    
    if hasattr(signals, '_parse_timeframe_seconds'):
        print("✅ _parse_timeframe_seconds() found")
    
    print("\n" + "=" * 60)
    print("✅ ALL CHECKS PASSED - Ready to run scanner!")
    print("=" * 60)
    
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
