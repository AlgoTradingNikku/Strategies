"""
Quick validation test for RS Filter + Dynamic Index Symbol changes
Run: python test_rs_filter.py
"""

import sys
from pathlib import Path

# Add Bot-Stocks to path
sys.path.insert(0, str(Path(__file__).parent))

from scanner import get_index_symbol

def test_index_symbol_mapping():
    """Test that index symbols map correctly for each data source."""
    print("=" * 70)
    print("Testing Index Symbol Mapping")
    print("=" * 70)
    
    test_cases = [
        ("NIFTY50", "yfinance", "^NSEI"),
        ("NIFTY50", "openalgo", "NIFTY 50"),
        ("NIFTY50", "tvdatafeed", "NIFTY"),
        ("BANKNIFTY", "yfinance", "^NSEBANK"),
        ("BANKNIFTY", "openalgo", "NIFTY BANK"),
        ("FINNIFTY", "openalgo", "FINNIFTY"),
        ("NIFTYIT", "yfinance", "^CNXIT"),
        ("NIFTYIT", "openalgo", "NIFTY IT"),
    ]
    
    passed = 0
    failed = 0
    
    for index_name, data_source, expected in test_cases:
        result = get_index_symbol(index_name, data_source)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"{status} | {index_name:12} + {data_source:12} → {result:15} (expected: {expected})")
        
        if result == expected:
            passed += 1
        else:
            failed += 1
    
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)
    
    return failed == 0

def test_config_loading():
    """Test that config.yml loads correctly with new RS settings."""
    print("\n" + "=" * 70)
    print("Testing Config Loading")
    print("=" * 70)
    
    try:
        from scanner import load_config
        config = load_config()
        
        filters = config.get("filters", {})
        
        # Check new RS settings exist
        checks = [
            ("rs_enabled", filters.get("rs_enabled") is not None),
            ("rs_index", filters.get("rs_index") is not None),
            ("rs_period", filters.get("rs_period") is not None),
            ("rs_buy_threshold", filters.get("rs_buy_threshold") is not None),
            ("rs_sell_threshold", filters.get("rs_sell_threshold") is not None),
        ]
        
        passed = 0
        for key, exists in checks:
            status = "✅ PASS" if exists else "❌ FAIL"
            value = filters.get(key, "MISSING")
            print(f"{status} | {key:20} → {value}")
            if exists:
                passed += 1
        
        print("=" * 70)
        print(f"Results: {passed}/{len(checks)} config keys present")
        print("=" * 70)
        
        return passed == len(checks)
        
    except Exception as e:
        print(f"❌ Config loading failed: {e}")
        print("=" * 70)
        return False

if __name__ == "__main__":
    print("\n🚀 RS Filter + Dynamic Index Symbol Validation")
    print("=" * 70)
    
    test1 = test_index_symbol_mapping()
    test2 = test_config_loading()
    
    print("\n" + "=" * 70)
    if test1 and test2:
        print("✅ ALL TESTS PASSED — Implementation successful!")
    else:
        print("❌ SOME TESTS FAILED — Review errors above")
    print("=" * 70)
