"""
Quick verification script for RS Filter + OpenAlgo Index Symbol fixes.
Run: python verify_rs_fix.py
"""

import yaml
from pathlib import Path
import sys

# Add parent directory to path to import scanner
sys.path.insert(0, str(Path(__file__).parent))

def verify_config():
    """Verify config.yml has correct RS threshold values."""
    config_path = Path(__file__).parent / "config.yml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    
    filters = cfg.get('filters', {})
    rs_enabled = filters.get('rs_enabled', False)
    rs_buy = filters.get('rs_buy_threshold', 0)
    rs_sell = filters.get('rs_sell_threshold', 0)
    
    print("=" * 70)
    print("RS Filter Configuration Verification")
    print("=" * 70)
    print(f"RS Enabled: {rs_enabled}")
    print(f"RS Buy Threshold: {rs_buy} (should be 1.05)")
    print(f"RS Sell Threshold: {rs_sell} (should be 0.95)")
    print("=" * 70)
    
    # Validation
    issues = []
    if not rs_enabled:
        issues.append("⚠️  RS filter is DISABLED (rs_enabled: false)")
    if rs_buy != 1.05:
        issues.append(f"❌ RS Buy threshold is {rs_buy}, expected 1.05")
    if rs_sell != 0.95:
        issues.append(f"❌ RS Sell threshold is {rs_sell}, expected 0.95")
    
    if issues:
        print("\n⚠️  ISSUES FOUND:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("\n✅ All RS filter settings are correct!")
        print("   • Thresholds set to industry standard ±5%")
        print("   • Filter is enabled and ready")
        return True


def verify_index_symbols():
    """Verify OpenAlgo index symbol mappings are correct."""
    from scanner import get_index_symbol
    
    print("\n" + "=" * 70)
    print("OpenAlgo Index Symbol Mapping Verification")
    print("=" * 70)
    
    test_cases = [
        ("NIFTY50", "openalgo", "NIFTY"),
        ("BANKNIFTY", "openalgo", "BANKNIFTY"),
        ("FINNIFTY", "openalgo", "FINNIFTY"),
        ("NIFTYIT", "openalgo", "NIFTY IT"),
    ]
    
    all_passed = True
    for index_name, data_source, expected in test_cases:
        result = get_index_symbol(index_name, data_source)
        passed = result == expected
        status = "✅" if passed else "❌"
        print(f"{status} {index_name:12} → {result:15} (expected: {expected})")
        if not passed:
            all_passed = False
    
    print("=" * 70)
    
    if all_passed:
        print("\n✅ All OpenAlgo index symbols are correctly mapped!")
        print("   • NIFTY (not 'NIFTY 50')")
        print("   • BANKNIFTY (not 'NIFTY BANK')")
        print("   • Use with exchange='NSE_INDEX' for OpenAlgo")
    else:
        print("\n❌ Some index symbol mappings are incorrect!")
    
    return all_passed


if __name__ == "__main__":
    config_ok = verify_config()
    symbols_ok = verify_index_symbols()
    
    print("\n" + "=" * 70)
    if config_ok and symbols_ok:
        print("✅ ALL VERIFICATIONS PASSED!")
        print("=" * 70)
        print("\nNext steps:")
        print("1. Restart the bot: python app.py")
        print("2. Run a scan and verify no 'NIFTY 50 not found' errors")
        print("3. Check logs for: '[NIFTY] Detected as index symbol, using exchange=NSE_INDEX'")
        exit(0)
    else:
        print("❌ SOME VERIFICATIONS FAILED!")
        print("=" * 70)
        exit(1)
