from openalgo import api
import json

# Using the same config as in the bot
api_key = "a1e43574fd5008b00b81024f71096fdc966bed01a5b64a13af36fb2b7ea41faf"
api_host = "http://127.0.0.1:5000"

client = api(api_key=api_key, host=api_host)

if __name__ == "__main__":
    print("--- Getting NIFTY LTP ---")
    try:
        res = client.get_ltp("NIFTY", "NSE_INDEX")
        print(f"NIFTY LTP: {res}")
    except Exception as e:
        print(f"Error getting NIFTY LTP: {e}")

    print("--- Listing NIFTY Symbols for 13JAN26 ---")
    with open("search_results.txt", "w") as f:
        f.write("--- Listing NIFTY Symbols for 13JAN26 ---\n")
        try:
            res = client.search(query="NIFTY13JAN26", exchange="NFO")
            if isinstance(res, dict) and "data" in res and res['data']:
                symbols = [item.get('trading_symbol') or item.get('symbol') for item in res['data']]
                f.write(f"Total found: {len(symbols)}\n")
                for s in sorted(symbols):
                    f.write(f"- {s}\n")
            else:
                f.write(f"No data: {res}\n")
        except Exception as e:
            f.write(f"Error: {e}\n")

    print("Done. Check search_results.txt")
