from openalgo import api
import pandas as pd

try:
    # Initialize client (User's API key from config)
    api_key = "a1e43574fd5008b00b81024f71096fdc966bed01a5b64a13af36fb2b7ea41faf"
    host = "http://127.0.0.1:5000"
    
    client = api(api_key=api_key, host=host)
    
    print("Fetching NSE_INDEX Instruments...")
    # Fetch NSE_INDEX
    data = client.instruments(exchange="NSE_INDEX")
    
    if data:
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict) and 'data' in data:
            df = pd.DataFrame(data['data'])
        else:
            print("Unknown data format")
            df = pd.DataFrame()
            
        print(f"Fetched {len(df)} NFO instruments")
        if not df.empty:
            print("Columns:", df.columns)
            print("Sample Lot Sizes:")
            print(df[['symbol', 'lotsize']].head())
            
            # Check for NIFTY lot size
            nifty_opts = df[df['symbol'].str.contains('NIFTY', na=False) & df['symbol'].str.contains('CE', na=False)]
            if not nifty_opts.empty:
                print("\nNIFTY Option Sample:")
                print(nifty_opts[['symbol', 'lotsize']].head())
            else:
                print("No NIFTY Options found")
    else:
        print("No data received for NFO")

except Exception as e:
    print(f"Error: {e}")
