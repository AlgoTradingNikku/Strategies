import requests
import json
from openalgo import api as openalgo_api
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def run_index_discovery():
    print(f"Connecting to Host: {host}")
    
    # 1. Search for "INDEX" to find all indices
    print("\n--- Searching for 'INDEX' ---")
    try:
        url = f"{host}/api/v1/search"
        payload = {"apikey": api_key, "query": "INDEX"}
        r = requests.post(url, json=payload)
        if r.status_code == 200:
            data = r.json().get('data', [])
            print(f"Found {len(data)} results for 'INDEX'")
            indices = [item for item in data if item.get('instrumenttype') == 'INDEX']
            print(f"Found {len(indices)} results with instrumenttype='INDEX'")
            for item in indices:
                print(f"  Index: {json.dumps(item)}")
        else:
            print(f"Search failed: {r.status_code}")
    except Exception as e:
        print(f"Search error: {e}")

    # 2. Search for "Nifty" and filter for INDEX
    print("\n--- Searching for 'Nifty' and filtering for INDEX ---")
    try:
        url = f"{host}/api/v1/search"
        payload = {"apikey": api_key, "query": "Nifty"}
        r = requests.post(url, json=payload)
        if r.status_code == 200:
            data = r.json().get('data', [])
            indices = [item for item in data if item.get('instrumenttype') == 'INDEX']
            for item in indices:
                print(f"  Found Nifty Index: {json.dumps(item)}")
    except Exception as e:
        print(f"Nifty search error: {e}")

if __name__ == "__main__":
    run_index_discovery()
