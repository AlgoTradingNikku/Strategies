
import asyncio
import aiohttp
import pandas as pd
import yaml

async def test_historical_data():
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
            
        api_host = config.get("api_host", "http://127.0.0.1:5000")
        api_key = config.get("api_key", "test_key")
        
        print(f"Connecting to OpenAlgo at {api_host}...")
        
        async with aiohttp.ClientSession() as session:
            # 1. Fetch NIFTY Index Data (15m)
            print("\nFetching NIFTY 15m Data...")
            # Calculate dates
            import datetime
            end_dt = datetime.datetime.now()
            start_dt = end_dt - datetime.timedelta(days=5)
            
            url = f"{api_host}/api/v1/history"
            payload = {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "interval": "15m",
                "start_date": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "apikey": api_key
            }
            
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success" and data.get("data"):
                        df = pd.DataFrame(data["data"])
                        if not df.empty:
                            last_candle = df.iloc[-1]
                            print("SUCCESS: Received Data")
                            print(f"Last Candle: Open={last_candle['Open']}, Close={last_candle['Close']}")
                        else:
                            print("SUCCESS: Response valid but no data for date range")
                    else:
                        print(f"FAILED: No data in response. {data}")
                else:
                    text = await resp.text()
                    print(f"FAILED: HTTP {resp.status} - {text}")
                    
            # 2. Verify Config Loading Logic
            use_ha_index = config.get("index_use_ha", True)
            use_ha_option = config.get("option", {}).get("ltf", {}).get("use_ha", True)
            
            print("\nConfiguration Verification:")
            print(f"Index Use HA: {use_ha_index} (Expected: False)")
            print(f"Option Use HA: {use_ha_option} (Expected: False)")
            
            if use_ha_index is False and use_ha_option is False:
                print("[PASS] CONFIG IS CORRECT: Both set to OHLC (False)")
            else:
                print("[FAIL] CONFIG MISMATCH: One or both are still True (HA)")

    except Exception as e:
        print(f"TEST ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_historical_data())
