import requests
import json
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def test_raw_quotes():
    print(f"Connecting to: {host}/api/v1/quotes")
    
    symbols_to_test = [
        ("NIFTY", "NSE_INDEX"),
        ("SBIN", "NSE"),
        ("Nifty 50", "NSE_INDEX")
    ]
    
    for sym, exch in symbols_to_test:
        print(f"\n--- Testing {sym} on {exch} ---")
        payload = {
            "apikey": api_key, # Use 'apikey' as per REST docs
            "symbol": sym,
            "exchange": exch
        }
        try:
            r = requests.post(f"{host}/api/v1/quotes", json=payload)
            print(f"Status: {r.status_code}")
            print(f"Response: {r.text}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_raw_quotes()
