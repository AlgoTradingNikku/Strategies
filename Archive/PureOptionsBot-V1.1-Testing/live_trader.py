"""
live_trader.py

Live Execution Runner for Pure Options Bot.
Polls market data (Index for signals, Option for execution), 
calculates indicators, and places real orders.
"""

from PureOptionsStrategy import (
    CONFIG, client, resolve_symbol_from_query, 
    fetch_history, get_contract_type, 
    get_strike_symbol, update_config_globally
)
import backtrader as bt
import pandas as pd
from datetime import datetime, timedelta
import time
import os
import sys
import threading

import csv

class TradeReporter:
    """
    Handles CSV reporting for signals and trades.
    Saves daily reports to 'reports/report_YYYY-MM-DD.csv'
    """
    def __init__(self):
        self.reports_dir = os.path.join(os.path.dirname(__file__), "reports")
        if not os.path.exists(self.reports_dir):
            os.makedirs(self.reports_dir)
            
    def get_report_file(self):
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.reports_dir, f"report_{today}.csv")
        
    def log_event(self, event_type, symbol, price, details=""):
        try:
            filepath = self.get_report_file()
            file_exists = os.path.exists(filepath)
            
            with open(filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Time", "Event Type", "Symbol", "Price", "Details"])
                
                writer.writerow([
                    datetime.now().strftime("%H:%M:%S"),
                    event_type,
                    symbol,
                    f"{price:.2f}",
                    details
                ])
        except Exception as e:
            print(f"[REPORT ERROR] {e}")

class LiveTrader:
    def __init__(self):
        self.idx_symbol = None
        self.trades = {} # key: symbol, value: dict of trade state
        self.last_exit_time = None
        self.last_index_fetch_time = None
        
        # --- THREADING & WEBSOCKET ---
        self.lock = threading.Lock()
        self.is_running = True
        self.reporter = TradeReporter() # NEW: Initialize Reporter
        self.ws_data = {} # key: symbol, value: {'ltp': 0.0, 'time': 0}
        self.master_df = None
        
        # Track historical data for active/observed symbols to avoid re-fetching
        self.history_cache = {} # symbol -> {interval -> df}
        
        # --- PROFESSIONAL CONTROLS ---
        self.daily_pnl = 0.0  # Track cumulative P&L for the day
        self.daily_trades_count = 0
        self.daily_losses_count = 0
        self.last_loss_time = None
        self.session_start_time = None
        self.blocked_until = None  # Timestamp when trading is blocked until
        
        # --- RE-ENTRY PREVENTION (ANTI-WHIPSAW) ---
        self.exit_blacklist = {}  # symbol -> {"exit_time": datetime, "pnl": float, "reason": str}

    def initialize(self):
        print("\n" + "="*60)
        print(f"{'Index Source:':<17} {CONFIG['index_query']} ({CONFIG['index_exchange']})")
        print(f"{'Signal Source:':<17} {CONFIG.get('signal_source', 'INDEX')}")
        
        ss = CONFIG.get("strike_selection", {})
        self.manual_monitor_list = set()
        
        if ss.get("mode") == "MANUAL":
            # Support both array (New) and single string (Legacy)
            ms = ss.get("manual_strikes")
            if not ms:
                # Fallback to legacy
                legacy = CONFIG.get("trade_symbol")
                if legacy: ms = [legacy]
                
            print(f"{'Manual Strikes:':<17} {ms}")
            if ms:
                self.manual_monitor_list = set(ms)
        else:
            print(f"{'Strike Selection:':<17} AUTO (Step: {ss.get('step')}, {ss.get('expiry')})")

        idx = CONFIG.get("index", {})
        opt = CONFIG.get("option", {})
        
        print(f"{'Index LTF:':<17} {idx['ltf']['timeframe']} (Sens: {idx['ltf']['sensitivity']}, ATR: {idx['ltf']['atr']})")
        print(f"{'Index HTF:':<17} {idx['htf']['timeframe']} (Sens: {idx['htf']['sensitivity']}, ATR: {idx['htf']['atr']}) [{'ENABLED' if idx['htf']['enabled'] else 'DISABLED'}]")
        print(f"{'Option LTF:':<17} {opt['ltf']['timeframe']} (Sens: {opt['ltf']['sensitivity']}, ATR: {opt['ltf']['atr']})")
        print(f"{'Option HTF:':<17} {opt['htf']['timeframe']} (Sens: {opt['htf']['sensitivity']}, ATR: {opt['htf']['atr']}) [{'ENABLED' if opt['htf']['enabled'] else 'DISABLED'}]")

        print(f"{'Bot Mode:':<17} {'LIVE TRADE' if CONFIG.get('live_trade') else 'PAPER TRADE'}")
        print(f"{'Lots (Mult):':<17} {CONFIG.get('lots', 1)}")
        print(f"{'Heikin Ashi:':<17} Index: {'ON' if CONFIG.get('index_use_ha') else 'OFF'}, Option: {'ON' if CONFIG.get('option_use_ha') else 'OFF'}")
        
        if CONFIG.get('use_sl'):
            print(f"{'Stop Loss:':<17} ENABLED ({CONFIG.get('sl_pct')}%)")
        if CONFIG.get('use_tp'):
            print(f"{'Take Profit:':<17} ENABLED ({CONFIG.get('tp_pct')}%)")
        
        # TSL Configuration
        tsl_mode = CONFIG.get("tsl_mode", "ATR").upper()
        print(f"{'TSL Mode:':<17} {tsl_mode}")
        if tsl_mode == "ATR":
            print(f"{'ATR Multiplier:':<17} {CONFIG.get('tsl_atr_multiplier', 2.5)}")
        elif tsl_mode == "PERCENT":
            print(f"{'TSL Percent:':<17} {CONFIG.get('tsl_percent', 4.0)}%")
        elif tsl_mode == "POINTS":
            print(f"{'TSL Points:':<17} {CONFIG.get('tsl_points', 8.0)} pts")
        print(f"{'Min Trail Gap:':<17} {CONFIG.get('min_trailing_gap', 5.0)} points")
        
        # Always resolve Index symbol for reference/display
        self.idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
            
        if not self.idx_symbol:
            print("[ERROR] Critical Error: Could not resolve index symbol. Exiting.")
            return False

            
        print(f"\n[INFO] Strategy started.")
        
        cache_file = "instruments_cache.pkl"
        use_cache = False
        
        if os.path.exists(cache_file):
            mtime = datetime.fromtimestamp(os.path.getmtime(cache_file)).date()
            if mtime == datetime.now().date():
                use_cache = True
        
        if use_cache:
            print("[INFO] Loading security master from local cache...")
            try:
                import pickle
                with open(cache_file, "rb") as f:
                    self.master_df = pickle.load(f)
                print(f"[INFO] Master loaded from cache ({len(self.master_df)} instruments).")
            except Exception as e:
                print(f"[WARN] Cache load failed: {e}. Falling back to API.")
                use_cache = False

        if not use_cache:
            print("[INFO] Fetching security master from API (This may take 10-20s)...")
            try:
                self.master_df = client.instruments()
                print(f"[INFO] Master fetched ({len(self.master_df)} instruments).")
                # Save to cache
                import pickle
                with open(cache_file, "wb") as f:
                    pickle.dump(self.master_df, f)
            except Exception as e:
                print(f"[WARN] Failed to fetch instruments master: {e}")

        idx_htf_str = idx['htf']['timeframe'] if idx['htf'].get('enabled', True) else "OFF"
        opt_htf_str = opt['htf']['timeframe'] if opt['htf'].get('enabled', True) else "OFF"
        sig_src = str(CONFIG.get('signal_source', 'INDEX')).capitalize()
        print(f"[INFO] Index [HTF:{idx_htf_str}, LTF:{idx['ltf']['timeframe']}] | Options [HTF:{opt_htf_str}, LTF:{opt['ltf']['timeframe']}, Source: {sig_src} Data]")
        
        # Display Trigger Mode Configuration
        entry_logic = CONFIG.get("entry_logic", {})
        idx_trigger = entry_logic.get("index_trigger_mode", "SIGNAL").upper()
        opt_trigger = entry_logic.get("option_trigger_mode", "SIGNAL").upper()
        idx_max_age = entry_logic.get("index_max_trend_age", 8)
        opt_max_age = entry_logic.get("option_max_trend_age", 5)
        
        # Build trigger mode display string
        trigger_info = f"[INFO] Trigger Modes: Index={idx_trigger}"
        if idx_trigger == "STATE":
            trigger_info += f" (max_age={idx_max_age})"
        trigger_info += f", Option={opt_trigger}"
        if opt_trigger == "STATE":
            trigger_info += f" (max_age={opt_max_age})"
        print(trigger_info)
        
        print("Press Ctrl+C to stop.\n")
        return True


    def calculate_utbot(self, df, sensitivity, atr_period, use_ha):
        """Helper to calculate UTBot trend for a dataframe"""
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
            
            # 1. Fresh Crossover Detection
            if prev_s < prev_trail and s > prev_trail:
                pos[i] = 1
                signals[i] = 1 # Fresh BUY
            elif prev_s > prev_trail and s < prev_trail:
                pos[i] = -1
                signals[i] = -1 # Fresh SELL
            else:
                # 3. Carry forward trend or Initialize (Fix for Stuck Trend Bug)
                if prev_p == 0:
                    pos[i] = 1 if s > prev_trail else -1
                else:
                    pos[i] = prev_p

                # 2. Pullback Detection (Still Bullish / Still Bearish)
                # Logic: Current is Bullish/Bearish State AND (Prev Candle was Opposite Color) AND (Curr Candle is My Color)
                # This catches the 'Still Bullish' (Red-to-Green bounce) and 'Still Bearish' (Green-to-Red pivot)
                curr_open, curr_close = df['Open'].iloc[i], df['Close'].iloc[i]
                prev_open, prev_close = df['Open'].iloc[i-1], df['Close'].iloc[i-1]
                
                if prev_p == 1: # Already Bullish
                    # A pullback is a Red candle followed by a Green candle bounce
                    if prev_close < prev_open and curr_close > curr_open: 
                         signals[i] = 2 # Still Bullish (Pullback Entry)
                elif prev_p == -1: # Already Bearish
                    # A pullback is a Green candle followed by a Red candle reversal
                    if prev_close > prev_open and curr_close < curr_open: 
                         signals[i] = -2 # Still Bearish (Pullback Entry)
        
        # DEBUG: Dump last calculation for inspection
        if CONFIG.get("index_debug", False) and len(df) > 5:
            last_idx = df.index[-1]
            print(f"\n[DEBUG DEBUG] Time: {last_idx}")
            print(f"Close: {df['Close'].iloc[-1]}, HA_Close: {df['HA_Close'].iloc[-1]}")
            print(f"HA_High: {df['HA_High'].iloc[-1]}, HA_Low: {df['HA_Low'].iloc[-1]}")
            print(f"ATR: {atr.iloc[-1]:.4f}, nLoss: {nLoss.iloc[-1]:.4f}")
            print(f"Trail: {trail[-1]:.2f}, Pos: {pos[-1]}")
            print(f"PrevTrail: {trail[-2]:.2f}, PrevPos: {pos[-2]}")
            print("-" * 30)

        return pd.Series(pos, index=df.index), pd.Series(trail, index=df.index), pd.Series(signals, index=df.index)

    def get_trend_age(self, pos_series):
        """Calculates distance from the start of the current trend"""
        if len(pos_series) < 2: return 0
        curr = pos_series.iloc[-2]
        count = 0
        for i in range(2, len(pos_series) + 1):
            if pos_series.iloc[-i] == curr:
                count += 1
            else:
                break
        return count

    def calculate_atr(self, df, period=14):
        """Helper to calculate ATR for current Option data"""
        # Allow override from config if not explicitly passed
        if period == 14: 
            period = int(CONFIG.get("tsl_atr_period", 14))
            
        if df.empty or len(df) < period: return 0.0
        
        high = df['High']
        low = df['Low']
        close = df['Close']
        
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        return atr.iloc[-1]

    def get_live_option_price(self, symbol):
        """High-speed LTP fetch for the option contract with Websocket priority"""
        if not symbol: return 0.0
        
        # 1. Try Websocket Cache First (Zero Latency)
        with self.lock:
            data = self.ws_data.get(symbol, {})
            ws_price = data.get('ltp', 0.0)
            last_upd = data.get('time', 0)
            
        # Use WS price if it's fresh (within last 5 seconds)
        if ws_price > 0 and (time.time() - last_upd) < 5:
            return ws_price
 
        # 2. Fallback to REST API (2s Safety Net)
        # OPTIMIZATION: If Aggressive Momentum is ON, skip this slow call.
        if CONFIG.get("execution", {}).get("aggressive_momentum_entry", False):
            return 0.0
            
        try:
            res = client.get_ltp(symbol, "NFO")
            if isinstance(res, dict):
                if 'ltp' in res and res['ltp']:
                    return float(res['ltp'])
        except Exception:
            pass
        return 0.0

    # ========================================
    # PROFESSIONAL TRADING CONTROLS
    # ========================================
    
    def is_expiry_day(self, symbol):
        """Check if the given option symbol expires today"""
        try:
            # Extract expiry date from symbol (format: NIFTY13JAN26...)
            import re
            match = re.search(r'(\d{2}[A-Z]{3}\d{2})', symbol)
            if not match:
                return False
            
            expiry_str = match.group(1)
            expiry_date = datetime.strptime(expiry_str, "%d%b%y").date()
            today = datetime.now().date()
            
            return expiry_date == today
        except:
            return False
    
    def get_days_to_expiry(self, symbol):
        """Calculate days remaining until expiry"""
        try:
            import re
            match = re.search(r'(\d{2}[A-Z]{3}\d{2})', symbol)
            if not match:
                return 999  # Unknown, allow trade
            
            expiry_str = match.group(1)
            expiry_date = datetime.strptime(expiry_str, "%d%b%y").date()
            today = datetime.now().date()
            
            return (expiry_date - today).days
        except:
            return 999

    def is_within_trading_hours(self):
        """Check if current time is within allowed trading window"""
        th = CONFIG.get("trading_hours", {})
        if not th.get("enabled", False):
            return True, "Time checks disabled"
        
        now = datetime.now()
        current_time = now.time()
        
        # Parse time strings
        start = datetime.strptime(th.get("start_time", "09:30"), "%H:%M").time()
        end = datetime.strptime(th.get("end_time", "15:00"), "%H:%M").time()
        
        if current_time < start:
            return False, f"Before trading hours (starts at {th.get('start_time')})"
        
        if current_time > end:
            return False, f"After trading hours (ends at {th.get('end_time')})"
        
        # Check lunch break
        if th.get("avoid_lunch", False):
            lunch_start = datetime.strptime(th.get("lunch_start", "12:30"), "%H:%M").time()
            lunch_end = datetime.strptime(th.get("lunch_end", "13:30"), "%H:%M").time()
            
            if lunch_start <= current_time <= lunch_end:
                return False, "Lunch break (avoid illiquid period)"
        
        return True, "OK"

    def check_daily_limits(self):
        """Verify if daily loss limits have been breached"""
        rc = CONFIG.get("risk_controls", {})
        if not rc.get("enabled", False):
            return True, "Daily limits disabled"
        
        # Check if blocked due to cool-down
        if self.blocked_until and datetime.now() < self.blocked_until:
            remaining = (self.blocked_until - datetime.now()).seconds
            return False, f"Cool-down active ({remaining}s remaining)"
        
        # Check daily loss limit
        max_loss = rc.get("max_daily_loss", 999999)
        if self.daily_pnl < -max_loss:
            return False, f"Daily loss limit hit (₹{abs(self.daily_pnl):.2f} / ₹{max_loss})"
        
        return True, "Within limits"

    def check_liquidity(self, symbol, is_final_entry=True):
        """Verify option has sufficient liquidity for safe entry/exit"""
        exec_config = CONFIG.get("execution", {})
        min_oi = exec_config.get("min_oi", 0)
        max_spread = exec_config.get("max_spread_pct", 100.0)
        
        try:
            # AGGRESSIVE MOMENTUM OPTIMIZATION (Zero Latency)
            # If enabled, we skip the API quote fetch entirely to save ~300ms
            if exec_config.get("aggressive_momentum_entry", False) and is_final_entry:
                 return True, "OK (Aggressive: Skipped quote fetch for speed)"

            # Fetch quotes (Only if Aggressive Mode is OFF)
            quote = client.get_quotes(symbol, "NFO")
            
            # Check Open Interest
            oi = quote.get('oi', 0) if isinstance(quote, dict) else 0
            if oi < min_oi:
                return False, f"Low OI ({oi} < {min_oi})"
            
            # Check Bid-Ask Spread
            bid = float(quote.get('bid', 0)) if isinstance(quote, dict) else 0
            ask = float(quote.get('ask', 0)) if isinstance(quote, dict) else 0
            
            if ask <= 0 or bid <= 0:
                if is_final_entry:
                    # AGGRESSIVE MOMENTUM FIX: If the user wants to trade based on chart price even without quotes
                    if exec_config.get("aggressive_momentum_entry", True):
                        return True, "OK (Aggressive: No quotes, using Chart price)"
                    return False, "No quotes available"
                else:
                    # Allow observation even if quotes are temporarily missing
                    return True, "WAIT: No quotes (Observing price only)"
            
            spread_pct = ((ask - bid) / ask) * 100
            if spread_pct > max_spread:
                return False, f"Wide spread ({spread_pct:.2f}% > {max_spread}%)"
            
            return True, f"OK (OI:{oi}, Spread:{spread_pct:.2f}%)"
            
        except Exception as e:
            # If quote fetch fails, allow trade (don't block on API issues)
            print(f"   [WARN] Liquidity check failed for {symbol}: {e}")
            return True, "Check skipped (API error)"
    
    def update_daily_pnl(self, pnl, symbol):
        """Update daily P&L tracker and enforce trade limits"""
        self.daily_pnl += pnl
        self.daily_trades_count += 1
        
        if pnl < 0:
            self.daily_losses_count += 1
            self.last_loss_time = datetime.now()
            
            # Apply cool-down after loss
            rc = CONFIG.get("risk_controls", {})
            cool_down = rc.get("cool_down_after_loss_sec", 0)
            if cool_down > 0:
                self.blocked_until = datetime.now() + timedelta(seconds=cool_down)
                print(f"   [CONTROL] Cool-down activated for {cool_down}s after loss")
        
        print(f"   [DAILY P&L] ₹{self.daily_pnl:+.2f} | Trades: {self.daily_trades_count} | Losses: {self.daily_losses_count}")

    def get_greeks(self, symbol):
        """Fetch Option Greeks from OpenAlgo API"""
        # OPTIMIZATION: Greeks API call disabled for speed as requested by user.
        return {'delta': 0, 'gamma': 0, 'theta': 0, 'vega': 0, 'iv': 0}
        
        # Original Logic Removed for Optimization
        return None
    
    def format_greeks_warning(self, symbol, greeks, dte):
        """Generate intelligent warnings based on Greeks values"""
        warnings = []
        
        if greeks is None:
            return []
        
        delta = greeks['delta']
        gamma = greeks['gamma']
        theta = greeks['theta']
        vega = greeks['vega']
        
        # Delta warnings
        if delta < 0.25:
            warnings.append(f"⚠️ Low Delta ({delta:.2f}) - Option barely responding")
        elif delta < 0.15:
            warnings.append(f"🚨 Dead Strike (Δ={delta:.2f}) - Consider exit")
        
        # Gamma warnings (especially on expiry)
        if dte == 0 and gamma > 0.001:
            warnings.append(f"⚡ Expiry Gamma Risk - High volatility expected")
        
        # Theta decay warnings
        if theta < -10:
            warnings.append(f"📉 High theta decay ({theta:.1f}/day) - Time working against you")
        
        # Vega (IV) warnings
        if vega > 20:
            warnings.append(f"💨 High Vega ({vega:.1f}) - Watch for IV crush risk")
        
        return warnings

    def _try_set_exiting(self, symbol):
        """
        Thread-safe atomic check-and-set for EXITING state.
        Returns True if successfully set to EXITING (caller should execute trade).
        Returns False if already EXITING or symbol not found (caller should abort).
        """
        with self.lock:
            if symbol not in self.trades:
                return False
            if self.trades[symbol].get("state") == "EXITING":
                return False
            self.trades[symbol]["state"] = "EXITING"
            return True

    def manage_risk(self, symbol, curr_price, is_trend_reversed_input=None):
        """
        Handles SL, TP, 3-Stage Profit Guard, and Trend Reversal exits for a specific symbol.
        Returns True if position was closed, False otherwise.
        """
        atr = 0.0 # Initialized for safety
        # Load trade state under lock
        with self.lock:
            if symbol not in self.trades:
                return False
            trade = self.trades[symbol]
        
            # If already exiting or manual exit pending, skip
            if trade.get("state") == "EXITING":
                return False
            
            if trade.get("manual_exit_pending", False):
                # We already alerted user to sell. Waiting for sync to kill it.
                return False

            entry = trade["entry_price"]
            highest = trade["highest_price"]
            trend_reversed = is_trend_reversed_input if is_trend_reversed_input is not None else trade.get("trend_reversed", False)

        if curr_price <= 0:
            return False

        rm_conf = CONFIG.get("risk_management", {})
        re_entry = CONFIG.get("re_entry_protection", {})  # Renamed for clarity
        pnl_pct = (curr_price - entry) / entry * 100
        
        # --- 0. CHECK TIME-BASED EXIT (THETA PROTECTION) ---
        # Prevents theta decay from eating profits on stagnant positions
        if re_entry.get("enabled", False):
            entry_time = trade.get("entry_time")
            if entry_time:
                hold_duration_mins = (datetime.now() - entry_time).seconds / 60
                max_hold = re_entry.get("max_hold_mins", 999999)
                min_profit = re_entry.get("min_profit_to_hold", 5.0)
                
                if hold_duration_mins > max_hold and pnl_pct < min_profit:
                    print(f"\n   [TIME EXIT] {symbol} held {hold_duration_mins:.0f}m without sufficient profit")
                    print(f"   (PnL: {pnl_pct:.2f}% < required {min_profit}% for extended hold)")
                    
                    # RACE CONDITION FIX: Atomic check-and-set
                    if not self._try_set_exiting(symbol):
                        return False

                    self.execute_trade("SELL", curr_price, symbol)
                    return True
        
        # --- 1. UPDATE HIGH WATER MARK ---
        if highest < curr_price:
            with self.lock:
                if symbol in self.trades:
                    self.trades[symbol]["highest_price"] = curr_price
            highest = curr_price

        # --- 3-STAGE PROFIT GUARD LOGIC ---
        tsl_price = 0.0
        stage = "INIT"
        
        # --- TRAILING STOP LOSS LOGIC ---
        # Calculate Base Distance based on Mode
        mode = CONFIG.get("tsl_mode", "ATR").upper()
        dist_pts = 0.0
        # atr already initialized at top of function
        atr_from_trade = trade.get("atr", 0.0)
        if atr_from_trade > 0:
            atr = atr_from_trade

        if mode == "ATR":
            mult = CONFIG.get("tsl_atr_multiplier", 2.5)
            dist_pts = atr * mult
            
            # IV ADJUSTMENT: (Disabled for now - Greek Monitoring OFF)
            # if re_entry.get("enabled", False) and re_entry.get("adapt_to_iv", False):
            #     greeks = self.get_greeks(symbol)
            #     if greeks and greeks['iv'] > 0:
            #         iv = greeks['iv']
            #         base_iv = 0.20
            #         iv_mult = 1.0 + ((iv - base_iv) * 2)
            #         dist_pts *= max(1.0, iv_mult)
        
        elif mode == "PERCENT":
            dist_pts = highest * (CONFIG.get("tsl_percent", 4.0) / 100.0)
        
        elif mode == "POINTS":
            dist_pts = CONFIG.get("tsl_points", 8.0)
        
        # Fallback if distance is zero
        if dist_pts <= 0:
            dist_pts = entry * 0.05
        
        # CRITICAL: Enforce minimum floor distance (Safety Gap)
        min_gap = CONFIG.get("min_trailing_gap", 5.0)
        dist_pts = max(dist_pts, min_gap)

        # C. Preliminary TSL Price
        tsl_price = highest - dist_pts

        # C2. EXPIRY DAY PROTECTION (Professional Safety)
        er = CONFIG.get("expiry_rules", {})
        if self.is_expiry_day(symbol):
            expiry_mult = er.get("increase_tsl_on_expiry", 1.5)
            # Tighten by reducing distance
            tsl_price = highest - (dist_pts / expiry_mult)
            stage = "TRAILING_EXPIRY"
            print(f"   [EXPIRY] {symbol} expires today - TSL tightened {expiry_mult}x")
        else:
            stage = "TRAILING"

        # D. Apply Break-Even Guard (Stage 1)
        be_trigger = rm_conf.get("be_trigger_pct", 2.0)
        if pnl_pct >= be_trigger:
            be_buffer = rm_conf.get("be_buffer_pts", 1.0)
            be_level = entry + be_buffer
            if tsl_price < be_level:
                stage = "BREAK-EVEN"
                tsl_price = be_level

        # E. Execution & Heartbeat
        with self.lock:
            prev_stage = trade.get("last_stage", "INIT")
            if prev_stage != stage:
                print(f"\n   >>> [GUARD] {symbol} Stage Transition: {prev_stage} -> {stage} <<<")
                self.trades[symbol]["last_stage"] = stage


        if time.time() % 10 < 2: # Reduce log verbosity for multi-position
            # Base heartbeat + Diagnostic Info
            self.safe_print(
                f"   [SYNC:{symbol}] LTP: {curr_price:.2f} | "
                f"Entry: {entry:.2f} | "
                f"PnL: {pnl_pct:+.2f}% | "
                f"Stage: {stage} | "
                f"TSL: {tsl_price:.2f} (Gap: {dist_pts:.2f}, ATR: {atr:.2f})"
            )
        
        # FINAL SAFETY: Stop loss cannot be below zero for long options
        if tsl_price < 0:
            tsl_price = 1.0 # Minimal fallback
        
        if curr_price <= tsl_price:
            print(f"\n   !!! [EXIT] {symbol} 3-Stage Guard: {stage} Hit !!!")
            print(f"   (Price: {curr_price:.2f} <= TSL: {tsl_price:.2f})\n")
            
            # RACE CONDITION FIX: Atomic check-and-set
            if not self._try_set_exiting(symbol):
                return False
            
            self.execute_trade("SELL", curr_price, symbol)
            return True
        
        # --- MANAGE EXITS ---
        if trend_reversed:
            print(f"   [SIGNAL] Trend Reversed for {symbol}. Closing Position.")
            
            # RACE CONDITION FIX: Atomic check-and-set
            if not self._try_set_exiting(symbol):
                return False

            self.execute_trade("SELL", curr_price, symbol)
            return True

        return False

    def safe_print(self, msg):
        """Thread-safe print to prevent garbled terminal logs"""
        if hasattr(self, 'print_lock'):
            with self.print_lock:
                print(msg)
        else:
            print(msg)

    def run_cycle(self):
        """Standard Check Cycle - Portfolio Management for Multiple Positions"""
        slow_interval = int(CONFIG.get("slow_check_seconds", 15))
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=CONFIG['lookback_days'])).strftime("%Y-%m-%d")
            
            idx_conf = CONFIG.get("index", {})
            opt_conf = CONFIG.get("option", {})
            max_pos = CONFIG.get("max_positions", 1)
            
            # --- 1. DATA FETCHING: INDEX ---
            df_idx_ltf = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['ltf']['timeframe'], silent=True)
            if df_idx_ltf.empty: return
            
            index_price = df_idx_ltf['Close'].iloc[-1]

            # UTILITY: Get trend and status for heartbeat/scanner
            def get_trend_data(df, stream_conf, use_ha):
                if df.empty or len(df) < 5: return None, None, None
                return self.calculate_utbot(df, stream_conf['sensitivity'], stream_conf['atr'], use_ha)

            def get_status_str(pos):
                if pos is None: return "WAIT"
                val = pos.iloc[-2]
                return "BULLISH" if val == 1 else "BEARISH"

            # Pre-calculate statuses for heartbeat and logic
            pos_idx_ltf, _, sig_idx_ltf = get_trend_data(df_idx_ltf, idx_conf['ltf'], CONFIG.get('index_use_ha', True))
            idx_ltf_status = get_status_str(pos_idx_ltf)
            idx_htf_status = "DISABLED"
            
            # --- HTF CACHING LOGIC ---
            if idx_conf['htf']['enabled']:
                now_ts = time.time()
                # Re-fetch HTF every 3 minutes (180s)
                if now_ts - self.last_htf_fetch > 180 or self.cached_htf_data[0] is None:
                    df_idx_htf_hb = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['htf']['timeframe'], silent=True)
                    pos_idx_htf, _, _ = get_trend_data(df_idx_htf_hb, idx_conf['htf'], CONFIG.get('index_use_ha', True))
                    idx_htf_status = get_status_str(pos_idx_htf)
                    self.cached_htf_data = (pos_idx_htf, idx_htf_status)
                    self.last_htf_fetch = now_ts
                else:
                    pos_idx_htf, idx_htf_status = self.cached_htf_data

            # Rich Heartbeat (every 60s) - Moved to top for visibility
            if time.time() % 60 < slow_interval: # Ensure it prints once per cycle
                now_str = datetime.now().strftime("%H:%M:%S")
                idx_ltf_tf = CONFIG["index"]["ltf"]["timeframe"]
                idx_htf_tf = CONFIG["index"]["htf"]["timeframe"]
                hb_msg = f"[{now_str}] HEARTBEAT | Index: {index_price:.2f} | LTF-{idx_ltf_tf}: {idx_ltf_status} | HTF-{idx_htf_tf}: {idx_htf_status}"
                if len(self.trades) > 0:
                    for sym, trade in self.trades.items():
                         state = trade.get("state", "UNKNOWN")
                         side = trade.get("side", "N/A")
                         bias = "BULLISH" if side == "CALL" else "BEARISH"
                         hb_msg += f"\n   ACTIVE: {sym} | State: {state} | Bias: {bias}"
                self.safe_print(hb_msg)
            
            last_bar_time = df_idx_ltf.index[-1]
            
            # --- SESSION / STALE DATA CHECK ---
            now = datetime.now()
            if last_bar_time.tzinfo is not None: last_bar_time = last_bar_time.replace(tzinfo=None)
            data_age_mins = (now - last_bar_time).total_seconds() / 60
            if data_age_mins > 15 and not CONFIG.get("ignore_session_check", False):
                if len(self.trades) == 0:
                    if now.minute % 5 == 0:
                        print(f"[{now.strftime('%H:%M:%S')}] [IDLE] Scanning {self.idx_symbol} @ {index_price:.2f}... (No positions)")
                    return

            # --- 2. MANAGE EXISTING TRADES ---
            # Use list of keys because trades might be deleted during iteration
            active_symbols = list(self.trades.keys())
            for symbol in active_symbols:
                with self.lock:
                    trade = self.trades.get(symbol)
                if not trade: continue

                # Fetch Option Data
                df_opt_ltf = fetch_history(symbol, "NFO", start, end, interval=opt_conf['ltf']['timeframe'], silent=True)
                if df_opt_ltf.empty:
                    print(f"   [WAIT] {symbol} ... waiting for initial chart data from broker")
                    continue
                
                df_opt_htf = pd.DataFrame()
                if opt_conf['htf']['enabled']:
                    df_opt_htf = fetch_history(symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'], silent=True)

                # --- A. OBSERVING STATE ---
                if trade["state"] == "OBSERVING":
                    pos_opt, _, sig_opt = get_trend_data(df_opt_ltf, opt_conf['ltf'], CONFIG.get('option_use_ha', False))
                    if sig_opt is None: continue
                    
                    # === OPTION TRIGGER MODE ===
                    # Read trigger mode from config
                    entry_logic = CONFIG.get("entry_logic", {})
                    option_trigger_mode = entry_logic.get("option_trigger_mode", "SIGNAL").upper()
                    
                    is_confirm = False
                    confirm_type = ""
                    
                    if option_trigger_mode == "STATE":
                        # STATE-BASED: Check if Option is currently in bullish state
                        # ⚠️ Advisory: SIGNAL mode recommended for better entry prices
                        is_bullish_state = (pos_opt.iloc[-2] == 1)
                        opt_trend_age = self.get_trend_age(pos_opt)
                        max_age = entry_logic.get("option_max_trend_age", 5)  # Option-specific setting
                        
                        if is_bullish_state and opt_trend_age <= max_age:
                            is_confirm = True
                            confirm_type = f"STATE (age:{opt_trend_age})"
                    else:
                        # SIGNAL-BASED (Default/Recommended): Wait for fresh crossover
                        # Entry Confirmation:
                        # 1. Fresh Crossover (sig == 1)
                        # 2. Still Bullish (Pullback) (sig == 2) if enabled
                        if sig_opt.iloc[-2] == 1:
                            is_confirm = True
                            confirm_type = "FRESH"
                        elif sig_opt.iloc[-2] == 2 and entry_logic.get("allow_option_pullback", False):
                            # Ensure we don't buy immediately after a trend change to avoid noise
                            age = self.get_trend_age(pos_opt)
                            if age >= entry_logic.get("pullback_warmup_candles", 3):
                                is_confirm = True
                                confirm_type = "PULLBACK"
                                print(f"   [PULLBACK] Option {trade['side']} Pivot Detect for {symbol}!")
                    
                    htf_ok = True
                    if opt_conf['htf']['enabled'] and not df_opt_htf.empty:
                        pos_opt_htf, _, _ = get_trend_data(df_opt_htf, opt_conf['htf'], CONFIG.get('option_use_ha', False))
                        if pos_opt_htf is not None: htf_ok = (pos_opt_htf.iloc[-2] == 1)

                    if is_confirm and htf_ok:
                        # Final check for spread before buy
                        liq_ok, liq_msg = self.check_liquidity(symbol, is_final_entry=True)
                        if liq_ok:
                            self.safe_print(f"   [CONFIRM] Option {trade['side']} {confirm_type} for {symbol}! Entering.")
                            self.reporter.log_event("ENTRY_CONFIRMED", symbol, df_opt_ltf['Close'].iloc[-1], f"{trade['side']} - {confirm_type}")
                            self.execute_trade("BUY", df_opt_ltf['Close'].iloc[-1], symbol, 
                                              side=trade['side'], expiry_params=trade['expiry_params'], 
                                              idx_at_res=trade['idx_at_res'])
                        else:
                            print(f"   [BLOCKED] Entry delayed: {liq_msg}")
                    else:
                        # CANDLE-BASED TIMEOUT LOGIC
                        # Only increment if a NEW candle has actually appeared
                        curr_candle_time = df_opt_ltf.index[-1]
                        last_candle_time = trade.get("last_obs_time")
                        
                        inc_candle = False
                        if last_candle_time is None or curr_candle_time > last_candle_time:
                            inc_candle = True
                        
                        with self.lock:
                            if inc_candle:
                                self.trades[symbol]["obs_candles"] = trade.get("obs_candles", 0) + 1
                                self.trades[symbol]["last_obs_time"] = curr_candle_time
                        
                        # Timeouts
                        candles = self.trades[symbol]["obs_candles"]
                        timeout = CONFIG.get("option_signal_timeout", 5)
                        if candles >= timeout:
                            print(f"   [TIMEOUT] {symbol} signal delayed. Clearing.")
                            with self.lock: del self.trades[symbol]
                            continue
                        
                        # Drift Guard
                        drift_limit = CONFIG.get("drift_guard_threshold", 25.0)
                        drift = abs(index_price - trade["idx_at_res"])
                        if drift > drift_limit and CONFIG.get("strike_selection", {}).get("mode") == "AUTO":
                            print(f"   [DRIFT] Index moved {drift:.2f} pts. Updating {symbol} Focus...")
                            new_target = get_strike_symbol(index_price, trade["side"], 
                                                        offset=trade["expiry_params"]["step"], 
                                                        expiry_type=trade["expiry_params"]["expiry"],
                                                        expiry_offset=trade["expiry_params"]["offset"])
                            if new_target and new_target != symbol:
                                with self.lock:
                                    # Swap registry entry
                                    old_trade = self.trades.pop(symbol)
                                    old_trade["idx_at_res"] = index_price
                                    self.trades[new_target] = old_trade
                                print(f"   >>> Target Shift: {symbol} -> {new_target}")
                                continue

                # --- B. POSITION STATE ---
                elif trade["state"] == "POSITION":
                    pos_opt, _, _ = get_trend_data(df_opt_ltf, opt_conf['ltf'], CONFIG.get('option_use_ha', False))
                    if pos_opt is None: continue
                    
                    is_exit = (pos_opt.iloc[-2] == -1 and pos_opt.iloc[-3] == 1)
                    
                    # Update ATR in trade dictionary
                    new_atr = self.calculate_atr(df_opt_ltf, period=opt_conf['ltf']['atr'])
                    with self.lock:
                        if symbol in self.trades:
                            self.trades[symbol]["atr"] = new_atr
                            self.trades[symbol]["trend_reversed"] = is_exit
                    
                    self.manage_risk(symbol, df_opt_ltf['Close'].iloc[-1], is_trend_reversed_input=is_exit)

            # --- 3. SCAN FOR NEW OPPORTUNITIES ---
            if len(self.trades) < max_pos:
                # Trends already calculated at top
                
                # === INDEX TRIGGER MODE ===
                entry_logic = CONFIG.get("entry_logic", {})
                index_trigger_mode = entry_logic.get("index_trigger_mode", "SIGNAL").upper()
                
                is_valid_setup = False
                setup_type = ""
                curr_idx = pos_idx_ltf.iloc[-2] if pos_idx_ltf is not None and len(pos_idx_ltf) > 1 else 0
                
                if index_trigger_mode == "STATE":
                    # STATE-BASED: Check if both LTF and HTF are currently aligned
                    # This mode never misses opportunities when both timeframes agree
                    if pos_idx_ltf is not None and len(pos_idx_ltf) > 1:
                        ltf_bullish = (pos_idx_ltf.iloc[-2] == 1)
                        
                        # Check HTF alignment (if enabled)
                        htf_bullish = True  # Default if HTF disabled
                        if idx_conf['htf']['enabled'] and self.cached_htf_data[0] is not None:
                            pos_idx_htf = self.cached_htf_data[0]
                            htf_bullish = (pos_idx_htf.iloc[-2] == 1) if len(pos_idx_htf) > 1 else True
                        
                        # Apply trend age filter (using index-specific setting)
                        max_age = entry_logic.get("index_max_trend_age", 8)
                        trend_age = self.get_trend_age(pos_idx_ltf)
                        
                        # Aligned BULLISH
                        if ltf_bullish and htf_bullish and trend_age <= max_age:
                            is_valid_setup = True
                            setup_type = f"STATE (age:{trend_age})"
                            curr_idx = 1  # BULLISH
                        # Aligned BEARISH
                        elif not ltf_bullish and not htf_bullish and trend_age <= max_age:
                            is_valid_setup = True
                            setup_type = f"STATE (age:{trend_age})"
                            curr_idx = -1  # BEARISH
                else:
                    # SIGNAL-BASED (Default): Wait for fresh UTBot crossover
                    if sig_idx_ltf is not None:
                        curr_sig = sig_idx_ltf.iloc[-2]
                        curr_idx = pos_idx_ltf.iloc[-2]
                        
                        # Signal Decision:
                        # 1. Fresh Signal (curr_sig in [1, -1])
                        # 2. Still State (curr_sig in [2, -2]) if mid-trend pullback is enabled
                        if curr_sig in [1, -1]:
                            is_valid_setup = True
                            setup_type = "FRESH"
                        elif curr_sig in [2, -2] and entry_logic.get("allow_index_pullback", False):
                            # Ensure trend is mature enough
                            age = self.get_trend_age(pos_idx_ltf)
                            if age >= entry_logic.get("pullback_warmup_candles", 3):
                                is_valid_setup = True
                                setup_type = "PULLBACK"

                if is_valid_setup:
                    sig_name = "BUY" if curr_idx == 1 else "SELL"
                    tf_idx = idx_conf['ltf']['timeframe']
                    tf_htf = idx_conf['htf']['timeframe']
                    
                    # Check HTF (for SIGNAL mode, STATE mode already checked above)
                    htf_ok = True
                    status_label = f"{idx_ltf_status} [{setup_type}]"
                    
                    # ANTI-SPAM: Check if we're already stalking a symbol for this side
                    target_side = "CALL" if curr_idx == 1 else "PUT"
                    already_stalking_side = any(
                        t.get("side") == target_side for t in self.trades.values()
                    )
                    
                    # For SIGNAL mode, verify HTF alignment
                    if index_trigger_mode != "STATE" and idx_conf['htf']['enabled']:
                        htf_ok = (idx_htf_status == idx_ltf_status)
                        if not htf_ok:
                            self.safe_print(f"   [TRIGGER] NIFTY LTF-{tf_idx} {status_label} | HTF-{tf_htf} {idx_htf_status} | Mismatch [SKIPPED]")
                        elif not already_stalking_side:
                            self.safe_print(f"   [TRIGGER] Index (NIFTY) {tf_idx} {setup_type} {sig_name} detected ({idx_ltf_status})")
                    elif not already_stalking_side:
                        # STATE mode or HTF disabled - just print the detection
                        self.safe_print(f"   [TRIGGER] Index (NIFTY) {tf_idx} {setup_type} {sig_name} detected ({idx_ltf_status})")
                    
                    # Proceed with strike selection if HTF aligned (or STATE mode)
                    if htf_ok:
                        side = "CALL" if curr_idx == 1 else "PUT"
                        ss = CONFIG.get("strike_selection", {})
                        base_step = ss.get("step", 0)
                        
                        # === INTELLIGENT STRIKE FALLBACK ===
                        # Try primary strike, then fallback to adjacent if blocked by cooldown
                        target = None
                        re_entry = CONFIG.get("re_entry_protection", {})
                        allow_fallback = re_entry.get("allow_adjacent_strikes", True)
                        max_offset = re_entry.get("max_strike_offset", 1)
                        
                        # Build list of strikes to try
                        strike_attempts = []
                        is_manual = (ss.get("mode") == "MANUAL")
                        
                        if is_manual:
                            strike_attempts = [0] # Single pass for Manual
                        else:
                            base_step = ss.get("step", 0)
                            strike_attempts = [base_step]  # Primary (ATM)
                            if allow_fallback:
                                for offset in range(1, max_offset + 1):
                                    strike_attempts.append(base_step + offset)  # OTM
                                    strike_attempts.append(base_step - offset)  # ITM
                        
                        # Try each strike in order until finding one not blocked
                        for attempt_step in strike_attempts:
                            candidate = None
                            
                            if is_manual:
                                # STRICT DIRECTIONAL LOGIC GATE
                                # Side is CALL -> Need CE. Side is PUT -> Need PE.
                                req_suffix = "CE" if side == "CALL" else "PE"
                                ms = ss.get("manual_strikes", [])
                                if not ms: ms = [CONFIG.get("trade_symbol")] # Legacy fallback
                                
                                # PORTFOLIO MODE: Scan ALL symbols for valid matches
                                # Instead of finding one and breaking, we iterate through the list
                                # Note: The outer loop 'strike_attempts' is logically [0] for manual.
                                # We hijack this pass to potentially trade multiple symbols.
                                
                                candidates_to_trade = []
                                for s in ms:
                                    if s and s.endswith(req_suffix):
                                        candidates_to_trade.append(s)
                                        
                                if not candidates_to_trade:
                                    self.safe_print(f"   [SKIP] Index is {side}, but no *{req_suffix} symbol found in manual list.")
                                    break # Stop this cycle
                                
                                # --- SPECIAL MULTI-EXECUTION LOOP FOR MANUAL MODE ---
                                for candidate in candidates_to_trade:
                                    # (Loop body logic reused below for each candidate)
                                    # We need to manually invoke the checking logic here to support multiple trades
                                    # Or better: We append them to a queue?
                                    # Simplest architecture: Treat 'candidates' as the loop.
                                    pass 
                                
                                # Architecture limitation: The outer loop expects 1 candidate per 'attempt_step'.
                                # Hack: In Manual mode, let's treat 'candidates_to_trade' as the 'strike_attempts'.
                                # BUT 'strike_attempts' is integers (offsets).
                                # FIX: We will flatten the logic.
                            else:
                                # AUTO MODE Resolution
                                candidate = get_strike_symbol(index_price, side, offset=attempt_step, 
                                                             expiry_type=ss.get("expiry", "WEEKLY"), 
                                                             expiry_offset=ss.get("offset", 0))
                                candidates_to_trade = [candidate] if candidate else []
                            
                            # --- UNIFIED EXECUTION LOOP ---
                            # Process every candidate identified in this step (1 for Auto, N for Manual)
                            for candidate in candidates_to_trade:
                                if not candidate: continue
                            
                                # Check if this candidate is blocked by RE-ENTRY COOLDOWN
                                is_blocked = False
                                if re_entry.get("enabled", False) and candidate in self.exit_blacklist:
                                    exit_info = self.exit_blacklist[candidate]
                                    time_since_exit = (datetime.now() - exit_info["exit_time"]).seconds / 60
                                    
                                    # DYNAMIC COOLDOWN: Based on exit reason (profit/loss/reversal)
                                    exit_reason = exit_info.get("reason", "UNKNOWN")
                                    if "PROFIT" in exit_reason or exit_info.get("pnl", 0) > 0:
                                        cooldown_mins = re_entry.get("cooldown_after_profit_mins", 5)
                                    elif "LOSS" in exit_reason or exit_info.get("pnl", 0) < 0:
                                        cooldown_mins = re_entry.get("cooldown_after_loss_mins", 30)
                                    else:  # REVERSAL or other
                                        cooldown_mins = re_entry.get("cooldown_after_reversal_mins", 15)
                                    
                                    if time_since_exit < cooldown_mins:
                                        is_blocked = True
                                        self.safe_print(f"   [BLOCKED] {candidate} (exited {time_since_exit:.1f}m ago, {cooldown_mins}m cooldown)")
                                
                                if not is_blocked:
                                    # --- PRICE CAP CHECK ---
                                    # Check if option is too expensive before proceeding
                                    max_price = CONFIG.get("strike_selection", {}).get("max_option_price", 0)
                                    if max_price > 0:
                                        # Quick fetch of LTP to validate price
                                        # Note: We might fetch again later for signals, but this cheap check saves processing
                                        check_ltp = self.get_live_option_price(target)
                                        if check_ltp > max_price:
                                            self.safe_print(f"   [SKIP] {target} Price {check_ltp} > Cap {max_price}")
                                            continue

                                    target = candidate
                                    
                                    # --- INTEGRATED VALIDATION & ENTRY ---
                                    if target and target not in self.trades and len(self.trades) < max_pos:
                                        validation_passed = True
                                        
                                        # 1. Check trading hours
                                        time_ok, time_msg = self.is_within_trading_hours()
                                        if not time_ok:
                                            print(f"   [BLOCKED] {time_msg}")
                                            validation_passed = False
                                        
                                        # 2. Check daily limits
                                        if validation_passed:
                                            limits_ok, limits_msg = self.check_daily_limits()
                                            if not limits_ok:
                                                print(f"   [BLOCKED] {limits_msg}")
                                                validation_passed = False
                                        
                                        # 3. Check DTE
                                        if validation_passed:
                                            er = CONFIG.get("expiry_rules", {})
                                            if er.get("avoid_new_entry_on_expiry", False):
                                                dte = self.get_days_to_expiry(target)
                                                min_dte = er.get("min_dte", 1)
                                                if dte < min_dte:
                                                    print(f"   [BLOCKED] {target} expires in {dte} day(s) (min: {min_dte})")
                                                    validation_passed = False
                                        
                                        # 4. Check liquidity
                                        if validation_passed:
                                            liq_ok, liq_msg = self.check_liquidity(target, is_final_entry=False)
                                            if not liq_ok:
                                                self.safe_print(f"   [BLOCKED] {target} - {liq_msg}")
                                                validation_passed = False
                                        
                                        # 5. Check Max Option Price
                                        if validation_passed:
                                            max_opt_price = CONFIG.get("max_option_price", 0)
                                            if max_opt_price > 0:
                                                opt_ltp = self.get_live_option_price(target)
                                                if opt_ltp > max_opt_price:
                                                    self.safe_print(f"   [BLOCKED] Price {opt_ltp:.2f} > Limit {max_opt_price:.2f} ({target})")
                                                    validation_passed = False
                                        
                                        if validation_passed:
                                            self.safe_print(f"   [SYNC] Setting up {target}. State: OBSERVING (Stalking for surgical entry...)")
                                            with self.lock:
                                                self.trades[target] = {
                                                    "state": "OBSERVING",
                                                    "side": side,
                                                    "obs_candles": 0,
                                                    "idx_at_res": index_price,
                                                    "expiry_params": {"step": ss.get("step", 0), "expiry": ss.get("expiry", "WEEKLY"), "offset": ss.get("offset", 0)},
                                                    "atr": 0.0
                                                }
                                            
                                            # REPORTING: Log Signal Detection
                                            self.reporter.log_event("SIGNAL_DETECTED", target, index_price, f"{side} Setup ({setup_type})")
                                            
                                            self.safe_print("\n" + "="*50)
                                            self.safe_print(f"   [TRIGGER] NEW {side} SETUP DETECTED!")
                                            self.safe_print(f"   Instrument: {target}")
                                            self.safe_print(f"   Index Ref:  {index_price:.2f}")
                                            self.safe_print("="*50 + "\n")
                                
                                # In AUTO mode, stop after first successful resolution
                                if not is_manual and target in self.trades:
                                    break


        except Exception as e:
            self.safe_print(f"[ERROR] Portfolio Cycle Error: {e}")
            import traceback
            self.safe_print(traceback.format_exc())

    def execute_trade(self, action, price, symbol, side=None, expiry_params=None, idx_at_res=None):
        """Execute Buy/Sell order via OpenAlgo with Dynamic Lot Sizing"""
        try:
            # 1. Resolve Dynamic Quantity from Security Master
            lot_size = 0
            if self.master_df is not None and not self.master_df.empty:
                # Find instrument by symbol
                match = self.master_df[self.master_df['symbol'] == symbol]
                if not match.empty:
                    # Defensive check for column name variants
                    col = 'lot_size' if 'lot_size' in match.columns else 'lotsize' if 'lotsize' in match.columns else None
                    if col:
                        lot_size = int(match[col].iloc[0])
            
            # Fallback if master lookup fails
            if lot_size <= 0:
                print(f"   [WARN] Could not resolve lot_size for {symbol}. Falling back to 75...")
                lot_size = 75

            lots = int(CONFIG.get("lots", 1))
            qty = lots * lot_size
            
            if qty <= 0:
                print(f"   [ERROR] Invalid Quantity: {qty}. Check configuration.")
                return


            # ========================================
            # 2. INTELLIGENT ORDER EXECUTION
            # ========================================
            exec_config = CONFIG.get("execution", {})
            order_type = exec_config.get("order_type", "MARKET")
            
            # Determine order price
            limit_price = price  # Default to input price
            
            if order_type == "LIMIT":
                # AGGRESSIVE MOMENTUM OPTIMIZATION: Skip quote fetch for speed if enabled
                if exec_config.get("aggressive_momentum_entry", False):
                    # Use provided 'price' (from signal) directly, no offset applied to keep it simple and fast
                    limit_price = price
                    print(f"   [LIMIT] Aggressive Mode: Using signal price ₹{limit_price:.2f} for Limit {action}")
                else:
                    # Fetch current bid/ask for precise pricing
                    try:
                        quote = client.get_quotes(symbol, "NFO")
                        if isinstance(quote, dict):
                            bid = float(quote.get('bid', 0))
                            ask = float(quote.get('ask', 0))
                            
                            if bid > 0 and ask > 0:
                                offset_pct = exec_config.get("limit_offset_pct", 0.5) / 100.0
                                
                                if action == "BUY":
                                    # Place limit slightly above ask for better fill probability
                                    limit_price = ask * (1 + offset_pct)
                                    print(f"   [LIMIT] Ask: ₹{ask:.2f} → Limit Buy: ₹{limit_price:.2f} (+{offset_pct*100:.1f}%)")
                                else:
                                    # Place limit slightly below bid
                                    limit_price = bid * (1 - offset_pct)
                                    print(f"   [LIMIT] Bid: ₹{bid:.2f} → Limit Sell: ₹{limit_price:.2f} (-{offset_pct*100:.1f}%)")
                            else:
                                print(f"   [WARN] No valid quotes, falling back to MARKET order")
                                order_type = "MARKET"
                    except Exception as e:
                        print(f"   [WARN] Quote fetch failed: {e}, using MARKET order")
                        order_type = "MARKET"

            order_payload = {
                "strategy": CONFIG['strategy_name'],
                "symbol": symbol,
                "action": action, 
                "exchange": "NFO",
                "pricetype": order_type,
                "product": "NRML",
                "quantity": qty,
                "position_size": qty if action == "BUY" else 0
            }
            
            # Add price for LIMIT orders
            if order_type == "LIMIT":
                order_payload["price"] = round(limit_price, 2)
            
            if action == "SELL":
                print(f"\n   >>> [EXITING POSITION] Selling {qty} ({lots} Lots) of {symbol} <<<")
                
            # ========================================
            # 3. ORDER PLACEMENT & POLLING
            # ========================================
            is_success = False
            actual_price = price
            
            # --- AUTO-SELL CHECK ---
            if action == "SELL":
                enable_auto_sell = CONFIG.get("execution", {}).get("enable_bot_auto_sell", True)
                if not enable_auto_sell:
                    # MANUAL MODE: Alert User, Skip API, Wait for Sync to Close
                    msg = f"\n   [ALERT] SELL SIGNAL ({reason}) : Please Exit {symbol} Manually!"
                    print("="*60)
                    print(msg)
                    print("="*60)
                    self.safe_print(msg)
                    
                    # Log to report
                    self.reporter.log_event("MANUAL_EXIT_ALERT", symbol, price, f"Exit Triggered: {reason}")
                    
                    with self.lock:
                        if symbol in self.trades:
                            # Mark as pending manual exit so we don't spam alerts
                            # But keep state as POSITION so sync_positions can track it
                            self.trades[symbol]["manual_exit_pending"] = True
                            
                            # Reset exiting state if it was set temporarily
                            if self.trades[symbol].get("state") == "EXITING":
                                self.trades[symbol]["state"] = "POSITION" 

                    return True # Pretend success

            if CONFIG.get("live_trade", False):
                self.reporter.log_event("ORDER_PLACED", symbol, price, f"{action} {qty} ({order_type})")
                
                print(f"   [LIVE] Executing {order_type} {action} Order for {qty} {symbol}...")
                response = client.placesmartorder(**order_payload)
                print(f"   [API] Order Response: {response}")
                
                # Handle LIMIT order polling
                if order_type == "LIMIT" and isinstance(response, dict):
                    order_id = response.get('orderid') or (response.get('data', {}).get('orderid') if isinstance(response.get('data'), dict) else None)
                    
                    if order_id:
                        timeout = exec_config.get("order_timeout_sec", 5)
                        print(f"   [POLLING] Waiting {timeout}s for LIMIT order fill...")
                        
                        filled = False
                        poll_start = time.time()
                        
                        while (time.time() - poll_start) < timeout:
                            try:
                                status = client.orderbook()
                                if isinstance(status, list):
                                    for order in status:
                                        if order.get('orderid') == order_id:
                                            order_status = order.get('status', '').upper()
                                            if 'COMPLETE' in order_status or 'FILL' in order_status:
                                                actual_price = float(order.get('avgprice', limit_price))
                                                print(f"   [FILLED] Order completed at ₹{actual_price:.2f}")
                                                filled = True
                                                is_success = True
                                                break
                                            elif 'REJECT' in order_status or 'CANCEL' in order_status:
                                                print(f"   [REJECT] Order {order_status}")
                                                break
                                if filled:
                                    break
                                time.sleep(0.5)  # Poll every 500ms
                            except:
                                pass
                        
                        # Timeout - cancel and retry with MARKET
                        if not filled:
                            print(f"   [TIMEOUT] LIMIT order not filled in {timeout}s")
                            try:
                                client.cancelorder(order_id=order_id, strategy=CONFIG['strategy_name'])
                                print(f"   [CANCEL] LIMIT order cancelled, retrying with MARKET...")
                                
                                # Retry with MARKET order
                                order_payload["pricetype"] = "MARKET"
                                if "price" in order_payload:
                                    del order_payload["price"]
                                
                                response = client.placesmartorder(**order_payload)
                                print(f"   [API] MARKET Order Response: {response}")
                            except Exception as e:
                                print(f"   [ERROR] Cancel/Retry failed: {e}")
                
                # Check success for MARKET orders or retried orders
                if not is_success:
                    if isinstance(response, dict):
                        if response.get('status') == 'success':
                            is_success = True
                        elif 'data' in response and isinstance(response['data'], dict):
                            if response['data'].get('status') == 'success':
                                is_success = True
            else:
                print(f"   [PAPER] Simulated {order_type} {action} Order for {qty} ({lots} Lots) {symbol}")
                if order_type == "LIMIT":
                    print(f"   [PAPER] Limit Price: ₹{limit_price:.2f}")
                # Minimal mock response for logic to continue
                response = {"status": "success", "data": {"status": "success"}}
                is_success = True

            # Continue with trade registry update
            if is_success:
                with self.lock:
                    if action == "BUY":
                        self.trades[symbol] = {
                            "state": "POSITION",
                            "position": 1,
                            "entry_price": price,
                            "highest_price": price,
                            "side": side,
                            "idx_at_res": idx_at_res,
                            "expiry_params": expiry_params,
                            "atr": self.current_option_atr if hasattr(self, 'current_option_atr') else 0.0,
                            "trend_reversed": False,
                            "entry_time": datetime.now()  # Track entry time
                        }
                    else:
                        # SELL - Calculate P&L
                        if symbol in self.trades:
                            trade = self.trades[symbol]
                            entry_price = trade.get("entry_price", price)
                            pnl = (price - entry_price) * qty
                            pnl_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                            
                            self.safe_print(f"   [TRADE RESULT] Entry: ₹{entry_price:.2f} | Exit: ₹{price:.2f} | P&L: ₹{pnl:+.2f} ({pnl_pct:+.2f}%)")
                            
                            # REPORTING: Log Trade Result
                            self.reporter.log_event("TRADE_EXIT", symbol, price, f"PnL: {pnl:.2f} ({pnl_pct:.2f}%)")
                            
                            # Update daily P&L tracker
                            self.update_daily_pnl(pnl, symbol)
                            
                            # TRACK EXIT FOR RE-ENTRY PROTECTION
                            # Prevents immediately re-entering this same symbol
                            re_entry = CONFIG.get("re_entry_protection", {})
                            if re_entry.get("enabled", False):
                                # Store exit info for dynamic cooldown calculation
                                exit_reason = "PROFIT_EXIT" if pnl > 0 else "LOSS_EXIT"
                                self.exit_blacklist[symbol] = {
                                    "exit_time": datetime.now(),
                                    "pnl": pnl_pct,
                                    "reason": exit_reason
                                }
                                self.safe_print(f"   [COOLDOWN] {symbol} added to re-entry blacklist (Reason: {exit_reason})")
                            
                            del self.trades[symbol]
                        self.last_exit_time = datetime.now()
                        self.safe_print(f"   [INFO] Position Closed for {symbol}. Cooldown active.")
                
                sys.stdout.flush()
                time.sleep(1)
            else:
                self.safe_print(f"   [CRITICAL] Order REJECTED for {symbol}")

        except Exception as e:
            self.safe_print(f"   [ERROR] Order Failed for {symbol}: {e}")

    def sync_positions(self):
        """
        Reconciles internal trade state with broker's position book.
        Detects if a position was closed externally and removes it from memory to prevent ghost actions.
        """
        # HEADLESS SYNC: Only run if there are active trades to check
        # This optimization prevents API calls when we are flat
        if len(self.trades) == 0:
            return

        # Throttle to max once per 2 seconds (even if called faster)
        now_ts = time.time()
        if hasattr(self, '_last_sync_time') and (now_ts - self._last_sync_time < 2.0):
            return
        self._last_sync_time = now_ts
        
        try:
            positions = client.positionbook()
            if not positions:
                # If truly empty or error, we can't reliably sync (or maybe user has NO positions). 
                # If list is empty, it means closed or no positions.
                # Careful: API might return error or None on failure.
                # If API returns success but empty list, we assume 0 positions.
                pass
            
            # Map Symbol -> Net Quantity
            broker_map = {}
            
            # Handle API variations
            data_list = []
            if isinstance(positions, list):
                data_list = positions
            elif isinstance(positions, dict):
                data_list = positions.get('data', [])
            
            if data_list:
                for pos in data_list:
                    sym = pos.get('symbol') or pos.get('tradingsymbol')
                    netqty = int(pos.get('netqty', 0))
                    if sym:
                        broker_map[sym] = netqty
                        
            # Check internal trades against broker map
            # Use list(keys) to modify dictionary safely during iteration
            active_symbols = list(self.trades.keys())
            
            for sym in active_symbols:
                with self.lock:
                    if sym not in self.trades: continue
                    idx_trade = self.trades[sym] 
                    
                    # Only check if we think we are in a POSITION (not just observing)
                    if idx_trade.get("state") == "POSITION":
                        curr_qty = broker_map.get(sym, 0)
                        
                        expected_side = idx_trade.get("side") # CALL/PUT
                        # If we bought (Long), NetQty should be > 0. If we Sold (Short), NetQty < 0.
                        # Actually logic:
                        # If NetQty is 0, it is CLOSED.
                        
                        if curr_qty == 0:
                            msg = f"DETECTED EXTERNAL CLOSURE for {sym} (NetQty: 0)"
                            self.safe_print(f"\n   [SYNC] {msg}. removing...")
                            
                            # REPORTING: Log External Exit
                            self.reporter.log_event("EXTERNAL_EXIT", sym, 0, "Manual/Broker Closure")

                            # Add to cooldown so we don't immediately re-enter
                            re_entry = CONFIG.get("re_entry_protection", {})
                            if re_entry.get("enabled", False):
                                self.exit_blacklist[sym] = {
                                    "exit_time": datetime.now(),
                                    "pnl": 0.0,
                                    "reason": "EXTERNAL_EXIT"
                                }
                            
                            del self.trades[sym]
                            self.last_exit_time = datetime.now()

        except Exception as e:
            # Don't spam errors
            pass

    # --- THREADED WORKERS ---
    def risk_worker(self):
        """Dedicated thread for high-speed risk monitoring of all active positions"""
        self.last_htf_fetch = 0
        self.cached_htf_data = (None, "WAIT")
        self.print_lock = threading.Lock()
        
        self.safe_print("[INFO] Risk Worker (Bodyguard) started.")
        fast_interval = int(CONFIG.get("fast_check_seconds", 2))
        
        # Ensure fast interval isn't too fast for API
        if fast_interval < 1: fast_interval = 1
        
        while self.is_running:
            try:
                # 1. Sync Positions first (Detect external changes)
                self.sync_positions()
                
                # 2. Iterate through a snapshot of active positions
                with self.lock:
                    pos_symbols = [s for s, t in self.trades.items() if t.get("state") == "POSITION"]
                
                for symbol in pos_symbols:
                    lp = self.get_live_option_price(symbol)
                    if lp > 0:
                        self.manage_risk(symbol, lp)
                
                time.sleep(fast_interval)
            except Exception as e:
                print(f"[ERROR] Risk Worker Error: {e}")
                time.sleep(fast_interval)

    def scanner_worker(self):
        """Dedicated thread for processing chart signals"""
        print("[INFO] Scanner Worker (The Brain) started.")
        slow_interval = int(CONFIG.get("fetch_interval_seconds", 15))
        while self.is_running:
            try:
                self.run_cycle()
                time.sleep(slow_interval)
            except Exception as e:
                print(f"[ERROR] Scanner Worker Error: {e}")
                time.sleep(slow_interval)

    # --- WEBSOCKET LOGIC ---
    def on_ws_data(self, data):
        """Websocket Callback - Updates live price in real-time for any tracked symbol"""
        try:
            if isinstance(data, dict):
                sym = data.get('symbol')
                ltp = data.get('ltp')
                if sym and ltp:
                    with self.lock:
                        self.ws_data[sym] = {'ltp': float(ltp), 'time': time.time()}
        except Exception:
            pass

    def websocket_worker(self):
        """Manages Websocket connection and multiple subscriptions for portfolio"""
        if not CONFIG.get("use_websocket", True):
            return

        ws_url = CONFIG.get("ws_url", "ws://127.0.0.1:8765")
        current_subscriptions = set()
        
        try:
            client.ws_url = ws_url
            
            # Try to connect
            try:
                client.connect()
                
                # Wait for connection to establish
                time.sleep(2)
                
                # Robust verification
                if hasattr(client, 'ws') and client.ws and hasattr(client.ws, 'sock') and client.ws.sock:
                    print("[INFO] Websocket Connected.")
                else:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    print(f"[{now_str}] [WARN] WebSocket connection failed (No Socket). Is OpenAlgo server running?")
                    return # Exit worker
                
            except Exception as e:
                now_str = datetime.now().strftime("%H:%M:%S")
                self.safe_print(f"[{now_str}] [WARN] WebSocket connection failed: {e}")
                logger.warning(f"WebSocket connection failed: {e}")
                return  # Exit worker if connection fails
            
            while self.is_running:
                # 1. Identify what we NEED to be subscribed to
                with self.lock:
                    needed_syms = set(self.trades.keys())
                    
                # Add Manual Symbols (Hot Start)
                if hasattr(self, 'manual_monitor_list'):
                    needed_syms = needed_syms.union(self.manual_monitor_list)
                
                # 2. Identify Deltas
                to_sub = needed_syms - current_subscriptions
                to_unsub = current_subscriptions - needed_syms
                
                # 3. Apply Changes
                if to_unsub:
                    try:
                        client.unsubscribe_ltp([{"exchange": "NFO", "symbol": s} for s in to_unsub])
                        current_subscriptions -= to_unsub
                    except: pass
                
                if to_sub:
                    try:
                        client.subscribe_ltp([{"exchange": "NFO", "symbol": s} for s in to_sub], 
                                           on_data_received=self.on_ws_data)
                        current_subscriptions |= to_sub
                        now_str = datetime.now().strftime("%H:%M:%S")
                        self.safe_print(f"[{now_str}] [WS] Subscribed to new symbols: {list(to_sub)}")
                    except Exception as e:
                        now_str = datetime.now().strftime("%H:%M:%S")
                        self.safe_print(f"[{now_str}] [ERROR] WS Sub Failed: {e}")
                
                time.sleep(2) # Re-sync every 2 seconds
                
        except Exception as e:
            now_str = datetime.now().strftime("%H:%M:%S")
            self.safe_print(f"[{now_str}] [ERROR] Websocket Worker Error: {e}")
        finally:
            try: client.disconnect() 
            except: pass

    def config_worker(self):
        """Monitors config.yaml for changes and reloads it dynamically"""
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        last_mtime = os.path.getmtime(config_path) if os.path.exists(config_path) else 0
        
        while self.is_running:
            try:
                if os.path.exists(config_path):
                    current_mtime = os.path.getmtime(config_path)
                    if current_mtime > last_mtime:
                        print(f"\n[CONFIG] Change detected in {os.path.basename(config_path)}.")
                        old_keys = set(CONFIG.keys())
                        if update_config_globally():
                            new_keys = set(CONFIG.keys())
                            # Simple key diff (ignoring deep values for brevity in log)
                            print(f"[CONFIG] Reload successful. Logic updated.")
                        last_mtime = current_mtime
            except Exception as e:
                print(f"[CONFIG ERROR] Failed to reload: {e}")
            
            time.sleep(2) # Check every 2 seconds

if __name__ == "__main__":
    trader = LiveTrader()
    if trader.initialize():
        if CONFIG.get("use_threading", True):
            # Parallel Execution Mode
            t_risk = threading.Thread(target=trader.risk_worker, daemon=True)
            t_scan = threading.Thread(target=trader.scanner_worker, daemon=True)
            t_ws = threading.Thread(target=trader.websocket_worker, daemon=True)
            
            t_config = threading.Thread(target=trader.config_worker, daemon=True)
            
            t_risk.start()
            t_scan.start()
            t_ws.start()
            t_config.start()
            
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                trader.is_running = False
                print("\n[INFO] Stopping threads...")
                sys.exit(0)
