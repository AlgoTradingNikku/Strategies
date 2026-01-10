import sys
import os

# Add the strategy directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "PureOptionsBot")))

from PureOptionsStrategy import resolve_symbol_from_query

def test():
    print("--- Testing Direct Resolution ---")
    
    # 1. Standard format with spaces
    res1 = resolve_symbol_from_query("NIFTY 13Jan26 26200 PE")
    print(f"Result 1: {res1}")
    assert res1 == "NIFTY13JAN2626200PE"
    
    # 2. Raw ticker
    res2 = resolve_symbol_from_query("NIFTY13JAN2626200PE")
    print(f"Result 2: {res2}")
    assert res2 == "NIFTY13JAN2626200PE"
    
    # 3. Different expiry/strike
    res3 = resolve_symbol_from_query("NIFTY 15Jan26 27000 CE")
    print(f"Result 3: {res3}")
    assert res3 == "NIFTY15JAN2627000CE"

    print("\n[SUCCESS] Direct resolution working as expected.")

if __name__ == "__main__":
    test()
