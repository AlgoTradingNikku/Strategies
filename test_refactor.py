import sys
import os

# Add the strategy directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "PureOptionsBot")))

from PureOptionsStrategy import CONFIG, get_contract_type, resolve_symbol_from_query

def test_refactor():
    print("--- Testing Refactored Logic ---")
    
    # 1. Check CONFIG
    print(f"Trade Symbol: {CONFIG.get('trade_symbol')}")
    assert "trade_symbol" in CONFIG
    assert "option_query" not in CONFIG
    assert "contract_type" not in CONFIG
    assert "auto_strike_selection" not in CONFIG
    
    # 2. Check get_contract_type
    c1 = get_contract_type("NIFTY13JAN2626200PE")
    print(f"NIFTY13JAN2626200PE -> {c1}")
    assert c1 == "Put"
    
    c2 = get_contract_type("NIFTY13JAN2626200CE")
    print(f"NIFTY13JAN2626200CE -> {c2}")
    assert c2 == "Call"
    
    # 3. Check Symbol Resolution
    res = resolve_symbol_from_query(CONFIG["trade_symbol"])
    print(f"Resolved: {res}")
    assert res == CONFIG["trade_symbol"]

    print("\n[SUCCESS] Refactoring verification passed.")

if __name__ == "__main__":
    test_refactor()
