import requests
import json
import os
from config import config
import re

host = config.get("api.host")
api_key = config.get("api.api_key")

def run():
    url = f"{host}/api/v1/search"
    payload = {"apikey": api_key, "query": "NIFTY"}
    r = requests.post(url, json=payload)
    if r.status_code == 200:
        data = r.json().get('data', [])
        expiries = set()
        # Look for NIFTY + 2 digits + 3 chars + 2 digits (e.g. NIFTY06JAN26)
        pattern = re.compile(r'NIFTY(\d{2}[A-Z]{3}\d{2})')
        for item in data:
            sym = item.get('symbol', '')
            match = pattern.search(sym)
            if match:
                expiries.add(match.group(1))
        
        sorted_exp = sorted(list(expiries))
        print("FOUND_EXPIRIES:", ",".join(sorted_exp))
    else:
        print("ERROR:", r.status_code)

if __name__ == "__main__":
    run()
