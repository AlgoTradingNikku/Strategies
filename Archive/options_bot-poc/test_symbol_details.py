import requests
import json
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def run():
    url = f"{host}/api/v1/search"
    payload = {"apikey": api_key, "query": "NIFTY"}
    r = requests.post(url, json=payload)
    if r.status_code == 200:
        data = r.json().get('data', [])
        for item in data[:5]:
            print(f"Symbol: {item.get('symbol')} | LotSize: {item.get('lotsize')} | keys: {list(item.keys())}")
    else:
        print("ERROR")

if __name__ == "__main__":
    run()
