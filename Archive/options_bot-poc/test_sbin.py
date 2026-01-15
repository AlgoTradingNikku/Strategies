import requests
import json
from openalgo import api as openalgo_api
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def test_equity():
    print(f"Connecting to Host: {host}")
    client = openalgo_api(api_key=api_key, host=host)
    
    # 1. Search for SBIN
    print("\n--- Searching for 'SBIN' ---")
    try:
        url = f"{host}/api/v1/search"
        payload = {"apikey": api_key, "query": "SBIN"}
        r = requests.post(url, json=payload)
        if r.status_code == 200:
            data = r.json().get('data', [])
            for item in data[:5]:
                print(f"  Found: {json.dumps(item)}")
    except Exception as e:
        print(f"Search error: {e}")

    # 2. LTP Test for SBIN
    try:
        print(f"\nLTP Test -> SBIN on NSE: ", end="")
        resp = client.get_ltp(symbol="SBIN", exchange="NSE")
        print(resp)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_equity()
