import requests
import json
from openalgo import api as openalgo_api
from config import config

host = config.get("api.host")
api_key = config.get("api.api_key")

def run_discovery():
    print(f"Connecting to Host: {host}")
    search_url = f"{host}/api/v1/search"
    
    # 1. Broad Search via POST
    search_queries = ["Nifty", "NIFTY", "Index"]
    discovered_symbols = []
    
    for q in search_queries:
        try:
            print(f"Searching for: {q} via POST {search_url}...")
            payload = {
                "apikey": api_key,
                "query": q
            }
            r = requests.post(search_url, json=payload)
            if r.status_code == 200:
                try:
                    resp_json = r.json()
                    if resp_json.get('status') == 'success':
                        results = resp_json.get('data', [])
                        print(f"  Found {len(results)} matches.")
                        for item in results[:20]:
                            sym = item.get('symbol')
                            exch = item.get('exchange')
                            name = item.get('name')
                            discovered_symbols.append((sym, exch))
                            print(f"    - Symbol: '{sym}', Exchange: '{exch}', Name: '{name}'")
                    else:
                        print(f"  API returned error: {resp_json.get('message')}")
                except Exception as je:
                    print(f"  Failed to parse JSON for {q}: {je}")
                    print(f"  Raw response: {r.text[:500]}")
            else:
                print(f"  Search {q} failed with status {r.status_code}")
                if r.status_code == 404:
                    print("  404 error - checking root dashboard search...")
                    # Try /search as fallback if api/v1/search isn't there
                    r2 = requests.get(f"{host}/search?symbol={q}")
                    print(f"  GET /search status: {r2.status_code}")
        except Exception as e:
            print(f"  Error searching {q}: {e}")

    # 2. Test LTP for interesting ones
    print("\n--- Testing LTP for Discovered Symbols ---")
    client = openalgo_api(api_key=api_key, host=host)
    
    test_list = list(set(discovered_symbols))
    # Also add standard ones just in case
    test_list.append(("NIFTY", "NSE_INDEX"))
    test_list.append(("Nifty 50", "NSE_INDEX"))
    test_list.append(("Nifty 50", "NSE"))
    
    for sym, exch in test_list:
        try:
            # Handle potential 'EXCH:SYMBOL' format if search returns it
            test_sym = sym
            test_exch = exch
            if ":" in str(sym):
                parts = sym.split(":")
                test_exch = parts[0]
                test_sym = parts[1]
                
            print(f"LTP Test -> sym='{test_sym}', exch='{test_exch}': ", end="")
            resp = client.get_ltp(symbol=test_sym, exchange=test_exch)
            print(resp)
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    run_discovery()
