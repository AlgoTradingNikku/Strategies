import requests
import json
from openalgo import api as openalgo_api
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def run_exhaustive_test():
    print(f"Connecting to Host: {host}")
    client = openalgo_api(api_key=api_key, host=host)
    
    # 1. Search for "Nifty 50" explicitly
    print("\n--- Searching for 'Nifty 50' ---")
    try:
        url = f"{host}/api/v1/search"
        payload = {"apikey": api_key, "query": "Nifty 50"}
        r = requests.post(url, json=payload)
        if r.status_code == 200:
            data = r.json().get('data', [])
            print(f"Found {len(data)} results for 'Nifty 50'")
            for item in data[:10]:
                print(f"  Result: {json.dumps(item)}")
    except Exception as e:
        print(f"Search error: {e}")

    # 2. Exhaustive LTP Test
    symbols = ["NIFTY", "Nifty 50", "NIFTY 50", "NIFTY INDEX", "Nifty Index"]
    exchanges = ["NSE", "NSE_INDEX", "NFO"]
    
    print("\n--- Exhaustive LTP Test ---")
    for s in symbols:
        for e in exchanges:
            try:
                print(f"Testing {s} on {e} -> ", end="")
                resp = client.get_ltp(symbol=s, exchange=e)
                print(resp)
            except Exception as ex:
                print(f"Error: {ex}")

if __name__ == "__main__":
    run_exhaustive_test()
