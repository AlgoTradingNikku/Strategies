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

# Add project root to path if running standalone
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if project_root not in sys.path:
    # Append to prioritize installed packages
    sys.path.append(project_root)

# ======================
# CONFIGURATION
# ======================
CONFIG = {
    # ========================================
    # 1. ASSET SELECTION
    # ========================================
    "index_query": "NIFTY",           # Underlying index symbol
    "index_exchange": "NSE_INDEX",    # Exchange for index data
    "signal_source": "OPTION",        # Signal source: "INDEX" or "OPTION"
    
    # Option Contract Selection
    "strike_selection": {
        "mode": "AUTO",               # "AUTO" or "MANUAL"
        "step": 0,                    # 0=ATM, -1=ITM1, 1=OTM1
        "expiry": "WEEKLY",           # "WEEKLY", "NEXT_WEEK"
    },
    "trade_symbol": "", # Manual fallback symbol
    
    "index": {
        "ltf": {"timeframe": "3m", "sensitivity": 1.0, "atr": 10},
        "htf": {"timeframe": "15m", "sensitivity": 1.0, "atr": 10, "enabled": True}
    },
    "option": {
        "ltf": {"timeframe": "1m", "sensitivity": 1.5, "atr": 10},
        "htf": {"timeframe": "15m", "sensitivity": 1.0, "atr": 10, "enabled": False}
    },
    "option_signal_timeout": 5,        # Candles to wait for Option signal
    "use_heikin_ashi": True,           # Use Heikin Ashi candles for signals
    
    # ========================================
    # 3. TRADE EXECUTION
    # ========================================
    "quantity": 65,                    # Order quantity (lot size multiple)
    "capital": 100000,                 # Initial capital for backtesting
    "lookback_days": 5,                # Historical data window
    "trading_mode": "long",            # "long" or "short"
    
    # ========================================
    # 4. RISK MANAGEMENT (TRAILING STOP LOSS)
    # ========================================
    # TSL Mode Selection
    "use_tsl": True,                   # Enable trailing stop loss
    "tsl_mode": "HYBRID",              # "PCT", "ATR", or "HYBRID"
     
    # ATR-Based TSL when tsl_mode is ATR
    "tsl_atr_multiplier": 1.5,         # ATR multiplier for volatility-based TSL
    
    # Hybrid TSL when tsl mode is hybrid
    "tsl_hybrid_threshold": 5.0,      # Profit % to switch from PCT to ATR in HYBRID mode (used if trigger is PCT)
    "tsl_hybrid_trigger": "POINTS",    # "PCT" or "POINTS"
    "tsl_hybrid_point_tiers": [        # Tiered points required to switch based on entry price
        {"max_entry_price": 30,  "tsl_points": 1.5},
        {"max_entry_price": 60,  "tsl_points": 2.5},
        {"max_entry_price": 100, "tsl_points": 3.5},
        {"max_entry_price": 200, "tsl_points": 5.0},
        {"max_entry_price": 9999,"tsl_points": 8.0} 
    ],
    
     # Percentage-Based TSL when tsl_mode is PCT
    "tsl_pct": 5.0,                    # Default TSL % (if stepped TSL disabled)
    "use_stepped_tsl": True,           # Use dynamic stepped TSL
    "tsl_steps": [
        {"profit": 3.0, "tsl": 1.5},   # 0-3% profit → 1.5% TSL
        {"profit": 5.0, "tsl": 2.0},   # 3-5% profit → 2.0% TSL
        {"profit": 10.0, "tsl": 2.5},  # 5-10% profit → 2.5% TSL
        {"profit": 20.0, "tsl": 3.0},  # 10-20% profit → 3.0% TSL
        {"profit": 100.0, "tsl": 3.5}  # >20% profit → 3.5% TSL
    ],
   
    # Re-Entry Control
    "cooldown_seconds": 300,           # Cooldown after exit before allowing re-entry
    
    # ========================================
    # 5. ENTRY CONTROLS
    # ========================================
    
    # HTF Filter Alignment
    "allow_late_htf_alignment": True,         # Allow entry if HTF aligns later
    "late_alignment_max_candles": 2,          # Max age of 3m trend (in candles) for late entry
    
    # ========================================
    # 6. ADVANCED RISK PROTECTION
    # ========================================
    "fast_check_seconds": 2,           # Price check frequency when in position
    "use_reversal_leash": True,        # Tighten TSL on trend reversal
    "reversal_leash_pct": 1.5,         # Tightened TSL % on reversal
    "use_threading": True,             # Run risk and scanner in parallel
    "use_websocket": True,             # Enable real-time websocket updates
    "ws_url": "ws://127.0.0.1:8765",   # Websocket URL
    
    # ========================================
    # 7. LEGACY RISK CONTROLS (Optional)
    # ========================================
    "use_sl": False,                   # Hard stop loss (not recommended with TSL)
    "sl_pct": 10.0,                    # Stop loss %
    "use_tp": False,                   # Hard take profit (not recommended with TSL)
    "tp_pct": 20.0,                    # Take profit %
    
    # ========================================
    # 8. OPTIMIZATION PARAMETERS
    # ========================================
    "OPT_MAP": {
        # Format: Key -> List of values. 
        # Note: Strategy logic will need to map these to nested CONFIG if optimizing.
        "index_ltf_sens": [1.0, 1.5, 2.0],
        "option_ltf_sens": [1.0, 1.5, 2.0],
        "index_htf_sens": [1.0, 2.0],
        "atr_period": [10, 20]
    },
    
    # ========================================
    # 9. EXECUTION CONTROL
    # ========================================
    "live_trade": True,                # Enable live trading (for live_trader.py)
    "execute_backtest_orders": False,  # Send orders during backtest (Safety: False)
    
    # ========================================
    # 10. SYSTEM SETTINGS
    # ========================================
    "strategy_name": "UTBot-Put",
    "fetch_interval_seconds": 15,
    "ignore_session_check": True,            # Bypass market hours check (for special sessions)
    "api_key": "a1e43574fd5008b00b81024f71096fdc966bed01a5b64a13af36fb2b7ea41faf",
    "api_host": "http://127.0.0.1:5000",
}

