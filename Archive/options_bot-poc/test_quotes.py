import requests
import json
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def run():
    # Let's try to get quotes for a NIFTY index first
    payload = {
        "apikey": api_key,
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX"
    }
    url = f"{host}/api/v1/quotes"
    r = requests.post(url, json=payload)
    if r.status_code == 200:
        print("QUOTE DATA:", json.dumps(r.json().get('data', {}), indent=2))
    else:
        print("ERROR")

if __name__ == "__main__":
    run()
