from openalgo import api
import json

try:
    api_key = "a1e43574fd5008b00b81024f71096fdc966bed01a5b64a13af36fb2b7ea41faf"
    host = "http://127.0.0.1:5000"
    
    client = api(api_key=api_key, host=host)
    
    print("Fetching Quote for NSE_INDEX:NIFTY...")
    quote = client.quotes(symbol="NIFTY", exchange="NSE_INDEX")
    if quote is not None:
        print(f"Quote: {json.dumps(quote if not hasattr(quote, 'to_dict') else quote.to_dict('records'), indent=2)}")
    else:
        print("Quote is None")

    
    print("\nFetching Position Book...")
    pb = client.positionbook()
    print(f"Position Book: {json.dumps(pb, indent=2)}")

except Exception as e:
    print(f"Error: {e}")
