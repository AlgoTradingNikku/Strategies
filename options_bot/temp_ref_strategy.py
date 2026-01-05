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
    
    # Option Contract
    "option_query": "NIFTY 06Jan26 26350 PE",  # Specific option to trade
    "contract_type": "Put",                     # "Call" or "Put"
    
    # Dynamic Strike Selection (Advanced)
    "auto_strike_selection": False,   # Auto-select ITM strikes (requires signal_source="INDEX")
    "strike_offset": 100,              # Points ITM for auto-selection
    "expiry_base": "NIFTY 13Jan26",   # Expiry prefix for auto-selection
    
    # ========================================
    # 2. STRATEGY PARAMETERS
    # ========================================
    # UTBot Settings
    "sensitivity": 1.0,                # UTBot sensitivity (Key Value)
    "atr_period": 10,                  # ATR period for UTBot
    "use_heikin_ashi": True,           # Use Heikin Ashi candles for signals
    
    # Higher Timeframe Filter (Optional)
    "use_htf_filter": True,            # Enable HTF trend filter
    "htf_timeframe": "15m",            # HTF timeframe
    "htf_sensitivity": 1.0,            # HTF UTBot sensitivity
    "htf_atr_period": 10,              # HTF ATR period
    
    # ========================================
    # 3. TRADE EXECUTION
    # ========================================
    "timeframe": "3m",                 # Primary chart timeframe
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
    # Trend Continuation (Morning Entry)
    "allow_opening_continuation": False,        # Enable continuation entry at market open
    "continuation_time_limit": "09:30",        # Time limit for continuation (HH:MM format)
    "continuation_max_attempts": 1,            # Max continuation entries per day
    "max_opening_gap_pct": 2.0,                # Max gap % for safe continuation entry
    "continuation_check_gap_direction": True,  # Verify gap direction matches contract type
    "continuation_min_profit_pct": 0.0,        # Min profit % from yesterday (0 = disabled)
    
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
        "sensitivity": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
        "atr_period": [10, 15, 20, 25, 30],
        "htf_sensitivity": [1.0, 2.0],
        "htf_atr_period": [10, 20]
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
    "api_key": "a2edab0147e5058617b63b677c82c5c44533d356d8b8f33734127d6c5f029a55",
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
            expiry = "" # e.g. 30DEC25
            strike = "" # e.g. 26000
            opt_type = "" # CE/PE
            for p in query_parts:
                if p in ["CE", "PE"]: opt_type = p
                elif any(m in p for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
                    expiry = p
                elif p.isdigit() and len(p) >= 3:
                    strike = p

            best_match = None
            for cand in candidates:
                sym = (cand.get('symbol') or cand.get('trading_symbol', '')).upper()
                if not sym: continue
                if not sym.startswith(root_asset): continue
                
                # Structural Check: Strip Asset and Expiry to isolate the strike
                rem = sym.replace(root_asset, "")
                if expiry: rem = rem.replace(expiry, "")
                
                if strike and opt_type:
                    target_rem = f"{strike}{opt_type}"
                    if rem == target_rem:
                        best_match = sym
                        break
            
            if best_match:
                print(f"Resolved: {best_match}")
                return best_match
            print(f"Could not match symbol with all parts: {query_parts}")
            print(f"Candidates seen: {[c.get('symbol') or c.get('trading_symbol') for c in candidates]}")
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
    tf = interval if interval else CONFIG["timeframe"]
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
             tr2 = bt.indicators.Abs(self.data.ha_high - self.data.ha_close(-1))
             tr3 = bt.indicators.Abs(self.data.ha_low - self.data.ha_close(-1))
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
        ("signal_source", CONFIG.get("signal_source", "INDEX")),
        ("quantity", CONFIG["quantity"]),
        ("sensitivity", CONFIG["sensitivity"]),
        ("atr_period", CONFIG["atr_period"]),
        ("use_heikin_ashi", CONFIG["use_heikin_ashi"]),
        ("use_sl", CONFIG["use_sl"]),
        ("sl_pct", CONFIG["sl_pct"]),
        ("use_tp", CONFIG["use_tp"]),
        ("tp_pct", CONFIG["tp_pct"]),
        ("use_tsl", CONFIG.get("use_tsl", False)),
        ("tsl_pct", CONFIG.get("tsl_pct", 5.0)),
        ("htf_sensitivity", CONFIG.get("htf_sensitivity", 1.0)),
        ("htf_atr_period", CONFIG.get("htf_atr_period", 10)),
        ("use_htf_filter", CONFIG.get("use_htf_filter", False)),
        ("live_trade", CONFIG.get("live_trade", False)),
        ("execute_backtest_orders", CONFIG.get("execute_backtest_orders", False)),
        ("verbose", True),
    )

    def log(self, txt, dt=None):
        if self.params.verbose:
            dt = dt or self.datas[0].datetime.datetime(0)
            print(f"{dt.strftime('%H:%M')} {txt}")

    def __init__(self):
        # Determine Signal Source
        if self.params.signal_source == "OPTION":
            signal_data = self.datas[1]
        else:
            signal_data = self.datas[0]

        # UTBot applied to SELECT SOURCE
        self.utbot = UTBotIndicator(
            signal_data,
            sensitivity=self.params.sensitivity,
            atr_period=self.params.atr_period,
            use_ha=self.params.use_heikin_ashi
        )
        
        # HTF Indicator (Optional)
        self.htf_utbot = None
        if len(self.datas) > 2:
             self.htf_utbot = UTBotIndicator(
                self.datas[2],
                sensitivity=self.params.htf_sensitivity,
                atr_period=self.params.htf_atr_period,
                use_ha=self.params.use_heikin_ashi
            )
        
        self.order = None
        self.continuation_attempts = 0 # Track daily continuation entries
        self.last_cont_date = None
        
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
            symbol = resolve_symbol_from_query(CONFIG["option_query"], exchange="NFO")
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

        mode = CONFIG.get("trading_mode", "long").lower()
        
        # Update Trend Age
        curr_3m_trend = self.utbot.pos[0]
        prev_3m_trend = self.utbot.pos[-1]
        if curr_3m_trend == prev_3m_trend:
            self.trend_age += 1
        else:
            self.trend_age = 1
        
        # Get Position on the OPTION data
        position = self.getposition(self.option_data)
        
        # ---------------------------
        # RISK MANAGEMENT (On Option Price)
        # ---------------------------
        if self.order: return 

        if not self.option_data: return

        # IMPORTANT: Ensure option data has verified data for this timestamp
        # Backtrader syncs datas, so if Option has no volume/ticks for this time, it might be stale or missing
        # We process matching timestamps.
        
        mode = CONFIG.get("trading_mode", "long").lower()
        
        # Get Position on the OPTION data
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
        c_type = CONFIG.get("contract_type", "Call").capitalize()
        
        # --- TREND CONTINUATION LOGIC (9:15 AM Entry) ---
        # Reset Counter Daily
        if self.last_cont_date != self.datas[0].datetime.date(0):
            self.continuation_attempts = 0
            self.last_cont_date = self.datas[0].datetime.date(0)

        # Runs only if we are flat, enabled, within time guard, AND under attempt limit
        if position.size == 0 and CONFIG.get('allow_opening_continuation', False) and self.continuation_attempts < CONFIG.get('continuation_max_attempts', 1):
            current_time_str = self.datas[0].datetime.time(0).strftime('%H:%M')
            time_limit = CONFIG.get('continuation_time_limit', '09:30')
            
            # Only run if we haven't already entered and time is valid
            if current_time_str <= time_limit:
                 prev_trend = self.utbot.pos[-1]
                 
                 # Match Trend
                 trend_matches = False
                 if c_type == 'Call' and prev_trend == 1: trend_matches = True
                 elif c_type == 'Put' and prev_trend == -1: trend_matches = True
                 
                 if trend_matches:
                     # GAP SAFETY
                     idx_open = self.datas[0].open[0]
                     idx_prev_close = self.datas[0].close[-1]
                     gap = (idx_open - idx_prev_close) / idx_prev_close * 100.0
                     gap_pct = abs(gap)
                     
                     # NEW: Gap Direction Check
                     gap_direction_ok = True
                     if CONFIG.get('continuation_check_gap_direction', True):
                         if c_type == 'Call' and gap < 0:
                             gap_direction_ok = False
                             self.log(f'   [SKIP] Continuation skipped: Gap is DOWN ({gap:.2f}%) but trading CALL')
                         elif c_type == 'Put' and gap > 0:
                             gap_direction_ok = False
                             self.log(f'   [SKIP] Continuation skipped: Gap is UP ({gap:.2f}%) but trading PUT')
                     
                     # NEW: Minimum Profit Filter
                     profit_ok = True
                     min_profit = CONFIG.get('continuation_min_profit_pct', 0.0)
                     if min_profit > 0:
                         idx_prev_open = self.datas[0].open[-1]
                         yesterday_profit = (idx_prev_close - idx_prev_open) / idx_prev_open * 100.0
                         if yesterday_profit < min_profit:
                             profit_ok = False
                             self.log(f'   [SKIP] Continuation skipped: Yesterday profit ({yesterday_profit:.2f}%) < Min ({min_profit:.2f}%)')
                     
                     max_gap = CONFIG.get('max_opening_gap_pct', 2.0)
                     if gap_pct <= max_gap and gap_direction_ok and profit_ok:
                         self.log(f'[CONTINUATION] Valid Trend ({prev_trend}) & Gap ({gap:.2f}%). FORCE ENTRY!')
                         
                         if self.option_data.close[0] > 0:
                             if mode == 'long':
                                 self.order = self.buy(data=self.option_data, size=self.params.quantity)
                                 msg = 'BUY'
                             else:
                                 self.order = self.sell(data=self.option_data, size=self.params.quantity)
                                 msg = 'SELL'
                                 
                             if self.params.verbose: 
                                 self.log(f'   >>> CONTINUATION {msg} {c_type.upper()} @ {self.option_data.close[0]:.2f}')
                             current_price = self.option_data.close[0]
                             self.execute_real_order(msg, current_price)
                             if msg == 'BUY': self.highest_price = current_price 
                             elif msg == 'SELL': self.highest_price = current_price 
                             
                             # INCREMENT ATTEMPTS
                             self.continuation_attempts += 1
                             self.log(f'   [INFO] Continuation Attempts: {self.continuation_attempts}/{CONFIG.get("continuation_max_attempts", 1)}')
                             
                             return # Stop processing this bar
                     elif gap_pct > max_gap:
                         self.log(f'   [SKIP] Continuation skipped: Gap ({gap_pct:.2f}%) > Max ({max_gap:.2f}%)')
        
        
        
        if position.size == 0:
            # Check Index Signals (data0)
            if mode == "long":
                # Check HTF Filter if enabled
                htf_ok = True
                htf_just_aligned = False
                htf_target = 1 if c_type == "Call" else -1

                if self.htf_utbot and self.params.use_htf_filter:
                    htf_curr = self.htf_utbot.pos[0]
                    htf_prev = self.htf_utbot.pos[-1]
                    htf_ok = (htf_curr == htf_target)
                    htf_just_aligned = (htf_prev != htf_target and htf_curr == htf_target)

                # Determine if we have the entry signal we want
                is_option_src = (self.params.signal_source == "OPTION")
                
                if is_option_src:
                     # Direct Signal from Option Chart: Buy Signal means Price going up -> Buy Option
                     entry_signal = self.utbot.buy_signal[0]
                     sig_name = "Bullish"
                else:
                    # Indirect Signal from Index: 
                    # Call: Entry on Buy signal (Bullish Index)
                    # Put:  Entry on Sell signal (Bearish Index)
                    entry_signal = self.utbot.buy_signal[0] if c_type == "Call" else self.utbot.sell_signal[0]
                    sig_name = "Bullish" if c_type == "Call" else "Bearish"

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
                    if self.htf_utbot and CONFIG.get("use_htf_filter", False):
                        self.log(f"   (HTF Filter confirmed {sig_name.upper()} @ {self.datas[2].close[0]:.2f})")
                    
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
    print("STARTING BACKTEST")
    print("-" * 50)
    print(f"Sig Source:    {CONFIG.get('signal_source', 'INDEX')}")
    print("="*50)
    
    # 1. Resolve Symbols
    is_option_src = (CONFIG.get("signal_source") == "OPTION")
    
    idx_symbol = None
    if not is_option_src:
        idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
        
    opt_symbol = resolve_symbol_from_query(CONFIG["option_query"], exchange="NFO")
    
    if (not is_option_src and not idx_symbol) or not opt_symbol:
        print("[FAIL] Could not resolve required symbols.")
        return

    # 2. Fetch Data
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    print(f"\n[INFO] Data Range: {start} to {end}")
    
    is_option_src = (CONFIG.get("signal_source") == "OPTION")
    
    df_index = pd.DataFrame()
    if not is_option_src:
        df_index = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end)
        
    df_option = fetch_history(opt_symbol, "NFO", start, end)
    
    df_htf = pd.DataFrame()
    if CONFIG.get("use_htf_filter", False):
        if is_option_src:
             df_htf = fetch_history(opt_symbol, "NFO", start, end, interval=CONFIG["htf_timeframe"])
        else:
             df_htf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=CONFIG["htf_timeframe"])
    
    if df_option.empty:
        print("[FAIL] Missing Option Data.")
        return
        
    if not is_option_src and df_index.empty:
        print("[FAIL] Missing Index Data.")
        return

    # 3. Align Data (Intersection)
    # If using INDEX signals, we strictly need alignment.
    # If using OPTION signals, we might strictly only need Option data, but we keep Index for reference.
    # However, if Index data is short (e.g. 24 rows), intersecting will kill Option data (400 rows).
    # So we SKIP intersection if signal_source is OPTION.
    
    if not is_option_src:
         common_idx = df_index.index.intersection(df_option.index)
         if not df_htf.empty:
              common_idx = common_idx.intersection(df_htf.index)
              
         if common_idx.empty:
             print("[FAIL] No matching timestamps for alignment.")
             return
             
         df_index = df_index.loc[common_idx].sort_index()
         df_option = df_option.loc[common_idx].sort_index()
         if not df_htf.empty:
              df_htf = df_htf.loc[common_idx].sort_index() # Re-align HTF if possible or keep as is
         print(f"[INFO] Aligned Data Points: {len(df_index)} candles")
    else:
         print(f"[INFO] Using Independent Option Data: {len(df_option)} candles")
         # We still need df_index for datas[0], ensuring it's not empty or crashing
         # If df_index is empty, it will be handled by the strategy's data access.

    # 4. Setup Cerebro
    cerebro = bt.Cerebro()
    
    # Add Strategy
    cerebro.addstrategy(PureOptionsStrategy)
    
    # Add Data Feeds
    # If Signal Source is OPTION, we use df_option as data0 (Primary/Signal) AND data1 (Execution)
    # This ensures the timeline is driven by the Option's full history
    if is_option_src:
         print("[INFO] Setting Primary Feed (data0) to Option Data")
         index_data = PandasDataPlusHA(dataname=df_option, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
         cerebro.adddata(index_data, name="OPTION_SIGNAL")
    else:
         index_data = PandasDataPlusHA(dataname=df_index, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
         cerebro.adddata(index_data, name="INDEX_SIGNAL")
         
    # Option = Data 1 (always df_option for execution)
    option_data = PandasDataPlusHA(dataname=df_option, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
    cerebro.adddata(option_data, name="OPTION_EXECUTION")
    
    if not df_htf.empty:
        data_htf = PandasDataPlusHA(dataname=df_htf, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
        cerebro.adddata(data_htf, name="HTF")
    
    cerebro.broker.setcash(CONFIG["capital"])
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="TA")
    
    print(f"\n[INFO] Starting Capital: {CONFIG['capital']}")
    results = cerebro.run()
    strat = results[0]
    ta = strat.analyzers.TA.get_analysis()
    
    final_val = cerebro.broker.getvalue()
    pnl = final_val - CONFIG["capital"]
    
    # Stats
    total_closed = ta.get("total", {}).get("closed", 0)
    total_open = ta.get("total", {}).get("open", 0)
    
    print("\n" + "="*50)
    print("BACKTEST RESULTS")
    print("="*50)
    print("="*50)
    print(f"Sig Source:    {CONFIG.get('signal_source', 'INDEX')}")
    print(f"Signals:       {idx_symbol if CONFIG.get('signal_source') == 'INDEX' else opt_symbol}")
    print(f"Traded:        {opt_symbol} (Option)")
    print(f"Final Value:   {final_val:.2f}")
    print(f"Net P&L:       {pnl:+.2f}")
    print(f"Return:        {(pnl/CONFIG['capital'])*100:.2f}%")
    print(f"Trades:        {total_closed} Closed | {total_open} Open")
    
    if total_closed > 0:
        won_pnl = ta.get("won", {}).get("pnl", {}).get("total", 0)
        lost_pnl = ta.get("lost", {}).get("pnl", {}).get("total", 0)
        print(f"   Won PnL:   +{won_pnl:.2f}")
        print(f"   Lost PnL:  {lost_pnl:.2f}")

    print("="*50 + "\n")

def run_optimization():
    print("\n" + "="*50)
    print("STARTING OPTIMIZATION (Index-Based)")
    print("="*50)
    
    is_option_src = (CONFIG.get("signal_source") == "OPTION")
    
    idx_symbol = None
    if not is_option_src:
        idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
        
    opt_symbol = resolve_symbol_from_query(CONFIG["option_query"], exchange="NFO")
    
    if (not is_option_src and not idx_symbol) or not opt_symbol: return

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    
    is_option_src = (CONFIG.get("signal_source") == "OPTION")
    
    df_index = pd.DataFrame()
    if not is_option_src:
        df_index = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end)
        
    df_option = fetch_history(opt_symbol, "NFO", start, end)
    
    df_htf = pd.DataFrame()
    if CONFIG.get("use_htf_filter", False):
        if is_option_src:
             df_htf = fetch_history(opt_symbol, "NFO", start, end, interval=CONFIG["htf_timeframe"])
        else:
             df_htf = fetch_history(idx_symbol, CONFIG["index_exchange"], start, end, interval=CONFIG["htf_timeframe"])

    if df_option.empty:
        print("[FAIL] Missing Option Data.")
        return
        
    if not is_option_src and df_index.empty:
        print("[FAIL] Missing Index Data.")
        return
    
    if not is_option_src:
        common_idx = df_index.index.intersection(df_option.index)
        if not df_htf.empty:
             common_idx = common_idx.intersection(df_htf.index)
             
        if common_idx.empty:
            print("[FAIL] No matching timestamps for alignment.")
            return
            
        df_index = df_index.loc[common_idx].sort_index()
        df_option = df_option.loc[common_idx].sort_index()
        if not df_htf.empty:
             df_htf = df_htf.loc[common_idx].sort_index() # Re-align HTF if possible or keep as is

    cerebro = bt.Cerebro()
    
    htf_sens_list = [1.0, 2.0] if CONFIG.get("use_htf_filter", False) else [CONFIG.get("htf_sensitivity", 1.0)]
    htf_atr_list = [10, 20] if CONFIG.get("use_htf_filter", False) else [CONFIG.get("htf_atr_period", 10)]

    cerebro.optstrategy(
        PureOptionsStrategy,
        sensitivity=CONFIG["OPT_MAP"]["sensitivity"],
        atr_period=CONFIG["OPT_MAP"]["atr_period"],
        htf_sensitivity=CONFIG["OPT_MAP"]["htf_sensitivity"] if CONFIG.get("use_htf_filter") else [CONFIG.get("htf_sensitivity", 1.0)],
        htf_atr_period=CONFIG["OPT_MAP"]["htf_atr_period"] if CONFIG.get("use_htf_filter") else [CONFIG.get("htf_atr_period", 10)],
        use_htf_filter=[CONFIG.get("use_htf_filter", False)],

        use_heikin_ashi=[CONFIG.get("use_heikin_ashi", False)],
        use_sl=[CONFIG.get("use_sl", False)],
        sl_pct=[CONFIG.get("sl_pct", 10.0)],
        use_tp=[CONFIG.get("use_tp", False)],
        tp_pct=[CONFIG.get("tp_pct", 20.0)],
        live_trade=[CONFIG.get("live_trade", False)], 
        execute_backtest_orders=[False], # ALWAYS DISABLE FOR OPTIMIZATION
        verbose=[False]
    )
    # Add Data Feeds
    if is_option_src:
         index_data = PandasDataPlusHA(dataname=df_option, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
         cerebro.adddata(index_data, name="OPTION_SIGNAL")
    else:
         index_data = PandasDataPlusHA(dataname=df_index, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
         cerebro.adddata(index_data, name="INDEX_SIGNAL")
    
    option_data = PandasDataPlusHA(dataname=df_option, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
    cerebro.adddata(option_data, name="OPTION_EXECUTION")
    
    if not df_htf.empty:
        data_htf = PandasDataPlusHA(dataname=df_htf, ha_open='HA_Open', ha_high='HA_High', ha_low='HA_Low', ha_close='HA_Close')
        cerebro.adddata(data_htf, name="HTF")

    
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
                "sens": strat.params.sensitivity,
                "atr": strat.params.atr_period,
                "h_sens": strat.params.htf_sensitivity,
                "h_atr": strat.params.htf_atr_period,
                "pnl": pnl,
                "closed": ta.get("total", {}).get("closed", 0),
                "open": ta.get("total", {}).get("open", 0)
            })
            
    sorted_stats = sorted(final_stats, key=lambda x: x["pnl"], reverse=True)
    
    use_htf = CONFIG.get("use_htf_filter", False)
    
    if use_htf:
        print(f"\n{'='*85}")
        print(f"TOP RESULTS (HTF Filter ON)")
        print(f"{'Sens':<6} | {'ATR':<5} | {'H-Sens':<6} | {'H-ATR':<5} | {'PnL':>10} | {'Closed':>6} | {'Open':>6}")
        print("-" * 85)
        for res in sorted_stats[:15]:
            print(f"{res['sens']:<6.1f} | {res['atr']:<5} | {res['h_sens']:<6.1f} | {res['h_atr']:<5} | {res['pnl']:>10.2f} | {res['closed']:>6} | {res['open']:>6}")
        print("="*85 + "\n")
    else:
        print(f"\n{'='*65}")
        print(f"TOP RESULTS (HTF Filter OFF)")
        print(f"{'Sens':<6} | {'ATR':<5} | {'PnL':>10} | {'Closed':>6} | {'Open':>6}")
        print("-" * 65)
        for res in sorted_stats[:15]:
            print(f"{res['sens']:<6.1f} | {res['atr']:<5} | {res['pnl']:>10.2f} | {res['closed']:>6} | {res['open']:>6}")
        print("="*65 + "\n")

if __name__ == "__main__":
    try:
        while True:
            print(f"\n--- Index-Signal Options Bot ---")
            print(f"Index: {CONFIG['index_query']} | Option: {CONFIG['option_query']} ({CONFIG['contract_type']})")
            
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