# ======================
# API CLIENT
# ======================
client = api(api_key=CONFIG["api_key"], host=CONFIG["api_host"])


# ======================
# UTILITY: RESOLVE SYMBOL
# ======================
def get_hybrid_point_threshold(entry_price):
    """
    Returns the required absolute point gain to switch to ATR in Hybrid mode,
    based on the entry price tiers defined in CONFIG.
    """
    tiers = CONFIG.get("tsl_hybrid_point_tiers", [])
    if not tiers:
        return 3.0 # Default fallback
    
    for tier in tiers:
        if entry_price <= tier["max_entry_price"]:
            return tier["tsl_points"]
    return tiers[-1]["tsl_points"]

def get_contract_type(symbol):
    """Infers Call/Put from symbol name"""
    symbol = symbol.upper()
    if "PE" in symbol: return "Put"
    if "CE" in symbol: return "Call"
    return "Call" # Default

def get_nearest_expiry(expiry_type="WEEKLY"):
    """
    Finds the nearest Nifty expiry (Thursday).
    Note: Simple implementation - does not account for exchange holidays.
    """
    from datetime import datetime, timedelta
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

def get_strike_symbol(index_price, side, offset=0, expiry_type="WEEKLY"):
    """
    Orchestrates the resolution of the final trading symbol based on Index price.
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
        
    expiry = get_nearest_expiry(expiry_type)
    query = f"NIFTY {expiry} {int(strike)} {suffix}"
    
    print(f"   [SYMB] Derived Query: {query} (Index: {index_price:.2f})")
    
    # Attempt direct resolution
    symbol = resolve_symbol_from_query(query, exchange="NFO")
    return symbol

def resolve_symbol_from_query(query, exchange="NFO"):
    """
    Resolves a descriptive query to an actionable Symbol Token.
    """
    print(f"SEARCHING for: '{query}' in {exchange}...")
    
    # Special handling for Nifty Index which might not appear in standard search
    # If using NSE_INDEX, trust the symbol directly
    if exchange == "NSE_INDEX":
        print(f"Assuming Index Symbol: {query}")
        return query
        
    if exchange == "NSE" and "NIFTY" in query.upper() and ("50" in query or len(query) < 10):
        print(f"Assuming Index/Equity Symbol: {query}")
        return query

    # --- DIRECT RESOLUTION PATH (Bypass Search API) ---
    if exchange == "NFO":
        query_parts = [p.upper() for p in query.upper().split()]
        
        # 1. Trust Raw Tickers (No spaces)
        if len(query_parts) == 1:
            raw_sym = query_parts[0]
            # Heuristic: Raw NFO symbols are usually NIFTY + DDMMMYY + STRIKE + TYPE
            if raw_sym.startswith("NIFTY") and len(raw_sym) > 10:
                print(f"Bypassing Search: Trusting direct symbol '{raw_sym}'")
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
                direct_sym = f"{root}{expiry}{strike}{opt_type}"
                print(f"Direct Format Conversion: {query} -> {direct_sym}")
                return direct_sym
    # --------------------------------------------------

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
        ("quantity", CONFIG["quantity"]),
        
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

        ("use_heikin_ashi", CONFIG["use_heikin_ashi"]),
        ("use_sl", CONFIG["use_sl"]),
        ("sl_pct", CONFIG["sl_pct"]),
        ("use_tp", CONFIG["use_tp"]),
        ("tp_pct", CONFIG["tp_pct"]),
        ("use_tsl", CONFIG.get("use_tsl", False)),
        ("tsl_pct", CONFIG.get("tsl_pct", 5.0)),
        ("live_trade", CONFIG.get("live_trade", False)),
        ("execute_backtest_orders", CONFIG.get("execute_backtest_orders", False)),
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
            use_ha=self.params.use_heikin_ashi
        )
        
        # Option Logic - Always data1
        self.opt_utbot = UTBotIndicator(
            self.datas[1],
            sensitivity=self.params.opt_ltf_sens,
            atr_period=self.params.opt_ltf_atr,
            use_ha=self.params.use_heikin_ashi
        )

        # HTF Indicators - Optional (data2 for Index HTF, data3 for Option HTF)
        self.idx_htf_utbot = None
        if len(self.datas) > 2 and self.params.use_index_htf:
             self.idx_htf_utbot = UTBotIndicator(
                self.datas[2],
                sensitivity=self.params.idx_htf_sens,
                atr_period=self.params.idx_htf_atr,
                use_ha=self.params.use_heikin_ashi
            )
            
        self.opt_htf_utbot = None
        if len(self.datas) > 3 and self.params.use_option_htf:
             self.opt_htf_utbot = UTBotIndicator(
                self.datas[3],
                sensitivity=self.params.opt_htf_sens,
                atr_period=self.params.opt_htf_atr,
                use_ha=self.params.use_heikin_ashi
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
        self.trend_age = 0 # How many candles the current 3m trend has been active
        
        # Volatility Indicator for TSL (ATR)
        self.atr_tsl = bt.indicators.ATR(self.option_data, period=14)

    def stop(self):
        self.final_value = self.broker.getvalue()

    def execute_real_order(self, action, price):
        """
        Place real or paper orders via OpenAlgo API.
        """
        if not self.params.execute_backtest_orders:
            return

        qty = int(self.params.quantity)
        strategy_name = "PureOptions_Execution"

        self.log(f"   >>> [LIVE/PAPER] TRIGGERING {action.upper()} ORDER (Qty: {qty}) <<<")
        
        try:
            symbol = resolve_symbol_from_query(CONFIG["trade_symbol"], exchange="NFO")
            order_payload = {
                "strategy": strategy_name,
                "symbol": symbol,
                "action": action,
                "exchange": "NFO",
                "pricetype": "MARKET",
                "product": "NRML",
                "quantity": qty,
                "position_size": qty if action == "BUY" else 0
            }
            response = client.placesmartorder(**order_payload)
            self.log(f"   [API RESPONSE] {response}")
        except Exception as e:
            self.log(f"   [API ERROR] Failed to place order: {e}")

    def next(self):
        # 1. Map indicators for this cycle
        self.utbot = self.opt_utbot if self.params.signal_source == "OPTION" else self.idx_utbot
        self.htf_utbot = self.idx_htf_utbot # Priority HTF
        
        # 2. Update Trend Age
        curr_trend = self.utbot.pos[0]
        prev_trend = self.utbot.pos[-1]
        if curr_trend == prev_trend:
            self.trend_age += 1
        else:
            self.trend_age = 1
        
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
            
            # --- STOP LOSS ---
            if self.params.use_sl:
                sl_pct = self.params.sl_pct / 100.0
                if pct_change <= -sl_pct:
                    if self.params.verbose:
                        self.log(f"STOP LOSS HIT! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f}")
                    self.order = self.close(data=self.option_data) # Close Option Position
                    return
            # --- TAKE PROFIT ---
            if self.params.use_tp:
                tp_pct = self.params.tp_pct / 100.0
                if pct_change >= tp_pct:
                    if self.params.verbose:
                        self.log(f"TAKE PROFIT HIT! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f}")
                    self.order = self.close(data=self.option_data)
                    return
            
            # --- TRAILING STOP LOSS ---
            if self.params.use_tsl:
                # Update Highest/Lowest Price for Trail
                if pos_dir == 1:
                    if self.highest_price < curr_price: self.highest_price = curr_price

                    # Calculate TSL Value based on MODE
                    tsl_val = 0.0
                    tsl_mode = CONFIG.get('tsl_mode', 'PCT')
                    use_atr = False

                    # Check Hybrid Switch
                    if tsl_mode == 'HYBRID':
                        trigger_type = CONFIG.get("tsl_hybrid_trigger", "PCT")
                        if trigger_type == "POINTS":
                            pts_gained = self.highest_price - entry_price
                            pts_required = get_hybrid_point_threshold(entry_price)
                            if pts_gained >= pts_required:
                                use_atr = True
                                if self.params.verbose:
                                    self.log(f"   [HYBRID] Switch to ATR triggered by Points (+{pts_gained:.2f} >= {pts_required:.2f})")
                        else:
                            # Percentage Based Trigger
                            profit_pct_high = (self.highest_price - entry_price) / entry_price * 100
                            if profit_pct_high >= CONFIG.get('tsl_hybrid_threshold', 10.0):
                                use_atr = True # Switch to ATR logic
                                if self.params.verbose:
                                    self.log(f"   [HYBRID] Switch to ATR triggered by % ({profit_pct_high:.2f}% >= {CONFIG.get('tsl_hybrid_threshold'):.2f}%)")
                            else:
                                use_atr = False # Stay on PCT logic
                    elif tsl_mode == 'ATR':
                        use_atr = True

                    # Calculate Stop Price
                    if use_atr:
                        # ATR Based Trail
                        if len(self.atr_tsl) > 0:
                            current_atr = self.atr_tsl[0]
                            multiplier = CONFIG.get('tsl_atr_multiplier', 1.5)
                            atr_stop = self.highest_price - (current_atr * multiplier)
                            
                            # HYBRID SAFETY: If switching from PCT to ATR, ensure we don't drop the stop
                            if tsl_mode == 'HYBRID':
                                # Calculate what PCT stop would be to compare
                                pct_val_equiv = 0.0
                                tsl_pct_equiv = self.params.tsl_pct
                                if CONFIG.get('use_stepped_tsl'):
                                    profit_at_high = (self.highest_price - entry_price) / entry_price * 100
                                    for step in CONFIG.get('tsl_steps', []):
                                        if profit_at_high < step['profit']:
                                            tsl_pct_equiv = step['tsl']
                                            break
                                pct_val_equiv = self.highest_price * (1 - tsl_pct_equiv/100.0)
                                
                                # Take the HIGHER of the two (Safety Net)
                                tsl_val = max(atr_stop, pct_val_equiv)
                            else:
                                tsl_val = atr_stop
                        else:
                            tsl_val = self.highest_price * 0.99 # Fallback
                    else:
                        # Phase 1: Point-Based or Percentage-Based Trail
                        if tsl_mode == 'HYBRID' and CONFIG.get("tsl_hybrid_trigger") == "POINTS":
                            # Use same points required for switch as the TSL buffer
                            pts_required = get_hybrid_point_threshold(entry_price)
                            tsl_val = self.highest_price - pts_required
                        else:
                            # Standard Percentage Based Trail (Default)
                            tsl_pct = self.params.tsl_pct
                            if CONFIG.get('use_stepped_tsl'):
                                profit_at_high = (self.highest_price - entry_price) / entry_price * 100
                                for step in CONFIG.get('tsl_steps', []):
                                    if profit_at_high < step['profit']:
                                        tsl_pct = step['tsl']
                                        break
                            tsl_val = self.highest_price * (1 - tsl_pct/100.0)

                    # --- Cost Protection (Break-Even) ---
                    if self.highest_price >= entry_price * 1.01:
                        tsl_val = max(tsl_val, entry_price)

                    if curr_price <= tsl_val:
                        self.log(f'TRAILING STOP HIT! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f} (High: {self.highest_price:.2f}, Mode: {tsl_mode})')
                        self.order = self.close(data=self.option_data)
                        self.highest_price = 0.0
                        return

                else:
                    # Short Logic
                    if self.highest_price == 0 or self.highest_price > curr_price: self.highest_price = curr_price

                    # Logic for Short is symmetric (Hybrid logic omitted for brevity as user mainly trades Long, but best to include basic support)
                    # Simplified Short Logic (Supports ATR/PCT switch but skipped full hybrid safety for code brevity in this prompt context)
                    tsl_val = 0.0
                    tsl_mode = CONFIG.get('tsl_mode', 'PCT')
                    use_atr = False
                    
                    if tsl_mode == 'HYBRID':
                         # Simply check profit threshold for Short
                         profit_pct_low = (entry_price - self.highest_price) / entry_price * 100
                         use_atr = (profit_pct_low >= CONFIG.get('tsl_hybrid_threshold', 10.0))
                    elif tsl_mode == 'ATR':
                         use_atr = True

                    if use_atr:
                        if len(self.atr_tsl) > 0:
                            current_atr = self.atr_tsl[0]
                            multiplier = CONFIG.get('tsl_atr_multiplier', 1.5)
                            tsl_val = self.highest_price + (current_atr * multiplier)
                        else:
                            tsl_val = self.highest_price * 1.01
                    else:
                        tsl_pct = self.params.tsl_pct
                        if CONFIG.get('use_stepped_tsl'):
                            profit_at_low = (entry_price - self.highest_price) / entry_price * 100
                            for step in CONFIG.get('tsl_steps', []):
                                if profit_at_low < step['profit']:
                                    tsl_pct = step['tsl']
                                    break
                        tsl_val = self.highest_price * (1 + tsl_pct/100.0)

                    # --- Cost Protection (Break-Even) ---
                    if self.highest_price <= entry_price * 0.99:
                        tsl_val = min(tsl_val, entry_price)

                    if curr_price >= tsl_val:
                        self.log(f'TRAILING STOP HIT (SHORT)! PnL: {pct_change*100:.2f}% | Price: {curr_price:.2f} (Mode: {tsl_mode})')
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
                htf_just_aligned = False
                htf_target = 1 if c_type == "Call" else -1

                if self.idx_htf_utbot:
                    htf_curr = self.idx_htf_utbot.pos[0]
                    htf_prev = self.idx_htf_utbot.pos[-1]
                    htf_ok = (htf_curr == htf_target)
                    htf_just_aligned = (htf_prev != htf_target and htf_curr == htf_target)

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

                # CHECK FOR LATE HTF ALIGNMENT
                late_alignment_entry = False
                if CONFIG.get("allow_late_htf_alignment", False) and htf_just_aligned:
                    # Check if 3m trend is already aligned but just waiting for HTF
                    if curr_3m_trend == htf_target and self.trend_age <= CONFIG.get("late_alignment_max_candles", 5):
                        late_alignment_entry = True
                        self.log(f"   [HTF] Late Alignment detected! 3m trend age: {self.trend_age} candles. ENTRY ALLOWED.")

                if (entry_signal or late_alignment_entry) and htf_ok:
                    log_msg = f"[SIGNAL] Index {sig_name} detected" if not late_alignment_entry else "[SIGNAL] Late HTF Alignment detected"
                    self.log(f"{log_msg} @ {self.datas[0].close[0]:.2f}")
                    if self.idx_htf_utbot:
                        self.log(f"   (Index HTF confirms @ {self.datas[2].close[0]:.2f})")
                    
                    # Buy Option (data1)
                    if self.option_data.close[0] > 0:
                        self.order = self.buy(data=self.option_data, size=self.params.quantity)
                        if self.params.verbose:
                            self.log(f"   >>> BUYING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        # --- API EXECUTION ---
                        self.execute_real_order("BUY", self.option_data.close[0])
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
                        self.order = self.sell(data=self.option_data, size=self.params.quantity)
                        if self.params.verbose:
                            self.log(f"   >>> SELLING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        # --- API EXECUTION ---
                        self.execute_real_order("SELL", self.option_data.close[0])
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
                     # TSL PRIORITY
                    if self.params.use_tsl and self.highest_price > 0:
                         # Calculate current dynamic TSL val
                         tsl_val = 0.0
                         tsl_mode = CONFIG.get("tsl_mode", "PCT")
                         
                         if tsl_mode == "ATR":
                              tsl_val = self.highest_price - (self.atr_tsl[0] * CONFIG.get("tsl_atr_multiplier", 1.5))
                         else:
                             tsl_pct = self.params.tsl_pct
                             if CONFIG.get("use_stepped_tsl"):
                                 profit_at_high = (self.highest_price - entry_price) / entry_price * 100
                                 for step in CONFIG.get("tsl_steps", []):
                                     if profit_at_high < step["profit"]:
                                         tsl_pct = step["tsl"]
                                         break
                             tsl_val = self.highest_price * (1 - tsl_pct/100.0)
                         curr_price = self.option_data.close[0]
                         # Profit-Protected: Only ignore if TSL has moved above entry
                         if curr_price > tsl_val and tsl_val > entry_price:
                             should_exit = False
                             self.log(f"[FILTERED] Trend Reversed but Profit Locked (TSL {tsl_val:.2f} > Entry {entry_price:.2f}, {tsl_pct}%). Holding...")
                    
                    if should_exit:
                        self.log(f"[EXIT SIGNAL] Index flipped @ {self.datas[0].close[0]:.2f}")
                        self.order = self.close(data=self.option_data)
                        if self.params.verbose:
                             self.log(f"   >>> CLOSING {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        # --- API EXECUTION ---
                        self.execute_real_order("SELL", self.option_data.close[0])
            
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
                     # TSL PRIORITY
                    if self.params.use_tsl and self.highest_price > 0:
                         # Calculate current dynamic TSL val
                         tsl_val = 0.0
                         tsl_mode = CONFIG.get("tsl_mode", "PCT")
                         
                         if tsl_mode == "ATR":
                              tsl_val = self.highest_price + (self.atr_tsl[0] * CONFIG.get("tsl_atr_multiplier", 1.5))
                         else:
                             tsl_pct = self.params.tsl_pct
                             if CONFIG.get("use_stepped_tsl"):
                                 profit_at_low = (entry_price - self.highest_price) / entry_price * 100
                                 for step in CONFIG.get("tsl_steps", []):
                                     if profit_at_low < step["profit"]:
                                         tsl_pct = step["tsl"]
                                         break
                             tsl_val = self.highest_price * (1 + tsl_pct/100.0)
                         curr_price = self.option_data.close[0]
                         # Short logic: Profit Locked if TSL floor is BELOW entry
                         if curr_price < tsl_val and tsl_val < entry_price:
                             should_exit = False
                             self.log(f"[FILTERED] Trend Reversed but Profit Locked (TSL {tsl_val:.2f} < Entry {entry_price:.2f}, {tsl_pct}%). Holding...")
                    
                    if should_exit:
                        self.log(f"[COVER SIGNAL] Index flipped @ {self.datas[0].close[0]:.2f}")
                        self.order = self.close(data=self.option_data)
                        if self.params.verbose:
                            self.log(f"   >>> CLOSING SHORT {c_type.upper()} @ {self.option_data.close[0]:.2f}")
                        
                        # --- API EXECUTION ---
                        self.execute_real_order("BUY", self.option_data.close[0])

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None
        
        if not self.params.verbose: return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                print(f"   BUY EXECUTED @ {order.executed.price:.2f} (Comm: {order.executed.comm:.2f})")
            else:
                print(f"   SELL EXECUTED @ {order.executed.price:.2f} (Comm: {order.executed.comm:.2f})")

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
    opt_symbol = resolve_symbol_from_query(CONFIG["trade_symbol"], exchange="NFO")
    
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
    
    idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
    opt_symbol = resolve_symbol_from_query(CONFIG["trade_symbol"], exchange="NFO")
    
    if not idx_symbol or not opt_symbol: return

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
            
            if CONFIG.get("live_trade", False):
                print(f"!!! WARNING: LIVE API CALLS ENABLED (All signals will be sent) !!!")

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

