import pickle
import os
import sys

CACHE_FILE = "instruments_cache.pkl"
SYMBOL = "NIFTY27JAN2625400PE"

def inspect():
    if not os.path.exists(CACHE_FILE):
        print(f"Error: {CACHE_FILE} not found.")
        return

    print(f"Loading {CACHE_FILE}...")
    try:
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
            
        print(f"Cache Type: {type(data)}")
        print(f"Total Items: {len(data)}")
        
        # Check if it's a list or dict
        # The provider logic: self._master_cache = {item['symbol']: item for item in master_list}
        # So it should be a DICT.
        
        if isinstance(data, list):
             print("Cache is a LIST. Converting to search...")
             # Search in list
             found = [x for x in data if x.get('symbol') == SYMBOL]
             if found:
                 print(f"\nFOUND {SYMBOL}:")
                 print(found[0])
             else:
                 print(f"\n{SYMBOL} NOT FOUND in list.")
                 
        elif isinstance(data, dict):
             print("Cache is a DICT.")
             item = data.get(SYMBOL)
             if item:
                 print(f"\nFOUND {SYMBOL}:")
                 print(item)
                 print(f"\nLot Size field: {item.get('lotsize')}")
                 print(f"Token field: {item.get('token')}")
                 print(f"Open Interest: {item.get('oi')}")
             else:
                 print(f"\n{SYMBOL} NOT FOUND in dict.")
        
    except Exception as e:
        print(f"Error reading cache: {e}")

if __name__ == "__main__":
    inspect()
