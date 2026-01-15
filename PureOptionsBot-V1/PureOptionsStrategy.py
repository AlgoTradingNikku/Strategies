"""
PureOptionsStrategy.py

A simplified, dedicated options trading strategy using UTBot.
Trades specific option contracts based on their UNDERLYING INDEX (Nifty) price action.
"""

import backtrader as bt
import pandas as pd
from datetime import datetime, timedelta
from openalgo import api
import sys
import os
import time
import yaml

# Add project root to path if running standalone
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if project_root not in sys.path:
    # Append to prioritize installed packages
    sys.path.append(project_root)

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    try:
        with open(config_path, 'r') as f:
            new_config = yaml.safe_load(f)
            if new_config:
                return new_config
    except Exception as e:
        print(f"[RELOAD ERROR] Could not load config.yaml: {e}")
    return {}

def update_config_globally():
    global CONFIG
    new_cfg = load_config()
    if new_cfg:
        CONFIG.update(new_cfg)
        return True
    return False

CONFIG = load_config()
# If initial load fails, we might have issues, but let's assume config.yaml exists now.

# ======================
# API CLIENT
# ======================
client = api(api_key=CONFIG["api_key"], host=CONFIG["api_host"])


# ======================
# UTILITY: RESOLVE SYMBOL
# ======================
def get_contract_type(symbol):
    """Infers Call/Put from symbol name"""
    symbol = symbol.upper()
    if "PE" in symbol: return "Put"
    if "CE" in symbol: return "Call"
    return "Call" # Default

def get_nearest_expiry(expiry_type="WEEKLY", offset=0):
    """
    Finds the nearest Nifty expiry using OpenAlgo API to account for holidays.
    Fallback to calculated Thursday if API fails.
    """
    try:
        # 1. Fetch Expiries from API
        response = client.expiry(symbol="NIFTY", exchange="NFO", instrumenttype="options")
        
        # 2. Parse and Filter
        expiries = []
        if isinstance(response, dict) and 'data' in response:
             raw_list = response['data']
        elif isinstance(response, list):
             raw_list = response
        else:
             raw_list = []

        today = datetime.now().date()
        valid_expiries = []
        
        for date_str in raw_list:
            try:
                # Format: "10-FEB-26" -> %d-%b-%y
                dt = datetime.strptime(date_str, "%d-%b-%y").date()
                if dt >= today:
                    valid_expiries.append(dt)
            except: pass
            
        if valid_expiries:
            valid_expiries.sort()
            
            # --- MONTHLY FILTERING ---
            if expiry_type == "MONTHLY":
                # Group by (Year, Month) and keep only the last date for each
                # This assumes valid_expiries contains both weeklies and monthlies
                monthly_expiries = []
                # Use a dictionary to keep track of max date per month
                from collections import defaultdict
                month_map = defaultdict(list)
                for d in valid_expiries:
                    month_map[(d.year, d.month)].append(d)
                
                # Sort keys to ensure chronological order
                sorted_keys = sorted(month_map.keys())
                for k in sorted_keys:
                    # Last date of the month is the monthly expiry
                    monthly_expiries.append(max(month_map[k]))
                
                # Replace the main list with filtered list
                valid_expiries = monthly_expiries

            # --- OFFSET SELECTION ---
            # Default offset is 0 (first available)
            # If offset is requested (e.g. 1 for next), apply it.
            # Safety: clamp to last available index
            target_idx = min(offset, len(valid_expiries) - 1)
            nearest = valid_expiries[target_idx]
            
            return nearest.strftime("%d%b%y").upper()
            
    except Exception as e:
        print(f"[WARN] API Expiry Fetch failed: {e}. Using backup calculation.")

    # FALLBACK CALCULATION
    now = datetime.now()
    
    # Target 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    # Nifty Expiry is Thursday (3)
    days_ahead = (3 - now.weekday() + 7) % 7
    
    # If today is Thursday and it's after market hours, look for next week
    if days_ahead == 0 and now.hour >= 15:
        days_ahead = 7
        
    nearest_thursday = now + timedelta(days=days_ahead)
    
    if expiry_type == "NEXT_WEEK":
        nearest_thursday += timedelta(days=7)
    
    # Formatting for OpenAlgo expected search query: DDMMMYY
    return nearest_thursday.strftime("%d%b%y").upper()

def get_strike_symbol(index_price, side, offset=0, expiry_date=None, expiry_type="WEEKLY", expiry_offset=0):
    """
    Orchestrates the resolution of the final trading symbol based on Index price.
    expiry_date can be passed as specific string (DDMMMYY) to override default logic.
    """
    # 1. Round to nearest 50 (Nifty)
    atm = round(index_price / 50) * 50
    # 2. Add offset (step)
    # side is "CALL" or "PUT"
    # if side is CALL, +offset is OTM, -offset is ITM. 
    # But user wants standard: -1 = ITM 1, 0 = ATM, 1 = OTM 1.
    # For CALL: ITM 1 = atm - 50. OTM 1 = atm + 50.
    # For PUT:  ITM 1 = atm + 50. OTM 1 = atm - 50.
    
    multiplier = 50
    if side.upper() == "CALL":
        strike = atm + (offset * multiplier)
        suffix = "CE"
    else:
        strike = atm - (offset * multiplier)
        suffix = "PE"
        
    if expiry_date:
        expiry = expiry_date
    else:
        expiry = get_nearest_expiry(expiry_type=expiry_type, offset=expiry_offset)
    query = f"NIFTY {expiry} {int(strike)} {suffix}"
    
    print(f"   [SYMB] Derived Query: {query} (Index: {index_price:.2f})")
    
    # Attempt direct resolution
    symbol = resolve_symbol_from_query(query, exchange="NFO")
    return symbol

