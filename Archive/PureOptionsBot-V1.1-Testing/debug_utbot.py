
"""
debug_utbot.py

Standalone script to fetch live NIFTY data and print the exact UTBot & Heikin Ashi values
that the bot is seeing. Use this to compare with TradingView.
"""
import pandas as pd
import pandas as pd
# import pandas_ta as ta (Not needed, using manual calc)
import yaml
import os
import sys
from datetime import datetime, timedelta
from openalgo import api

# --- LOAD CONFIG ---
def load_config():
    try:
        with open("config.yaml", 'r') as f:
            return yaml.safe_load(f)
    except:
        return {}

CONFIG = load_config()
if not CONFIG:
    print("Error: config.yaml not found.")
    sys.exit(1)

# --- API CLIENT ---
client = api(api_key=CONFIG["api_key"], host=CONFIG["api_host"])

# --- COPY PASTE CALC FUNCTION FROM live_trader.py ---
def calculate_utbot(df, sensitivity, atr_period, use_ha):
    src = df['HA_Close'] if use_ha else df['Close']
    high = df['HA_High'] if use_ha else df['High']
    low = df['HA_Low'] if use_ha else df['Low']
    close = df['HA_Close'] if use_ha else df['Close']
    
    # ATR (RMA version to match TradingView)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()
    nLoss = sensitivity * atr
    
    trail = [0.0] * len(df)
    pos = [0] * len(df)
    signals = [0] * len(df)
    
    for i in range(atr_period, len(df)):
        s = src.iloc[i]
        prev_s = src.iloc[i-1]
        loss = nLoss.iloc[i]
        prev_trail = trail[i-1]
        
        if s > prev_trail and prev_s > prev_trail:
            curr_trail = max(prev_trail, s - loss)
        elif s < prev_trail and prev_s < prev_trail:
            curr_trail = min(prev_trail, s + loss)
        elif s > prev_trail:
            curr_trail = s - loss
        else:
            curr_trail = s + loss
        
        trail[i] = curr_trail
        
        # Position & Signal Calculation
        prev_p = pos[i-1]
        
        if prev_s < prev_trail and s > prev_trail:
            pos[i] = 1
            signals[i] = 1 
        elif prev_s > prev_trail and s < prev_trail:
            pos[i] = -1
            signals[i] = -1 
        else:
            if prev_p == 0:
                pos[i] = 1 if s > prev_trail else -1
            else:
                pos[i] = prev_p
                
    return pd.Series(pos, index=df.index), pd.Series(trail, index=df.index)

# --- HEIKIN ASHI ---
def calculate_ha(df):
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4.0
    
    ha_open_list = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        prev_ha_open = ha_open_list[i-1]
        prev_ha_close = df['HA_Close'].iloc[i-1]
        ha_open_list.append((prev_ha_open + prev_ha_close) / 2.0)
    df['HA_Open'] = ha_open_list
    
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    return df

# --- MAIN ---
if __name__ == "__main__":
    symbol = "NIFTY" # Index
    exchange = "NSE_INDEX"
    interval = CONFIG["index"]["ltf"]["timeframe"] # e.g. "3m"
    
    print(f"Fetching {symbol} {interval} data...")
    
    end = datetime.now()
    start = end - timedelta(days=5)
    
    raw = client.history(symbol=symbol, exchange=exchange, interval=interval, start_date=start, end_date=end)
    
    if not isinstance(raw, list) and not isinstance(raw, dict) and not isinstance(raw, pd.DataFrame):
         print("Error fetching data")
         sys.exit()

    if isinstance(raw, dict) and 'data' in raw:
        df = pd.DataFrame(raw['data'])
    elif isinstance(raw, dict): # Maybe just a dict that can be converted
        try:
             df = pd.DataFrame(raw)
        except:
             print(f"Failed to parse dict: {raw.keys()}")
             sys.exit()
    elif isinstance(raw, list):
        df = pd.DataFrame(raw)
    else:
        df = raw
    
    if df is None or df.empty:
        print("Empty DataFrame.")
        sys.exit()
        
    # Standardize
    col_map = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
               "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    df.rename(columns=col_map, inplace=True)
    df['timestamp'] = pd.to_datetime(df['time'] if 'time' in df.columns else df.index)
    df.set_index('timestamp', inplace=True)
    for c in ['Open','High','Low','Close']: df[c] = pd.to_numeric(df[c])
    
    # Calc HA
    df = calculate_ha(df)
    
    # Calc UTBot
    sens = CONFIG["index"]["ltf"]["sensitivity"]
    atr_p = CONFIG["index"]["ltf"]["atr"]
    use_ha = CONFIG.get("index_use_ha", True)
    
    pos, trail = calculate_utbot(df, sens, atr_p, use_ha)
    df['Pos'] = pos
    df['Trail'] = trail
    
    print("\n--- LAST 10 CANDLES ---")
    print(df[['Open', 'Close', 'HA_Open', 'HA_Close', 'Trail', 'Pos']].tail(10))
    
    last_pos = df['Pos'].iloc[-1]
    status = "BULLISH" if last_pos == 1 else "BEARISH"
    print(f"\nCURRENT STATUS: {status}")
    print(f"Signal Time: {df.index[-1]}")
