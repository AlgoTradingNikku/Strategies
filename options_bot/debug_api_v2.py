from openalgo import api as openalgo_api
from config import config
import time

print("Debugging OpenAlgo API Data Fetch...")
try:
    client = openalgo_api(
        api_key=config.get("api.api_key"),
        host=config.get("api.host")
    )
    
    symbols_to_test = [
        ("NIFTY", "NSE"),
        ("NIFTY 50", "NSE"),
        ("NIFTY 50", "NSE_INDEX"),
        ("SBIN", "NSE"),
        ("RELIANCE", "NSE")
    ]
    
    print("\n--- Raw HTTP Search Test ---")
    import requests
    host = config.get("api.host")
    try:
        search_url = f"{host}/search?symbol=NIFTY"
        print(f"Searching: {search_url}")
        r = requests.get(search_url)
        print(f"Status: {r.status_code}")
        try:
            results = r.json()
            print(f"Total Results: {len(results)}")
            print("--- First 10 Results ---")
            for i, item in enumerate(results[:10]):
                print(f"Item {i}: {item}")
        except Exception as json_err:
             print(f"JSON Parse Error: {json_err}. Text partial: {r.text[:200]}")
        
    except Exception as e:
        print(f"Search Error: {e}")

    for sym, exch in symbols_to_test:
        try:
            print(f"Testing get_ltp for {sym}...")
            resp = client.get_ltp(symbol=sym, exchange=exch)
            print(f"LTP Response: {resp}")
            
            print(f"Testing get_quotes for {sym}...")
            # Some versions might use 'get_quotes' or 'quote'
            try:
                resp_q = client.get_quotes(symbol=sym, exchange=exch)
                print(f"Quotes Response: {resp_q}")
            except:
                print("get_quotes not found")
                
        except Exception as e:
            print(f"Error: {e}")
            
except Exception as e:
    print(f"Init Error: {e}")