def resolve_symbol_from_query(query, exchange="NFO"):
    """
    Resolves a descriptive query to an actionable Symbol Token.
    """
    # Special handling for Nifty Index which might not appear in standard search
    # If using NSE_INDEX, trust the symbol directly
    if exchange == "NSE_INDEX":
        return query
        
    if exchange == "NSE" and "NIFTY" in query.upper() and ("50" in query or len(query) < 10):
        return query

    # --- DIRECT RESOLUTION PATH (Bypass Search API) ---
    if exchange == "NFO":
        query_parts = [p.upper() for p in query.upper().split()]
        
        # 1. Trust Raw Tickers (No spaces)
        if len(query_parts) == 1:
            raw_sym = query_parts[0]
            # Heuristic: Raw NFO symbols are usually NIFTY + DDMMMYY + STRIKE + TYPE
            if raw_sym.startswith("NIFTY") and len(raw_sym) > 10:
                return raw_sym

        # 2. Local Concatenation for standard format: "NIFTY 13JAN26 26200 PE"
        if len(query_parts) >= 4 and query_parts[0] == "NIFTY":
            root = query_parts[0] # NIFTY
            expiry = "" # e.g. 13JAN26
            strike = "" # e.g. 26200
            opt_type = "" # PE/CE
            
            for p in query_parts:
                if p in ["CE", "PE"]: opt_type = p
                elif any(m in p for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
                    expiry = p
                elif p.isdigit() and len(p) >= 3:
                    strike = p
            
            if root and expiry and strike and opt_type:
                return f"{root}{expiry}{strike}{opt_type}"
    # --------------------------------------------------

    print(f"SEARCHING for: '{query}' in {exchange}...")
    try:
        response = client.search(query=query, exchange=exchange)
        
        candidates = []
        if isinstance(response, dict):
            if "status" in response and response.get("status") is False:
                print(f"API Error: {response.get('message')}")
                return None
            candidates = response.get('data', [])
        elif isinstance(response, list):
            candidates = response
            
        if not candidates:
            print(f"No results found for '{query}'")
            return None
            
        # For NFO (Options), be strict about root match
        if exchange == "NFO":
            query_parts = [p.upper() for p in query.upper().split()]
            root_asset = query_parts[0]
            
            # Identify Expiry, Strike, and Type from query
            expiry = "" # e.g. 13JAN26
            strike = "" # e.g. 26000
            opt_type = "" # CE/PE
            for p in query_parts:
                if p in ["CE", "PE"]: opt_type = p
                elif any(m in p for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
                    expiry = p
                elif p.isdigit() and len(p) >= 3:
                    strike = p

            # FALLBACK: If initial search returned nothing, try searching just the root asset
            if not candidates:
                print(f"Fallback: Searching for root asset '{root_asset}'...")
                try:
                    fb_res = client.search(query=root_asset, exchange=exchange)
                    if isinstance(fb_res, dict): candidates = fb_res.get('data', [])
                    elif isinstance(fb_res, list): candidates = fb_res
                except: pass

            if not candidates:
                print(f"No candidates found even with fallback search.")
                return None

            best_match = None
            for cand in candidates:
                sym = (cand.get('symbol') or cand.get('trading_symbol', '')).upper()
                if not sym: continue
                if not sym.startswith(root_asset): continue
                
                # Structural Check: Strip Asset and Expiry to isolate the strike
                rem = sym.replace(root_asset, "")
                if expiry: rem = rem.replace(expiry.upper(), "")
                
                if strike and opt_type:
                    target_rem = f"{strike}{opt_type}"
                    if rem == target_rem:
                        best_match = sym
                        break
            
            if best_match:
                print(f"Resolved: {best_match}")
                return best_match
            print(f"Could not match symbol with all parts: {query_parts}")
            return None

            
        # For NSE (Equity)
        for cand in candidates:
             sym = cand.get('symbol') or cand.get('trading_symbol')
             if sym == query: 
                 print(f"Resolved: {sym}")
                 return sym
        
        # Fallback to first (Only for non-NFO)
        if exchange != "NFO":
            first = candidates[0].get('symbol') or candidates[0].get('trading_symbol')
            print(f"Resolved (First Match): {first}")
            return first
        
        return None

    except Exception as e:
        print(f"Search Error: {e}")
        return None

# ======================
# OPENALGO DATA FETCH
# ======================
def fetch_history(symbol, exchange, start_date, end_date, interval=None, silent=False):
    """
    Fetches OHLCV data from OpenAlgo.
    Includes Heikin Ashi calculation.
    """
    # If interval is not provided, we fall back to Index LTF as a reasonable default for historical data fetch
    tf = interval if interval else CONFIG.get("index", {}).get("ltf", {}).get("timeframe", "3m")
    if not silent:
        print(f"FETCHING data for {symbol} ({exchange}, {tf})...")
    
    try:
        raw = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=tf,
            start_date=start_date,
            end_date=end_date
        )
        
        # Normalize Data
        df = pd.DataFrame()
        
        if isinstance(raw, pd.DataFrame):
            df = raw.copy()
        elif isinstance(raw, dict):
            if "data" in raw:
                df = pd.DataFrame(raw["data"])
            else:
                try: df = pd.DataFrame(raw)
                except: return pd.DataFrame()
        elif isinstance(raw, list):
            df = pd.DataFrame(raw)
            
        if df.empty:
            print("[WARN] Empty dataset returned.")
            return pd.DataFrame()
            
        # Format Columns & Index
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        
        # PRESERVE OI (Case insensitive check)
        oi_col = None
        for col in df.columns:
            if col.lower() in ['oi', 'openinterest', 'open_interest']:
                oi_col = col
                break
        
        if oi_col:
            df['oi'] = df[oi_col]
        elif "time" in df.columns:
             df["timestamp"] = pd.to_datetime(df["time"])
             df = df.set_index("timestamp")
        else:
            try:
                df.index = pd.to_datetime(df.index)
                df.index.name = "timestamp"
            except: pass

        # Standardize Columns
        col_map = {
            "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"
        }
        df.rename(columns=col_map, inplace=True)
        
        # Ensure Numeric and Drop NaN
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(inplace=True)
        
        # HEIKIN ASHI CALCULATION
        df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4.0
        
        ha_open_list = [df['Open'].iloc[0]]
        for i in range(1, len(df)):
            prev_ha_open = ha_open_list[i-1]
            prev_ha_close = df['HA_Close'].iloc[i-1]
            ha_open_list.append((prev_ha_open + prev_ha_close) / 2.0)
        df['HA_Open'] = ha_open_list
        
        df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
        df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
        
        if not silent:
            print(f"   Rows fetched: {len(df)}")
        return df
        
    except Exception as e:
        print(f"Data Fetch Error: {e}")
        return pd.DataFrame()

# ======================
# BACKTRADER CLASSES
# ======================

class PandasDataPlusHA(bt.feeds.PandasData):
    """Data Feed that includes Heikin Ashi columns"""
    lines = ('ha_open', 'ha_high', 'ha_low', 'ha_close',)
    params = (('ha_open', -1), ('ha_high', -1), ('ha_low', -1), ('ha_close', -1),)

class UTBotIndicator(bt.Indicator):
    """ UT Bot Indicator - Pure Implementation """
    lines = ('stop', 'buy_signal', 'sell_signal', 'pos')
    params = (('sensitivity', 1.0), ('atr_period', 10), ('use_ha', True))

    def __init__(self):
        # Source Selection
        self.src = self.data.ha_close if self.p.use_ha else self.data.close
        
        # Match ATR to Chart Type (HA chart uses HA ATR on TV)
        if self.p.use_ha:
             # Calculate True Range using HA values
             tr1 = self.data.ha_high - self.data.ha_low
             tr2 = abs(self.data.ha_high - self.data.ha_close(-1))
             tr3 = abs(self.data.ha_low - self.data.ha_close(-1))
             tr = bt.indicators.Max(tr1, tr2, tr3)
             # SmoothedMovingAverage is Wilders/RMA equivalent in Backtrader
             self.atr = bt.indicators.SmoothedMovingAverage(tr, period=self.p.atr_period)
        else:
             self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
             
        self.nLoss = self.p.sensitivity * self.atr
        self.pos_state = 0 # Internal state for trend

    def next(self):
        if len(self) < self.p.atr_period: return

        src = self.src[0]
        src_prev = self.src[-1]
        nLoss = self.nLoss[0]
        prev_stop = self.l.stop[-1] if not pd.isna(self.l.stop[-1]) else 0.0
        prev_pos = self.pos_state
        
        if src > prev_stop and src_prev > prev_stop:
            current_stop = max(prev_stop, src - nLoss)
        elif src < prev_stop and src_prev < prev_stop:
            current_stop = min(prev_stop, src + nLoss)
        elif src > prev_stop:
            current_stop = src - nLoss
        else:
            current_stop = src + nLoss
            
        self.l.stop[0] = current_stop
        
        current_pos = 0 
        prev_pos = self.pos_state
        
        if src_prev < prev_stop and src > prev_stop:
            current_pos = 1
        elif src_prev > prev_stop and src < prev_stop:
            current_pos = -1
        else:
            # FIX: Initialize trend based on price vs stop if state is neutral
            if prev_pos == 0:
                current_pos = 1 if src > current_stop else -1
            else:
                current_pos = prev_pos
        
        self.pos_state = current_pos
        self.l.pos[0] = float(current_pos)
        
        self.l.buy_signal[0] = 1.0 if (current_pos == 1 and prev_pos == -1) else 0.0
        self.l.sell_signal[0] = 1.0 if (current_pos == -1 and prev_pos == 1) else 0.0

class PureOptionsStrategy(bt.Strategy):
    """
    Strategy to Trade Options based on Underlying Index Signals
    data0 = Index (Nifty)
    data1 = Option (Contract)
    """
    params = (
        ("signal_source", CONFIG.get("signal_source", "OPTION")),
        ("lots", CONFIG.get("lots", 1)),
        
        # Index Params
        ("idx_ltf_sens", CONFIG["index"]["ltf"]["sensitivity"]),
        ("idx_ltf_atr",  CONFIG["index"]["ltf"]["atr"]),
        ("idx_htf_sens", CONFIG["index"]["htf"]["sensitivity"]),
        ("idx_htf_atr",  CONFIG["index"]["htf"]["atr"]),
        ("use_index_htf", CONFIG["index"]["htf"]["enabled"]),

        # Option Params
        ("opt_ltf_sens", CONFIG["option"]["ltf"]["sensitivity"]),
        ("opt_ltf_atr",  CONFIG["option"]["ltf"]["atr"]),
        ("opt_htf_sens", CONFIG["option"]["htf"]["sensitivity"]),
        ("opt_htf_atr",  CONFIG["option"]["htf"]["atr"]),
        ("use_option_htf", CONFIG["option"]["htf"]["enabled"]),

        ("index_use_ha", CONFIG.get("index_use_ha", True)),
        ("option_use_ha", CONFIG.get("option_use_ha", False)),
        ("verbose", True),
    )

    def log(self, txt, dt=None):
        if self.params.verbose:
            dt = dt or self.datas[0].datetime.datetime(0)
            print(f"{dt.strftime('%H:%M')} {txt}")

    def __init__(self):
        # Index Logic - Always data0
        self.idx_utbot = UTBotIndicator(
            self.datas[0],
            sensitivity=self.params.idx_ltf_sens,
            atr_period=self.params.idx_ltf_atr,
            use_ha=self.params.index_use_ha
        )
        
        # Option Logic - Always data1
        self.opt_utbot = UTBotIndicator(
            self.datas[1],
            sensitivity=self.params.opt_ltf_sens,
            atr_period=self.params.opt_ltf_atr,
            use_ha=self.params.option_use_ha
        )

        # HTF Indicators - Optional (data2 for Index HTF, data3 for Option HTF)
        self.idx_htf_utbot = None
        if len(self.datas) > 2 and self.params.use_index_htf:
             self.idx_htf_utbot = UTBotIndicator(
                self.datas[2],
                sensitivity=self.params.idx_htf_sens,
                atr_period=self.params.idx_htf_atr,
                use_ha=self.params.index_use_ha
            )
            
        self.opt_htf_utbot = None
        if len(self.datas) > 3 and self.params.use_option_htf:
             self.opt_htf_utbot = UTBotIndicator(
                self.datas[3],
                sensitivity=self.params.opt_htf_sens,
                atr_period=self.params.opt_htf_atr,
                use_ha=self.params.option_use_ha
            )
        
        self.order = None
        
        # Verify we have option data
        if len(self.datas) < 2:
            print("ERROR: Missing second data feed (Option Data)!")
            self.option_data = None
        else:
            self.option_data = self.datas[1]
            
        # Trailing Stop State
        self.highest_price = 0.0 # Track highest price during trade

        self.final_value = CONFIG["capital"]

        # Signal State
        
        # Volatility Indicator for TSL (ATR)
        self.atr_tsl = bt.indicators.ATR(self.option_data, period=14)

    def stop(self):
        self.final_value = self.broker.getvalue()


    def next(self):
        # 1. Map indicators for this cycle
        self.utbot = self.opt_utbot if self.params.signal_source == "OPTION" else self.idx_utbot
        self.htf_utbot = self.idx_htf_utbot # Priority HTF
        
        
        # 3. Basic Safety Checks
        if self.order: return 
        if not self.option_data: return
        
        # 4. Get Current Stats
        mode = CONFIG.get("trading_mode", "long").lower()
        position = self.getposition(self.option_data)
        
        # ---------------------------
        # RISK MANAGEMENT (On Option Price)
        # ---------------------------
        if position.size != 0:
            pos_dir = 1 if position.size > 0 else -1
            entry_price = position.price
            curr_price = self.option_data.close[0] # Use Option Price
            
            if curr_price == 0: return # Skip bad data

            pct_change = (curr_price - entry_price) / entry_price if pos_dir == 1 else (entry_price - curr_price) / entry_price
            
            # --- TRAILING STOP LOSS ---
            # Update Highest/Lowest Price for Trail
            if pos_dir == 1:
                if self.highest_price < curr_price: self.highest_price = curr_price

                # Multi-Mode Trailing Stop Loss
                mode = CONFIG.get("tsl_mode", "ATR").upper()
                dist_pts = 0.0

                if mode == "ATR" and len(self.atr_tsl) > 0:
                    dist_pts = self.atr_tsl[0] * CONFIG.get('tsl_atr_multiplier', 2.5)
                elif mode == "PERCENT":
                    dist_pts = self.highest_price * (CONFIG.get('tsl_percent', 4.0) / 100.0)
                elif mode == "POINTS":
                    dist_pts = CONFIG.get('tsl_points', 8.0)
                
                # Enforce minimum distance
                min_gap = CONFIG.get('min_trailing_gap', 5.0)
                dist_pts = max(dist_pts, min_gap)
                
                tsl_val = self.highest_price - dist_pts

                # --- Cost Protection (Break-Even) ---
                if self.highest_price >= entry_price * 1.01:
                    tsl_val = max(tsl_val, entry_price)

                if curr_price <= tsl_val:
                    self.log(f'TRAILING STOP HIT! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f} (High: {self.highest_price:.2f})')
                    self.order = self.close(data=self.option_data)
                    self.highest_price = 0.0
                    return

            else:
                # Short Logic
                if self.highest_price == 0 or self.highest_price > curr_price: self.highest_price = curr_price

                # Multi-Mode Trailing Stop Loss (Short)
                mode = CONFIG.get("tsl_mode", "ATR").upper()
                dist_pts = 0.0

                if mode == "ATR" and len(self.atr_tsl) > 0:
                    dist_pts = self.atr_tsl[0] * CONFIG.get('tsl_atr_multiplier', 2.5)
                elif mode == "PERCENT":
                    dist_pts = self.highest_price * (CONFIG.get('tsl_percent', 4.0) / 100.0)
                elif mode == "POINTS":
                    dist_pts = CONFIG.get('tsl_points', 8.0)

                # Enforce minimum distance
                min_gap = CONFIG.get('min_trailing_gap', 5.0)
                dist_pts = max(dist_pts, min_gap)
                
                tsl_val = self.highest_price + dist_pts

                # --- Cost Protection (Break-Even) ---
                if self.highest_price <= entry_price * 0.99:
                    tsl_val = min(tsl_val, entry_price)

                if curr_price >= tsl_val:
                    self.log(f'TRAILING STOP HIT (SHORT)! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f}')
                    self.order = self.close(data=self.option_data)
                    self.highest_price = 0.0
                    return
        
        # ENTRY LOGIC (On Index Signals)
        # ---------------------------
        c_type = get_contract_type(self.option_data._name).capitalize()
        
        if position.size == 0:
            # Check Index Signals (data0)
            if mode == "long":
                # Check HTF Filter if enabled
                htf_ok = True
                htf_target = 1 if c_type == "Call" else -1

                if self.idx_htf_utbot:
                    htf_curr = self.idx_htf_utbot.pos[0]
                    htf_ok = (htf_curr == htf_target)

                # Determine if we have the entry signal we want
                is_option_src = (self.params.signal_source == "OPTION")
                
                if is_option_src:
                     # Direct Signal from Option Chart
                     entry_signal = self.opt_utbot.buy_signal[0]
                     sig_name = "Bullish (Option)"
                else:
                    # Indirect Signal from Index
                    entry_signal = self.idx_utbot.buy_signal[0] if c_type == "Call" else self.idx_utbot.sell_signal[0]
                    sig_name = "Bullish (Index)" if c_type == "Call" else "Bearish (Index)"

                if entry_signal and htf_ok:
                    log_msg = f"[SIGNAL] Index {sig_name} detected"
                    self.log(f"{log_msg} @ {self.datas[0].close[0]:.2f}")
                    if self.idx_htf_utbot:
                        self.log(f"   (Index HTF confirms @ {self.datas[2].close[0]:.2f})")
                    
                    # Buy Option (data1)
                    if self.option_data.close[0] > 0:
                        self.order = self.buy(data=self.option_data, size=self.params.lots * 75)
                        if self.params.verbose:
                            self.log(f"   >>> BUYING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        self.highest_price = self.option_data.close[0] # Init Trail
            
            elif mode == "short":
                # Short Selling the Option (Margin heavy)
                htf_ok = True
                if self.htf_utbot and CONFIG.get("use_htf_filter", False):
                    htf_target = -1 if c_type == "Call" else 1
                    htf_ok = (self.htf_utbot.pos[0] == htf_target)

                # Determine if we have the entry signal we want
                is_option_src = (self.params.signal_source == "OPTION")

                if is_option_src:
                     # Direct Signal from Option Chart: Sell Signal means Price going down -> Short Option
                     entry_signal = self.utbot.sell_signal[0]
                     sig_name = "Bearish"
                else:
                    # Call: Short on Sell signal
                    # Put:  Short on Buy signal
                    entry_signal = self.utbot.sell_signal[0] if c_type == "Call" else self.utbot.buy_signal[0]
                    sig_name = "Bearish" if c_type == "Call" else "Bullish"

                if entry_signal and htf_ok:
                    self.log(f"[SIGNAL] Index {sig_name} detected @ {self.datas[0].close[0]:.2f}")
                    # Sell Option (data1)
                    if self.option_data.close[0] > 0:
                        self.order = self.sell(data=self.option_data, size=self.params.lots * 75)
                        if self.params.verbose:
                            self.log(f"   >>> SELLING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        self.highest_price = self.option_data.close[0] # Init Trail
        
        # ---------------------------
        # EXIT LOGIC (On Index Signals)
        # ---------------------------
        else:
            if mode == "long":
                is_option_src = (self.params.signal_source == "OPTION")
                
                if is_option_src:
                     exit_signal = self.utbot.sell_signal[0]
                else:
                    # Call: Exit on Sell signal
                    # Put:  Exit on Buy signal
                    exit_signal = self.utbot.sell_signal[0] if c_type == "Call" else self.utbot.buy_signal[0]
                if exit_signal:
                    should_exit = True
                    # TSL PRIORITY: Ignore exit signal if we are in profit and TSL is above entry
                    if self.highest_price > 0:
                        # Calculate current dynamic TSL distance based on mode
                        mode = CONFIG.get("tsl_mode", "ATR").upper()
                        dist_pts = 0.0
                        if mode == "ATR" and len(self.atr_tsl) > 0:
                             dist_pts = self.atr_tsl[0] * CONFIG.get("tsl_atr_multiplier", 2.5)
                        elif mode == "PERCENT":
                             dist_pts = self.highest_price * (CONFIG.get("tsl_percent", 4.0) / 100.0)
                        elif mode == "POINTS":
                             dist_pts = CONFIG.get("tsl_points", 8.0)
                        
                        min_gap = CONFIG.get("min_trailing_gap", 2.5)
                        dist_pts = max(dist_pts, min_gap)
                        
                        tsl_val = self.highest_price - dist_pts
                        curr_price = self.option_data.close[0]
                        
                        # Profit-Protected: Only ignore if TSL has moved above entry
                        if curr_price > tsl_val and tsl_val > entry_price:
                            should_exit = False
                            self.log(f"[FILTERED] Trend Reversed but Profit Locked (TSL {tsl_val:.2f} > Entry {entry_price:.2f}). Holding...")
                    
                    if should_exit:
                        self.log(f"[EXIT SIGNAL] Index flipped @ {self.datas[0].close[0]:.2f}")
                        self.order = self.close(data=self.option_data)
                        if self.params.verbose:
                             self.log(f"   >>> CLOSING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
            
            elif mode == "short":
                is_option_src = (self.params.signal_source == "OPTION")
                
                if is_option_src:
                     exit_signal = self.utbot.buy_signal[0]
                else:
                    # Call: Exit on Buy signal
                    # Put:  Exit on Sell signal
                    exit_signal = self.utbot.buy_signal[0] if c_type == "Call" else self.utbot.sell_signal[0]
                if exit_signal:
                    should_exit = True
                    # TSL PRIORITY (Short): Ignore cover signal if TSL floor is below entry
                    if self.highest_price > 0:
                        # Calculate current dynamic TSL distance based on mode
                        mode = CONFIG.get("tsl_mode", "ATR").upper()
                        dist_pts = 0.0
                        if mode == "ATR" and len(self.atr_tsl) > 0:
                             dist_pts = self.atr_tsl[0] * CONFIG.get("tsl_atr_multiplier", 2.5)
                        elif mode == "PERCENT":
                             dist_pts = self.highest_price * (CONFIG.get("tsl_percent", 4.0) / 100.0)
                        elif mode == "POINTS":
                             dist_pts = CONFIG.get("tsl_points", 8.0)
                        
                        min_gap = CONFIG.get("min_trailing_gap", 2.5)
                        dist_pts = max(dist_pts, min_gap)

                        tsl_val = self.highest_price + dist_pts
                        curr_price = self.option_data.close[0]
                        
                        # Short logic: Profit Locked if TSL floor is BELOW entry
                        if curr_price < tsl_val and tsl_val < entry_price:
                            should_exit = False
                            self.log(f"[FILTERED] Trend Reversed but Profit Locked (TSL {tsl_val:.2f} < Entry {entry_price:.2f}). Holding...")
                    
                    if should_exit:
                        self.log(f"[COVER SIGNAL] Index flipped @ {self.datas[0].close[0]:.2f}")
                        self.order = self.close(data=self.option_data)
                        if self.params.verbose:
                            self.log(f"   >>> CLOSING SHORT {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        

# ======================
# RUNNERS
# ======================
def run_backtest():
    print("\n" + "="*50)
    print("STARTING BACKTEST (4-Way Precision)")
    print("-" * 50)
    
    idx_conf = CONFIG.get("index", {})
    opt_conf = CONFIG.get("option", {})
    
    # 1. Resolve Symbols
    idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
    trade_sym = CONFIG.get("trade_symbol", "")
    
    print(f"\n[BACKTEST SETUP]")
    user_input = input(f"   Enter Option Symbol to test (Leave empty to derive ATM from Index): ").strip()
    if user_input:
        trade_sym = user_input
    
    if not trade_sym:
        print("   [INFO] No symbol provided. Attempting to find a representative ATM strike...")
        # Get current index price for reference
        end_temp = datetime.now().strftime("%Y-%m-%d")
        start_temp = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        df_temp = fetch_history(idx_symbol, CONFIG["index_exchange"], start_temp, end_temp, interval="15m", silent=True)
        if not df_temp.empty:
            idx_price = df_temp['Close'].iloc[-1]
            ss = CONFIG["strike_selection"]
            
            # Allow expiry override
            default_expiry = get_nearest_expiry(ss.get("expiry", "WEEKLY"), offset=ss.get("offset", 0))
            print(f"   [INFO] Detected Expiry: {default_expiry}")
            expiry_input = input(f"   Enter Custom Expiry (DDMMMYY) to override or Press Enter to accept: ").strip().upper()
            final_expiry = expiry_input if expiry_input else default_expiry

            trade_sym = get_strike_symbol(
                idx_price, 
                "CALL", 
                ss.get("step", 0), 
                expiry_date=final_expiry, 
                expiry_type=ss.get("expiry", "WEEKLY"), 
                expiry_offset=ss.get("offset", 0)
            )
            if trade_sym:
                print(f"   [INFO] Automatically derived: {trade_sym}")
    
    opt_symbol = resolve_symbol_from_query(trade_sym, exchange="NFO")
    
    if not idx_symbol or not opt_symbol:
        print("[FAIL] Could not resolve required symbols.")
        return

    # 2. Fetch Data
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    print(f"[INFO] Data Range: {start} to {end}")
    
    df_idx_ltf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['ltf']['timeframe'])
    df_opt_ltf = fetch_history(opt_symbol, "NFO", start, end, interval=opt_conf['ltf']['timeframe'])
    
    df_idx_htf = pd.DataFrame()
    if idx_conf['htf']['enabled']:
        df_idx_htf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['htf']['timeframe'])
        
    df_opt_htf = pd.DataFrame()
    if opt_conf['htf']['enabled']:
        df_opt_htf = fetch_history(opt_symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'])

    if df_idx_ltf.empty or df_opt_ltf.empty:
        print("[FAIL] Missing primary data.")
        if df_opt_ltf.empty:
             print(f"   [TIP] Option data for {opt_symbol} is missing (Likely illiquid/future contract).")
             print(f"   [TIP] Try testing with a recently expired contract (e.g. from last week).")
        return

    # 3. Setup Cerebro
    cerebro = bt.Cerebro()
    cerebro.addstrategy(PureOptionsStrategy)
    
    # data0: Index LTF
    cerebro.adddata(PandasDataPlusHA(dataname=df_idx_ltf), name="INDEX_LTF")
    # data1: Option LTF
    cerebro.adddata(PandasDataPlusHA(dataname=df_opt_ltf), name="OPTION_LTF")
    
    # data2: Index HTF
    if not df_idx_htf.empty:
        cerebro.adddata(PandasDataPlusHA(dataname=df_idx_htf), name="INDEX_HTF")
    else:
        # Add dummy/empty to maintain index if needed, but Strategy handles len(datas)
        pass
        
    # data3: Option HTF
    if not df_opt_htf.empty:
        # Note: To ensure data3 is actually data3, we might need to add Index HTF even if empty
        # But our strategy checks len(datas) so it's fine.
        cerebro.adddata(PandasDataPlusHA(dataname=df_opt_htf), name="OPTION_HTF")

    cerebro.broker.setcash(CONFIG["capital"])
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="TA")
    
    print(f"[INFO] Starting Capital: {CONFIG['capital']}")
    results = cerebro.run()
    strat = results[0]
    ta = strat.analyzers.TA.get_analysis()
    
    final_val = cerebro.broker.getvalue()
    pnl = final_val - CONFIG["capital"]
    total_closed = ta.get("total", {}).get("closed", 0)
    total_open = ta.get("total", {}).get("open", 0)
    
    print("\n" + "="*50)
    print("BACKTEST RESULTS (4-Way)")
    print("="*50)
    print(f"Index LTF:     {idx_conf['ltf']['timeframe']} | Option LTF: {opt_conf['ltf']['timeframe']}")
    print(f"Final Value:   {final_val:.2f}")
    print(f"Net P&L:       {pnl:+.2f} ({(pnl/CONFIG['capital'])*100:.2f}%)")
    print(f"Trades:        {total_closed} Closed | {total_open} Open")
    
    if total_closed > 0:
        won_pnl = ta.get("won", {}).get("pnl", {}).get("total", 0)
        lost_pnl = ta.get("lost", {}).get("pnl", {}).get("total", 0)
        print(f"   Won PnL:   +{won_pnl:.2f}")
        print(f"   Lost PnL:  {lost_pnl:.2f}")

    print("="*50 + "\n")

def run_optimization():
    print("\n" + "="*50)
    print("STARTING OPTIMIZATION (4-Way Precision)")
    print("="*50)
    
    idx_conf = CONFIG.get("index", {})
    opt_conf = CONFIG.get("option", {})
    
    # 1. Resolve Symbols
    idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
    
    trade_sym = CONFIG.get("trade_symbol", "")
    print(f"\n[OPTIMIZATION SETUP]")
    user_input = input(f"   Enter Option Symbol to optimize for (Leave empty to derive ATM): ").strip()
    if user_input:
        trade_sym = user_input
        
    if not trade_sym:
        print("   [INFO] No symbol provided. Attempting to find a representative ATM strike...")
        end_temp = datetime.now().strftime("%Y-%m-%d")
        start_temp = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        df_temp = fetch_history(idx_symbol, CONFIG["index_exchange"], start_temp, end_temp, interval="15m", silent=True)
        if not df_temp.empty:
            idx_price = df_temp['Close'].iloc[-1]
            ss = CONFIG["strike_selection"]
            
            # Allow expiry override
            default_expiry = get_nearest_expiry(ss.get("expiry", "WEEKLY"), offset=ss.get("offset", 0))
            print(f"   [INFO] Detected Expiry: {default_expiry}")
            expiry_input = input(f"   Enter Custom Expiry (DDMMMYY) to override or Press Enter to accept: ").strip().upper()
            final_expiry = expiry_input if expiry_input else default_expiry

            trade_sym = get_strike_symbol(
                idx_price, 
                "CALL", 
                ss.get("step", 0), 
                expiry_date=final_expiry, 
                expiry_type=ss.get("expiry", "WEEKLY"), 
                expiry_offset=ss.get("offset", 0)
            )
            if trade_sym:
                print(f"   [INFO] Automatically derived: {trade_sym}")
    
    opt_symbol = resolve_symbol_from_query(trade_sym, exchange="NFO")
    
    if not idx_symbol or not opt_symbol:
        print("[FAIL] Could not resolve required symbols.")
        return

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    
    df_idx_ltf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['ltf']['timeframe'])
    df_opt_ltf = fetch_history(opt_symbol, "NFO", start, end, interval=opt_conf['ltf']['timeframe'])
    
    df_idx_htf = pd.DataFrame()
    if idx_conf['htf']['enabled']:
        df_idx_htf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['htf']['timeframe'])
        
    df_opt_htf = pd.DataFrame()
    if opt_conf['htf']['enabled']:
        df_opt_htf = fetch_history(opt_symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'])

    if df_idx_ltf.empty or df_opt_ltf.empty:
        print("[FAIL] Missing primary data.")
        return

    cerebro = bt.Cerebro()
    
    # Mapping Optimization Parameters from OPT_MAP
    # This allows users to tweak the ranges easily
    cerebro.optstrategy(
        PureOptionsStrategy,
        idx_ltf_sens=CONFIG["OPT_MAP"]["index_ltf_sens"],
        opt_ltf_sens=CONFIG["OPT_MAP"]["option_ltf_sens"],
        idx_htf_sens=CONFIG["OPT_MAP"]["index_htf_sens"],
        idx_ltf_atr=CONFIG["OPT_MAP"]["atr_period"],
        opt_ltf_atr=CONFIG["OPT_MAP"]["atr_period"],
        
        use_heikin_ashi=[CONFIG.get("use_heikin_ashi", False)],
        live_trade=[False], 
        execute_backtest_orders=[False],
        verbose=[False]
    )

    # Add Data Feeds
    cerebro.adddata(PandasDataPlusHA(dataname=df_idx_ltf), name="INDEX_LTF")
    cerebro.adddata(PandasDataPlusHA(dataname=df_opt_ltf), name="OPTION_LTF")
    
    if not df_idx_htf.empty:
        cerebro.adddata(PandasDataPlusHA(dataname=df_idx_htf), name="INDEX_HTF")
    if not df_opt_htf.empty:
        cerebro.adddata(PandasDataPlusHA(dataname=df_opt_htf), name="OPTION_HTF")
    
    cerebro.broker.setcash(CONFIG["capital"])
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="TA")
    
    print(f"\n[INFO] Running Optimization...")
    results = cerebro.run(maxcpus=1, optreturn=False)
    
    final_stats = []
    for run in results:
        for strat in run:
            ta = strat.analyzers.TA.get_analysis()
            pnl = strat.final_value - CONFIG["capital"]
            
            final_stats.append({
                "idx_sens": strat.params.idx_ltf_sens,
                "opt_sens": strat.params.opt_ltf_sens,
                "idx_atr": strat.params.idx_ltf_atr,
                "idx_h_sens": strat.params.idx_htf_sens,
                "pnl": pnl,
                "closed": ta.get("total", {}).get("closed", 0),
                "open": ta.get("total", {}).get("open", 0)
            })
            
    sorted_stats = sorted(final_stats, key=lambda x: x["pnl"], reverse=True)
    
    print(f"\n{'='*95}")
    print(f"TOP RESULTS (4-Way Optimization)")
    print(f"{'Idx-S':<6} | {'Opt-S':<6} | {'Idx-ATR':<7} | {'Idx-HS':<6} | {'PnL':>10} | {'Closed':>6}")
    print("-" * 95)
    for res in sorted_stats[:15]:
        print(f"{res['idx_sens']:<6.1f} | {res['opt_sens']:<6.1f} | {res['idx_atr']:<7} | {res['idx_h_sens']:<6.1f} | {res['pnl']:>10.2f} | {res['closed']:>6}")
    print("="*95 + "\n")

if __name__ == "__main__":
    try:
        while True:
            print(f"\n--- Index-Signal Options Bot ---")
            print(f"Index: {CONFIG['index_query']} | Option: {CONFIG['trade_symbol']}")
            

            print("1. Run Optimization")
            print("2. Run Backtest")
            print("3. Exit")
            
            choice = input("Select: ").strip()
            
            if choice == "1": run_optimization()
            elif choice == "2": run_backtest()
            elif choice == "3": 
                print("Exiting...")
                break
            else: print("Invalid choice.")
    except KeyboardInterrupt:
        print("\n\nExiting per user request.")

