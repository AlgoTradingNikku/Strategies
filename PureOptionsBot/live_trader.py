"""
live_trader.py

Live Execution Runner for Pure Options Bot.
Polls market data (Index for signals, Option for execution), 
calculates indicators, and places real orders.
"""

from PureOptionsStrategy import (
    CONFIG, client, resolve_symbol_from_query, 
    fetch_history, get_hybrid_point_threshold,
    get_contract_type, get_strike_symbol
)
import backtrader as bt
import pandas as pd
from datetime import datetime, timedelta
import time
import os
import sys
import threading

class LiveTrader:
    def __init__(self):
        self.idx_symbol = None
        self.opt_symbol = None
        self.position = 0 # 1=Long, 0=Flat
        self.entry_price = 0.0
        self.highest_price = 0.0 # Track max price for TSL
        self.last_exit_time = None
        self.bot_state = "SCANNING" # SCANNING, OBSERVING, POSITION
        self.observed_symbol = None
        self.observed_side = None # "CALL" or "PUT"
        self.observation_start_time = None
        self.observation_candles = 0 # Track candles spent in OBSERVING
        self.cached_index_price = 0.0
        self.last_index_fetch_time = None
        
        # --- THREADING & WEBSOCKET ---
        self.lock = threading.Lock()
        self.is_trend_reversed = False
        self.is_running = True
        self.ws_ltp = 0.0
        self.ws_last_update = 0
        self.current_subscribed_symbol = None
        self.master_df = None
        self.current_option_atr = 0.0 # Store ATR for risk management
        
        # --- FORCE BUY TRACKING ---

    def initialize(self):
        print("\n" + "="*60)
        print(f"{'Index Source:':<17} {CONFIG['index_query']} ({CONFIG['index_exchange']})")
        print(f"{'Signal Source:':<17} {CONFIG.get('signal_source', 'INDEX')}")
        
        ss = CONFIG.get("strike_selection", {})
        if ss.get("mode") == "AUTO":
            print(f"{'Strike Selection:':<17} AUTO (Step: {ss.get('step')}, {ss.get('expiry')})")
        else:
            print(f"{'Strike Selection:':<17} MANUAL ({CONFIG['trade_symbol']})")

        idx = CONFIG.get("index", {})
        opt = CONFIG.get("option", {})
        
        print(f"{'Index LTF:':<17} {idx['ltf']['timeframe']} (Sens: {idx['ltf']['sensitivity']}, ATR: {idx['ltf']['atr']})")
        print(f"{'Index HTF:':<17} {idx['htf']['timeframe']} (Sens: {idx['htf']['sensitivity']}, ATR: {idx['htf']['atr']}) [{'ENABLED' if idx['htf']['enabled'] else 'DISABLED'}]")
        print(f"{'Option LTF:':<17} {opt['ltf']['timeframe']} (Sens: {opt['ltf']['sensitivity']}, ATR: {opt['ltf']['atr']})")
        print(f"{'Option HTF:':<17} {opt['htf']['timeframe']} (Sens: {opt['htf']['sensitivity']}, ATR: {opt['htf']['atr']}) [{'ENABLED' if opt['htf']['enabled'] else 'DISABLED'}]")

        print(f"{'Bot Mode:':<17} {'LIVE TRADE' if CONFIG.get('live_trade') else 'PAPER TRADE'}")
        print(f"{'Quantity:':<17} {CONFIG['quantity']}")
        print(f"{'Heikin Ashi:':<17} {'ENABLED' if CONFIG['use_heikin_ashi'] else 'DISABLED'}")
        
        if CONFIG.get('use_sl'):
            print(f"{'Stop Loss:':<17} ENABLED ({CONFIG.get('sl_pct')}%)")
        if CONFIG.get('use_tp'):
            print(f"{'Take Profit:':<17} ENABLED ({CONFIG.get('tp_pct')}%)")
        
        tsl_mode = CONFIG.get("tsl_mode", "PCT")
        print(f"{'TSL Mode:':<17} {tsl_mode}")
        if tsl_mode == 'HYBRID':
            print(f"{'Hybrid Trig:':<17} > {CONFIG.get('tsl_hybrid_threshold', 10.0)}%")
        if tsl_mode == "ATR":
            print(f"{'ATR Multiplier:':<17} {CONFIG.get('tsl_atr_multiplier', 1.5)}")
        
        # Always resolve Index symbol for reference/display
        self.idx_symbol = resolve_symbol_from_query(CONFIG["index_query"], exchange=CONFIG["index_exchange"])
            
        ss_mode = CONFIG.get("strike_selection", {}).get("mode", "MANUAL")
        if ss_mode == "MANUAL":
            self.opt_symbol = resolve_symbol_from_query(CONFIG["trade_symbol"], exchange="NFO")
        else:
            self.opt_symbol = None
            print(f"   [INFO] Auto-Strike Mode: Manual symbol resolution skipped.")
        
        if (not self.idx_symbol) or (ss_mode == "MANUAL" and not self.opt_symbol):
            print("[ERROR] Critical Error: Could not resolve required symbols. Exiting.")
            return False

            
        print(f"\n[INFO] Strategy started.")
        
        print("[INFO] Fetching security master...")
        try:
            self.master_df = client.instruments()
            print(f"[INFO] Master fetched ({len(self.master_df)} instruments).")
        except Exception as e:
            print(f"[WARN] Failed to fetch instruments master: {e}")

        print(f"[INFO] Source: {CONFIG.get('signal_source', 'INDEX')} | LTFs: Index({CONFIG['index']['ltf']['timeframe']}) / Option({CONFIG['option']['ltf']['timeframe']})")
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
            
            # Position
            prev_p = pos[i-1]
            if prev_s < prev_trail and s > prev_trail:
                pos[i] = 1
            elif prev_s > prev_trail and s < prev_trail:
                pos[i] = -1
            else:
                pos[i] = prev_p
                
        return pd.Series(pos, index=df.index), pd.Series(trail, index=df.index)

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

    def get_live_option_price(self):
        """High-speed LTP fetch for the option contract with Websocket priority"""
        if not self.opt_symbol: return 0.0
        
        # 1. Try Websocket Cache First (Zero Latency)
        with self.lock:
            ws_price = self.ws_ltp
            last_upd = self.ws_last_update
            
        # Use WS price if it's fresh (within last 5 seconds)
        if ws_price > 0 and (time.time() - last_upd) < 5:
            return ws_price

        # 2. Fallback to REST API (2s Safety Net)
        try:
            res = client.get_ltp(self.opt_symbol, "NFO")
            if isinstance(res, dict):
                if 'ltp' in res and res['ltp']:
                    return float(res['ltp'])
                elif self.opt_symbol in res:
                    return float(res[self.opt_symbol])
            return 0.0
        except:
            return 0.0

    def manage_risk(self, curr_price, is_trend_reversed_input=None):
        """
        Handles SL, TP, TSL, and Trend Reversal exits.
        Returns True if position was closed, False otherwise.
        """
        # Load shared state under lock
        with self.lock:
            pos = self.position
            entry = self.entry_price
            highest = self.highest_price
            trend_reversed = is_trend_reversed_input if is_trend_reversed_input is not None else self.is_trend_reversed

        if pos == 0 or curr_price <= 0:
            return False

        pnl_pct = (curr_price - entry) / entry * 100
        
        # --- TRAILING STOP LOGIC ---
        if CONFIG.get("use_tsl"):
            # Update High Water Mark (Shared state update)
            if highest < curr_price:
                with self.lock:
                    self.highest_price = curr_price
                highest = curr_price
            
            # Calculate Dynamic TSL %
            base_tsl_pct = CONFIG.get("tsl_pct", 5.0)
            current_tsl_pct = base_tsl_pct
            
            if CONFIG.get("use_stepped_tsl"):
                profit_at_high = (highest - entry) / entry * 100
                for step in CONFIG.get("tsl_steps", []):
                    if profit_at_high < step["profit"]:
                        current_tsl_pct = step["tsl"]
                        break
            
            # --- LEASH TIGHTENER ---
            is_profit_locked = (highest >= entry * 1.01)
            applied_tsl_pct = current_tsl_pct
            leash_active = False
            
            if trend_reversed and is_profit_locked and CONFIG.get("use_reversal_leash"):
                applied_tsl_pct = CONFIG.get("reversal_leash_pct", 1.5)
                leash_active = True
            
            # Calculate Stop Price
            tsl_price = 0.0
            tsl_mode = CONFIG.get('tsl_mode', 'PCT')
            use_atr = False

            # Check Hybrid Switch
            if tsl_mode == 'HYBRID':
                trigger_type = CONFIG.get("tsl_hybrid_trigger", "PCT")
                if trigger_type == "POINTS":
                    pts_gained = highest - entry
                    pts_required = get_hybrid_point_threshold(entry)
                    if pts_gained >= pts_required:
                        use_atr = True
                else:
                    # Percentage Based Trigger (Default)
                    threshold = CONFIG.get('tsl_hybrid_threshold', 10.0)
                    if pnl_pct >= threshold:
                        use_atr = True
                    else:
                        use_atr = False
            elif tsl_mode == 'ATR':
                use_atr = True

            if use_atr:
                # ATR Logic
                current_atr = self.current_option_atr
                multiplier = CONFIG.get('tsl_atr_multiplier', 1.5)
                
                if current_atr <= 0:
                     # Fallback to PCT if no ATR available
                     tsl_price = highest * (1 - base_tsl_pct / 100.0)
                else:
                    atr_stop = highest - (current_atr * multiplier)
                    
                    # HYBRID SAFETY: Take MAX of PCT/Points vs ATR to never lower the stop
                    if tsl_mode == 'HYBRID':
                        if CONFIG.get("tsl_hybrid_trigger") == "POINTS":
                            pts_required = get_hybrid_point_threshold(entry)
                            safety_stop = highest - pts_required
                        else:
                            safety_stop = highest * (1 - applied_tsl_pct / 100.0)
                        
                        tsl_price = max(atr_stop, safety_stop)
                    else:
                        tsl_price = atr_stop
            else:
                # Phase 1: Point-Based or Percentage-Based Trail
                if tsl_mode == 'HYBRID' and CONFIG.get("tsl_hybrid_trigger") == "POINTS":
                    pts_required = get_hybrid_point_threshold(entry)
                    tsl_price = highest - pts_required
                else:
                    # Percentage Logic
                    tsl_price = highest * (1 - applied_tsl_pct / 100.0)
            
            # --- NEW: Cost Protection (Break-Even) ---
            if is_profit_locked:
                tsl_price = max(tsl_price, entry)
                
            status_line = f"   Entry: {entry:.2f} | PnL: {pnl_pct:+.2f}% | TSL: {tsl_price:.2f} (High: {highest:.2f}, {applied_tsl_pct}%)"
            if leash_active: status_line += " [LEASH ACTIVE]"
            print(status_line)
            
            if curr_price <= tsl_price:
                print(f"   [EXIT] Trailing Stop Hit! (Price: {curr_price:.2f} <= TSL: {tsl_price:.2f})")
                self.execute_trade("SELL", curr_price)
                return True
        else:
            print(f"   Entry: {entry:.2f} | Current PnL: {pnl_pct:+.2f}%")

        # Fixed SL
        if CONFIG.get("use_sl") and pnl_pct <= -CONFIG["sl_pct"]:
            print(f"   [EXIT] Stop Loss Hit!")
            self.execute_trade("SELL", curr_price)
            return True
        # Fixed TP
        if CONFIG.get("use_tp") and pnl_pct >= CONFIG["tp_pct"]:
            print(f"   [EXIT] Take Profit Hit!")
            self.execute_trade("SELL", curr_price)
            return True
        
        # Trend Reversal Exit (If not profit-protected)
        if trend_reversed:
            should_exit_priority = True
            if CONFIG.get("use_tsl") and highest > 0:
                if not (is_profit_locked and CONFIG.get("use_reversal_leash")):
                     if is_profit_locked:
                         should_exit_priority = False
                         print("   [FILTERED] Trend Reversed but Profit Locked. Holding...")
            
            if should_exit_priority:
                print(f"   [SIGNAL] 3m Trend Reversed. Closing Position.")
                self.execute_trade("SELL", curr_price)
                return True

        return False

    def run_cycle(self):
        """Standard Check Cycle - Dual Chart State Machine (4-Way Precision)"""
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=CONFIG['lookback_days'])).strftime("%Y-%m-%d")
            fetch_stats = []
            
            idx_conf = CONFIG.get("index", {})
            opt_conf = CONFIG.get("option", {})
            
            # --- 0. SESSION CHECK ---
            now = datetime.now()
            is_weekday = now.weekday() < 5
            is_hours = 9 <= now.hour < 16 # Broad window
            if not is_weekday or not is_hours:
                # Every 5 minutes or so in Scanning mode, we can show a heartbeat
                if self.bot_state == "SCANNING" and now.minute % 5 != 0:
                    return
                print(f"[{now.strftime('%H:%M:%S')}] [SESSION] Market Closed. Bot is idling...")
                if self.bot_state == "SCANNING":
                    # In scanning mode we might still want to fetch history once to show latest price
                    pass 
                else: 
                    # If in position or observing, we MUST continue to handle risk if prices were to move (WS)
                    pass
            
            # --- 1. DATA FETCHING BASED ON STATE ---
            df_idx_ltf = pd.DataFrame()
            df_idx_htf = pd.DataFrame()
            df_opt_ltf = pd.DataFrame()
            df_opt_htf = pd.DataFrame()
            
            # Always fetch Index LTF for logic/state reference
            df_idx_ltf = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['ltf']['timeframe'], silent=True)
            if df_idx_ltf.empty: return
            fetch_stats.append(f"IDX-LTF({idx_conf['ltf']['timeframe']})/{len(df_idx_ltf)}")
            index_price = df_idx_ltf['Close'].iloc[-1]
            
            if self.bot_state == "SCANNING":
                if idx_conf['htf']['enabled']:
                    df_idx_htf = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['htf']['timeframe'], silent=True)
                    fetch_stats.append(f"IDX-HTF({idx_conf['htf']['timeframe']})/{len(df_idx_htf)}")
            
            elif self.bot_state == "OBSERVING":
                df_opt_ltf = fetch_history(self.observed_symbol, "NFO", start, end, interval=opt_conf['ltf']['timeframe'], silent=True)
                fetch_stats.append(f"OPT-LTF({opt_conf['ltf']['timeframe']})/{len(df_opt_ltf)}")
                if opt_conf['htf']['enabled']:
                    df_opt_htf = fetch_history(self.observed_symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'], silent=True)
                    fetch_stats.append(f"OPT-HTF({opt_conf['htf']['timeframe']})/{len(df_opt_htf)}")
            
            elif self.bot_state == "POSITION":
                df_opt_ltf = fetch_history(self.opt_symbol, "NFO", start, end, interval=opt_conf['ltf']['timeframe'], silent=True)
                fetch_stats.append(f"OPT-LTF({opt_conf['ltf']['timeframe']})/{len(df_opt_ltf)}")
                if opt_conf['htf']['enabled']:
                    df_opt_htf = fetch_history(self.opt_symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'], silent=True)
                    fetch_stats.append(f"OPT-HTF({opt_conf['htf']['timeframe']})/{len(df_opt_htf)}")

            # --- 2. INDICATOR CALCULATION ---
            def get_trend_data(df, stream_conf):
                if df.empty or len(df) < 5: return None, None
                pos, trail = self.calculate_utbot(df, stream_conf['sensitivity'], stream_conf['atr'], CONFIG['use_heikin_ashi'])
                return pos, trail

            # --- 3. LOGGING STATUS ---
            now_time = datetime.now().strftime("%H:%M:%S")
            # Compact one-liner status
            status_line = f"[{now_time}] {self.bot_state} | "
            if self.bot_state == "SCANNING":
                status_line += f"IDX: {index_price:.2f}"
            elif self.bot_state == "OBSERVING":
                status_line += f"{self.observed_symbol}: {df_opt_ltf['Close'].iloc[-1] if not df_opt_ltf.empty else 'N/A'}"
            elif self.bot_state == "POSITION":
                status_line += f"{self.opt_symbol}: {df_opt_ltf['Close'].iloc[-1]}"
            
            # Append fetch stats to the end
            status_line += f" | {', '.join(fetch_stats)}"
            print(status_line)

            if self.bot_state == "SCANNING":
                pos_idx, _ = get_trend_data(df_idx_ltf, idx_conf['ltf'])
                if pos_idx is None: return
                
                curr_idx_ltf = pos_idx.iloc[-2]
                prev_idx_ltf = pos_idx.iloc[-3]
                
                # Check for Signal
                idx_f_entry = (curr_idx_ltf != prev_idx_ltf)
                if idx_f_entry:
                    # Index HTF Alignment
                    htf_ok = True
                    if idx_conf['htf']['enabled']:
                        pos_htf, _ = get_trend_data(df_idx_htf, idx_conf['htf'])
                        if pos_htf is not None:
                            htf_ok = (pos_htf.iloc[-2] == curr_idx_ltf)
                    
                    if htf_ok:
                        side = "CALL" if curr_idx_ltf == 1 else "PUT"
                        print(f"   [SIGNAL] Index {side} Cross! HTF Aligned.")
                        # Auto Select Strike
                        ss = CONFIG.get("strike_selection", {})
                        if ss.get("mode") == "AUTO":
                            target_symbol = get_strike_symbol(index_price, side, ss.get("step", 0), ss.get("expiry", "WEEKLY"))
                        else:
                            target_symbol = CONFIG["trade_symbol"]
                            
                        if target_symbol:
                            self.bot_state = "OBSERVING"
                            self.observed_symbol = target_symbol
                            self.observed_side = side
                            self.observation_candles = 0
                            print(f"   >>> Transition to OBSERVING: {target_symbol}")
                    else:
                        print(f"   [FILTERED] Index cross ignored: Index HTF conflict.")

            elif self.bot_state == "OBSERVING":
                if df_opt_ltf.empty: return
                pos_opt, _ = get_trend_data(df_opt_ltf, opt_conf['ltf'])
                if pos_opt is None: return
                
                curr_opt_ltf = pos_opt.iloc[-2]
                prev_opt_ltf = pos_opt.iloc[-3]
                opt_price = df_opt_ltf['Close'].iloc[-1]
                
                # Confirmation Signal (UTBot Buy on Option Chart)
                is_confirm = (curr_opt_ltf == 1 and prev_opt_ltf == -1)
                
                # Option HTF Alignment
                htf_ok = True
                if opt_conf['htf']['enabled']:
                    pos_opt_htf, _ = get_trend_data(df_opt_htf, opt_conf['htf'])
                    if pos_opt_htf is not None:
                        htf_ok = (pos_opt_htf.iloc[-2] == 1)

                if is_confirm and htf_ok:
                    print(f"   [CONFIRM] Option {self.observed_side} Chart BUY Signal! Entering trade.")
                    self.opt_symbol = self.observed_symbol
                    self.execute_trade("BUY", opt_price)
                    self.bot_state = "POSITION"
                else:
                    self.observation_candles += 1
                    timeout = CONFIG.get("option_signal_timeout", 5)
                    print(f"   [WAIT] Watching {self.observed_symbol} ({opt_conf['ltf']['timeframe']}) signal... ({self.observation_candles}/{timeout})")
                    
                    # Check for Index Trend Reversal (Invalidates setup)
                    pos_idx, _ = get_trend_data(df_idx_ltf, idx_conf['ltf'])
                    if pos_idx is not None:
                        idx_trend = pos_idx.iloc[-2]
                        target_idx_trend = 1 if self.observed_side == "CALL" else -1
                        if idx_trend != target_idx_trend:
                            print(f"   [RESET] Index trend reversed. Setup invalidated.")
                            self.bot_state = "SCANNING"
                    
                    if self.observation_candles >= timeout:
                        print(f"   [TIMEOUT] Option signal delayed too long. Resetting to Scanning.")
                        self.bot_state = "SCANNING"

            elif self.bot_state == "POSITION":
                if df_opt_ltf.empty: return
                pos_opt, _ = get_trend_data(df_opt_ltf, opt_conf['ltf'])
                if pos_opt is None: return
                
                curr_opt_ltf = pos_opt.iloc[-2]
                prev_opt_ltf = pos_opt.iloc[-3]
                opt_price = df_opt_ltf['Close'].iloc[-1]
                
                # Risk Management
                is_exit = (curr_opt_ltf == -1 and prev_opt_ltf == 1)
                with self.lock:
                    self.is_trend_reversed = is_exit
                
                print(f"   [POSITION] Price: {opt_price:.2f} | Exit Signal: {'Yes' if is_exit else 'No'}")
                self.manage_risk(opt_price, is_trend_reversed_input=is_exit)
                
                if self.position == 0:
                    print("   [INFO] Position exited. Returning to SCANNING.")
                    self.bot_state = "SCANNING"

        except Exception as e:
            print(f"[ERROR] Cycle Error: {e}")
            import traceback
            traceback.print_exc()

    def execute_trade(self, action, price):
        """Place Order via API"""
        print(f"   [ORDER] Placing {action} for {CONFIG['quantity']} qty...")
        
        # Check Safety Toggle
        if not CONFIG.get("live_trade", False):
            print(f"   [PAPER] Live Trade is OFF. Simulated {action} @ {price}")
            with self.lock:
                if action == "BUY":
                    self.position = 1
                    self.entry_price = price
                    self.highest_price = price
                    self.is_trend_reversed = False
                else:
                    self.position = 0
                    self.highest_price = 0.0
                    self.is_trend_reversed = False
            return

        try:
            # --- AUTO LOT SIZE CORRECTION ---
            lot_size = 1 # Default
            try:
                if self.master_df is not None and not self.master_df.empty:
                    # Filter for this symbol
                    match = self.master_df[(self.master_df['symbol'] == self.opt_symbol) & 
                                          (self.master_df['exchange'] == 'NFO')]
                    if not match.empty:
                        lot_size = int(match.iloc[0]['lotsize'])
            except: pass

            base_qty = int(CONFIG['quantity'])
            qty = (base_qty // lot_size) * lot_size
            
            if qty == 0:
                print(f"   [ERROR] Quantity {base_qty} is less than Lot Size {lot_size}. Order skipped.")
                return

            if qty != base_qty:
                print(f"   [SAFETY] Adjusted Quantity {base_qty} -> {qty} (Multiple of Lot Size {lot_size})")

            # Reset Force Buy flag if it was active

            order_payload = {
                "strategy": CONFIG['strategy_name'],
                "symbol": self.opt_symbol,
                "action": action, 
                "exchange": "NFO",
                "pricetype": "MARKET",
                "product": "NRML",
                "quantity": qty,
                "position_size": qty if action == "BUY" else 0
            }
            
            if action == "SELL":
                print(f"\n   >>> [EXITING POSITION] Selling {qty} {self.opt_symbol} <<<")
                
            response = client.placesmartorder(**order_payload)
            print(f"   [API] SmartOrder Response: {response}")
            
            # --- SYNC PROTECTION ---
            # Only update internal state if the broker accepted the order
            is_success = False
            if isinstance(response, dict):
                # Check for OpenAlgo success status
                if response.get('status') == 'success':
                    is_success = True
                elif 'data' in response and isinstance(response['data'], dict):
                    if response['data'].get('status') == 'success':
                        is_success = True

            if is_success:
                with self.lock:
                    if action == "BUY":
                        self.position = 1
                        self.entry_price = price
                        self.highest_price = price # Initialize TSL base
                        self.is_trend_reversed = False
                    else:
                        self.position = 0
                        self.highest_price = 0.0
                        self.is_trend_reversed = False
                        self.last_exit_time = datetime.now() # Start Cooldown
                        print(f"   [INFO] Position Closed. Cooldown active for {CONFIG.get('cooldown_seconds', 300)}s.")
                
                sys.stdout.flush()
                time.sleep(1) # Ensure log visibility
            else:
                print(f"   [CRITICAL] Order was REJECTED by API. Keeping Position = {self.position}")

        except Exception as e:
            print(f"   [ERROR] Order Failed: {e}")

    # --- THREADED WORKERS ---
    def risk_worker(self):
        """Dedicated thread for high-speed risk monitoring"""
        print("[INFO] Risk Worker (Bodyguard) started.")
        fast_interval = int(CONFIG.get("fast_check_seconds", 2))
        while self.is_running:
            try:
                # Check position under lock
                with self.lock:
                    pos = self.position
                
                if pos != 0:
                    lp = self.get_live_option_price()
                    if lp > 0:
                        self.manage_risk(lp)
                
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
        """Websocket Callback - Updates live price in real-time"""
        try:
            # OpenAlgo WS format: {'symbol': '...', 'ltp': ...}
            if isinstance(data, dict):
                sym = data.get('symbol')
                ltp = data.get('ltp')
                if sym == self.opt_symbol and ltp:
                    with self.lock:
                        self.ws_ltp = float(ltp)
                        self.ws_last_update = time.time()
        except Exception as e:
            pass

    def websocket_worker(self):
        """Manages Websocket connection and subscriptions"""
        if not CONFIG.get("use_websocket", True):
            return

        ws_url = CONFIG.get("ws_url", "ws://127.0.0.1:8765")
        print(f"[INFO] Connecting to Websocket: {ws_url}")
        
        try:
            # Update client with ws_url if provided
            client.ws_url = ws_url
            client.connect()
            print("[INFO] Websocket Connected.")
            
            while self.is_running:
                # Synchronize subscription with opt_symbol
                with self.lock:
                    target_sym = self.opt_symbol
                
                if target_sym and target_sym != self.current_subscribed_symbol:
                    # Unsubscribe from old
                    if self.current_subscribed_symbol:
                        try:
                            client.unsubscribe_ltp([{"exchange": "NFO", "symbol": self.current_subscribed_symbol}])
                        except: pass
                    
                    # Subscribe to new
                    print(f"[WS] Subscribing to: {target_sym}")
                    try:
                        client.subscribe_ltp([{"exchange": "NFO", "symbol": target_sym}], 
                                           on_data_received=self.on_ws_data)
                        self.current_subscribed_symbol = target_sym
                    except Exception as e:
                        print(f"[ERROR] WS Subscription Failed: {e}")
                
                time.sleep(1) # Check for symbol changes every 1s
                
        except Exception as e:
            print(f"[ERROR] Websocket Worker Error: {e}. Falling back to REST.")
        finally:
            try: client.disconnect() 
            except: pass

if __name__ == "__main__":
    trader = LiveTrader()
    if trader.initialize():
        if CONFIG.get("use_threading", True):
            # Parallel Execution Mode
            t_risk = threading.Thread(target=trader.risk_worker, daemon=True)
            t_scan = threading.Thread(target=trader.scanner_worker, daemon=True)
            t_ws = threading.Thread(target=trader.websocket_worker, daemon=True)
            
            t_risk.start()
            t_scan.start()
            t_ws.start()
            
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                trader.is_running = False
                print("\n[INFO] Stopping threads...")
                sys.exit(0)
        else:
            # Single-Threaded Periodic Mode (Legacy fallback)
            last_full_cycle = 0
            slow_interval = int(CONFIG.get("fetch_interval_seconds", 15))
            fast_interval = int(CONFIG.get("fast_check_seconds", 2))
            
            while True:
                try:
                    now = time.time()
                    if now - last_full_cycle >= slow_interval:
                        trader.run_cycle()
                        last_full_cycle = time.time()
                    elif trader.position != 0:
                        lp = trader.get_live_option_price()
                        if lp > 0:
                            trader.manage_risk(lp)
                    time.sleep(fast_interval)
                except KeyboardInterrupt:
                    print("\n[INFO] Stopped by user.")
                    sys.exit(0)
                except Exception as e:
                    print(f"[ERROR] Runtime Error: {e}")
                    time.sleep(fast_interval)
