import yaml
import pandas as pd
from datetime import datetime
import os
import sys

# Add project root and openalgo path
project_root = os.path.abspath(".")
sys.path.insert(0, project_root)
sys.path.insert(0, r"c:\Rahul\04_Rahul\01_Trade\Repos\openalgo")

from indicators.utbot import UTBotIndicator

def check_signal(symbol):
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)
    
    from openalgo import api
    
    api_key = os.getenv("OPENALGO_API_KEY") or config.get("api_key")
    api_host = config.get("api_host", "http://127.0.0.1:5000")
    client = api(api_key=api_key, host=api_host)
    
    timeframe = config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
    use_ha = config.get("option", {}).get("ltf", {}).get("use_ha", False)
    sensitivity = config.get("option", {}).get("ltf", {}).get("sensitivity", 1.0)
    atr_period = config.get("option", {}).get("ltf", {}).get("atr", 10)

    print(f"Checking {symbol} | TF: {timeframe} | HA: {use_ha} | Sens: {sensitivity} | ATR: {atr_period}")
    
    end_date = datetime.now()
    start_date = end_date - pd.Timedelta(days=1)
    
    raw = client.history(
        symbol=symbol, exchange="NFO", interval=timeframe,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )
    
    df = pd.DataFrame()
    if isinstance(raw, pd.DataFrame):
        df = raw
    elif isinstance(raw, dict) and "data" in raw:
        df = pd.DataFrame(raw["data"])
    else:
        print(f"Unknown data format: {type(raw)}")
        return

    if df.empty:
        print("No data found.")
        return
        
    print(f"Raw Columns: {df.columns.tolist()}")
    
    # Standardize columns
    col_map = {
        "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
        "timestamp": "timestamp", "time": "timestamp"
    }
    df.rename(columns=col_map, inplace=True)
    
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
    
    # Ensure numeric
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
             print(f"Error: Required column {col} missing after rename. Available: {df.columns.tolist()}")
             return
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(inplace=True)
    
    # Add HA
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)

    utbot = UTBotIndicator({"sensitivity": sensitivity, "atr_period": atr_period})
    
    print("\n--- Signals from 09:15 ---")
    warmup = utbot.warmup_period
    for i in range(len(df)):
        ts = df.index[i]
        ts_str = ts.strftime("%H:%M")
        if ts_str >= "09:15" and ts_str <= "11:00":
            if i < warmup:
                print(f"[{ts}] Warmup...")
                continue
            sub_df = df.iloc[:i+1]
            try:
                res = utbot.calculate(sub_df, use_ha=use_ha)
                ha_o = df['HA_Open'].iloc[i]
                ha_c = df['HA_Close'].iloc[i]
                color = "GREEN" if ha_c > ha_o else "RED"
                print(f"[{ts}] Price: {df['Close'].iloc[i]:.2f}, Signal: {res.signal}, Trend: {res.trend}, HA_{color} (O:{ha_o:.1f}, C:{ha_c:.1f})")
            except Exception as e:
                print(f"[{ts}] Error: {e}")

if __name__ == "__main__":
    check_signal("NIFTY27JAN2625100CE")
