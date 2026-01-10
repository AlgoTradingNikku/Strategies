from PureOptionsStrategy import (
    CONFIG, client, resolve_symbol_from_query, fetch_history
)
import pandas as pd
from datetime import datetime, timedelta
import os

def log_samples():
    log_file = "history_data.txt"
    
    # Resolve Symbols
    idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
    opt_symbol = resolve_symbol_from_query(CONFIG["trade_symbol"], exchange="NFO")
    
    # Dates
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    
    print(f"Fetching data for logging... Range: {start} to {end}")
    
    # Fetch Data
    df_index_3m = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval="3m")
    df_option_3m = fetch_history(opt_symbol, "NFO", start, end, interval="3m")
    df_index_15m = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval="15m")
    
    with open(log_file, "w") as f:
        f.write("=== HISTORY DATA SAMPLES (TOP 5 ROWS) ===\n\n")
        
        f.write(f"--- NIFTY INDEX (3m) --- \n")
        if not df_index_3m.empty:
            f.write(df_index_3m.head(5).to_string())
        else:
            f.write("Empty DataFrame")
        f.write("\n\n")
        
        f.write(f"--- NIFTY OPTION (3m) --- \n")
        if not df_option_3m.empty:
            f.write(df_option_3m.head(5).to_string())
        else:
            f.write("Empty DataFrame")
        f.write("\n\n")
        
        f.write(f"--- NIFTY INDEX (15m) --- \n")
        if not df_index_15m.empty:
            f.write(df_index_15m.head(5).to_string())
        else:
            f.write("Empty DataFrame")
        f.write("\n")

    print(f"Done! Samples written to {log_file}")

if __name__ == "__main__":
    log_samples()
