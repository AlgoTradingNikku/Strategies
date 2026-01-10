from openalgo import api
import json

CONFIG = {
    "api_key": "a2edab0147e5058617b63b677c82c5c44533d356d8b8f33734127d6c5f029a55",
    "api_host": "http://127.0.0.1:5000",
}

client = api(api_key=CONFIG["api_key"], host=CONFIG["api_host"])

print("Searching for Nifty in NSE_INDEX...")
try:
    res = client.search(query="NIFTY", exchange="NSE_INDEX")
    print(json.dumps(res, indent=2))
except Exception as e: print(e)

print("\nSearching for NIFTY in NSE...")
try:
    res = client.search(query="NIFTY", exchange="NSE")
    print(json.dumps(res, indent=2))
except Exception as e: print(e)
