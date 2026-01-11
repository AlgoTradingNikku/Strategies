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

class LiveTrader:
    def __init__(self):
        self.idx_symbol = None
        self.trades = {} # key: symbol, value: dict of trade state
        self.last_exit_time = None
        self.last_index_fetch_time = None
        
        # --- THREADING & WEBSOCKET ---
        self.lock = threading.Lock()
        self.is_running = True
        self.ws_data = {} # key: symbol, value: {'ltp': 0.0, 'time': 0}
        self.master_df = None
        
        # Track historical data for active/observed symbols to avoid re-fetching
        self.history_cache = {} # symbol -> {interval -> df}
        
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
        print(f"{'Lots (Mult):':<17} {CONFIG.get('lots', 1)}")
        print(f"{'Heikin Ashi:':<17} Index: {'ON' if CONFIG.get('index_use_ha') else 'OFF'}, Option: {'ON' if CONFIG.get('option_use_ha') else 'OFF'}")
        
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

        idx_conf = CONFIG.get("index", {})
        opt_conf = CONFIG.get("option", {})
        # Format the source nicely (e.g. OPTION -> Option)
        sig_src = str(CONFIG.get('signal_source', 'INDEX')).capitalize()
        print(f"[INFO] Index [HTF:{idx_conf['htf']['timeframe']}, LTF:{idx_conf['ltf']['timeframe']}] | Options [HTF:{opt_conf['htf']['timeframe']}, LTF:{opt_conf['ltf']['timeframe']}, Source: {sig_src} Data]")
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
        try:
            res = client.get_ltp(symbol, "NFO")
            if isinstance(res, dict):
                if 'ltp' in res and res['ltp']:
                    return float(res['ltp'])
        except Exception:
            pass
        return 0.0

    def manage_risk(self, symbol, curr_price, is_trend_reversed_input=None):
        """
        Handles SL, TP, 3-Stage Profit Guard, and Trend Reversal exits for a specific symbol.
        Returns True if position was closed, False otherwise.
        """
        # Load trade state under lock
        with self.lock:
            if symbol not in self.trades:
                return False
            trade = self.trades[symbol]
            entry = trade["entry_price"]
            highest = trade["highest_price"]
            trend_reversed = is_trend_reversed_input if is_trend_reversed_input is not None else trade.get("trend_reversed", False)

        if curr_price <= 0:
            return False

        rm_conf = CONFIG.get("risk_management", {})
        pnl_pct = (curr_price - entry) / entry * 100
        
        # --- 0. UPDATE HIGH WATER MARK ---
        if highest < curr_price:
            with self.lock:
                if symbol in self.trades:
                    self.trades[symbol]["highest_price"] = curr_price
            highest = curr_price

        # --- 3-STAGE PROFIT GUARD LOGIC ---
        tsl_price = 0.0
        stage = "INIT"
        
        if CONFIG.get("use_tsl"):
            # A. Calculate Base Distance (ATR or TIERED)
            dist_pts = 0.0
            mode = rm_conf.get("mode", "ATR")
            
            if mode == "ATR":
                atr = trade.get("atr", 0.0)
                mult = rm_conf.get("tsl_atr_multiplier", 2.0)
                dist_pts = atr * mult
                # Fallback if ATR is missing
                if dist_pts <= 0:
                    dist_pts = entry * 0.05 
            else:
                # TIERED MODE
                dist_pts = rm_conf.get("tsl_points_trail", 5.0)
                if rm_conf.get("tsl_use_dynamic_tiers"):
                    for tier in rm_conf.get("tsl_point_tiers", []):
                        if entry <= tier["max_entry"]:
                            dist_pts = tier["trail"]
                            break

            # B. Apply Profit Tightener (Stage 3)
            tighten_threshold = rm_conf.get("tighten_trigger_pct", 15.0)
            if pnl_pct >= tighten_threshold:
                stage = "TIGHTEN"
                ratio = rm_conf.get("tighten_ratio", 0.5)
                dist_pts *= ratio
            else:
                stage = "TRAILING"

            # C. Preliminary TSL Price
            tsl_price = highest - dist_pts

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
                status_line = (
                    f"   [HEARTBEAT:{symbol}] Price: {curr_price:.2f} | "
                    f"PnL: {pnl_pct:+.2f}% | "
                    f"Stage: {stage} | "
                    f"TSL: {tsl_price:.2f}"
                )
                print(status_line)
            
            if curr_price <= tsl_price:
                print(f"\n   !!! [EXIT] {symbol} 3-Stage Guard: {stage} Hit !!!")
                print(f"   (Price: {curr_price:.2f} <= TSL: {tsl_price:.2f})\n")
                self.execute_trade("SELL", curr_price, symbol)
                return True
        
        # --- MANAGE EXITS ---
        if trend_reversed:
            print(f"   [SIGNAL] Trend Reversed for {symbol}. Closing Position.")
            self.execute_trade("SELL", curr_price, symbol)
            return True

        return False

    def run_cycle(self):
        """Standard Check Cycle - Portfolio Management for Multiple Positions"""
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=CONFIG['lookback_days'])).strftime("%Y-%m-%d")
            
            idx_conf = CONFIG.get("index", {})
            opt_conf = CONFIG.get("option", {})
            max_pos = CONFIG.get("max_positions", 1)
            
            # --- 1. DATA FETCHING: INDEX ---
            df_idx_ltf = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['ltf']['timeframe'], silent=True)
            if df_idx_ltf.empty: return
            
            last_bar_time = df_idx_ltf.index[-1]
            index_price = df_idx_ltf['Close'].iloc[-1]
            
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
                if df_opt_ltf.empty: continue
                
                df_opt_htf = pd.DataFrame()
                if opt_conf['htf']['enabled']:
                    df_opt_htf = fetch_history(symbol, "NFO", start, end, interval=opt_conf['htf']['timeframe'], silent=True)

                def get_trend_data(df, stream_conf, use_ha):
                    if df.empty or len(df) < 5: return None, None
                    return self.calculate_utbot(df, stream_conf['sensitivity'], stream_conf['atr'], use_ha)

                # --- A. OBSERVING STATE ---
                if trade["state"] == "OBSERVING":
                    pos_opt, _ = get_trend_data(df_opt_ltf, opt_conf['ltf'], CONFIG.get('option_use_ha', False))
                    if pos_opt is None: continue
                    
                    is_confirm = (pos_opt.iloc[-2] == 1 and pos_opt.iloc[-3] == -1)
                    
                    htf_ok = True
                    if opt_conf['htf']['enabled'] and not df_opt_htf.empty:
                        pos_opt_htf, _ = get_trend_data(df_opt_htf, opt_conf['htf'], CONFIG.get('option_use_ha', False))
                        if pos_opt_htf is not None: htf_ok = (pos_opt_htf.iloc[-2] == 1)

                    if is_confirm and htf_ok:
                        print(f"   [CONFIRM] Option {trade['side']} Signal for {symbol}! Entering.")
                        self.execute_trade("BUY", df_opt_ltf['Close'].iloc[-1], symbol, 
                                          side=trade['side'], expiry_params=trade['expiry_params'], 
                                          idx_at_res=trade['idx_at_res'])
                    else:
                        with self.lock:
                            self.trades[symbol]["obs_candles"] = trade.get("obs_candles", 0) + 1
                        
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
                    pos_opt, _ = get_trend_data(df_opt_ltf, opt_conf['ltf'], CONFIG.get('option_use_ha', False))
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
                now_str = datetime.now().strftime("%H:%M:%S")
                # Periodic Portfolio Status (only if no active positions being tracked)
                if len(self.trades) == 0 and time.time() % 60 < 15:
                    print(f"[{now_str}] SCANNING (Portfolio: {len(self.trades)}/{max_pos} Slots Used) | Index: {index_price:.2f}")
                
                def get_trend_data(df, stream_conf, use_ha):
                    if df.empty or len(df) < 5: return None, None
                    return self.calculate_utbot(df, stream_conf['sensitivity'], stream_conf['atr'], use_ha)

                pos_idx, _ = get_trend_data(df_idx_ltf, idx_conf['ltf'], CONFIG.get('index_use_ha', True))
                if pos_idx is not None:
                    curr_idx = pos_idx.iloc[-2]
                    prev_idx = pos_idx.iloc[-3]
                    
                    if curr_idx != prev_idx:
                        # Check HTF
                        htf_ok = True
                        if idx_conf['htf']['enabled']:
                            df_idx_htf = fetch_history(self.idx_symbol, CONFIG["index_exchange"], start, end, interval=idx_conf['htf']['timeframe'], silent=True)
                            pos_htf, _ = get_trend_data(df_idx_htf, idx_conf['htf'], CONFIG.get('index_use_ha', True))
                            if pos_htf is not None: htf_ok = (pos_htf.iloc[-2] == curr_idx)
                        
                        if htf_ok:
                            side = "CALL" if curr_idx == 1 else "PUT"
                            ss = CONFIG.get("strike_selection", {})
                            target = get_strike_symbol(index_price, side, offset=ss.get("step", 0), 
                                                     expiry_type=ss.get("expiry", "WEEKLY"), 
                                                     expiry_offset=ss.get("offset", 0)) if ss.get("mode") == "AUTO" else CONFIG["trade_symbol"]
                            
                            if target and target not in self.trades:
                                with self.lock:
                                    self.trades[target] = {
                                        "state": "OBSERVING",
                                        "side": side,
                                        "obs_candles": 0,
                                        "idx_at_res": index_price,
                                        "expiry_params": {"step": ss.get("step", 0), "expiry": ss.get("expiry", "WEEKLY"), "offset": ss.get("offset", 0)},
                                        "atr": 0.0
                                    }
                                print("\n" + "="*50)
                                print(f"   [SIGNAL] NEW {side} SETUP DETECTED!")
                                print(f"   Instrument: {target}")
                                print(f"   Index Ref:  {index_price:.2f}")
                                print("="*50 + "\n")

        except Exception as e:
            print(f"[ERROR] Portfolio Cycle Error: {e}")
            import traceback; traceback.print_exc()

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

            order_payload = {
                "strategy": CONFIG['strategy_name'],
                "symbol": symbol,
                "action": action, 
                "exchange": "NFO",
                "pricetype": "MARKET",
                "product": "NRML",
                "quantity": qty,
                "position_size": qty if action == "BUY" else 0
            }
            
            if action == "SELL":
                print(f"\n   >>> [EXITING POSITION] Selling {qty} ({lots} Lots) of {symbol} <<<")
                
            if CONFIG.get("live_trade", False):
                print(f"   [LIVE] Executing {action} Order for {qty} {symbol}...")
                response = client.placesmartorder(**order_payload)
                print(f"   [API] SmartOrder Response: {response}")
            else:
                print(f"   [PAPER] Simulated {action} Order for {qty} ({lots} Lots) {symbol}")
                # Minimal mock response for logic to continue
                response = {"status": "success", "data": {"status": "success"}}
            
            is_success = False
            if isinstance(response, dict):
                if response.get('status') == 'success':
                    is_success = True
                elif 'data' in response and isinstance(response['data'], dict):
                    if response['data'].get('status') == 'success':
                        is_success = True

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
                            "trend_reversed": False
                        }
                    else:
                        # SELL
                        if symbol in self.trades:
                            del self.trades[symbol]
                        self.last_exit_time = datetime.now()
                        print(f"   [INFO] Position Closed for {symbol}. Cooldown active.")
                
                sys.stdout.flush()
                time.sleep(1)
            else:
                print(f"   [CRITICAL] Order REJECTED for {symbol}")

        except Exception as e:
            print(f"   [ERROR] Order Failed for {symbol}: {e}")

    # --- THREADED WORKERS ---
    def risk_worker(self):
        """Dedicated thread for high-speed risk monitoring of all active positions"""
        print("[INFO] Risk Worker (Bodyguard) started.")
        fast_interval = int(CONFIG.get("fast_check_seconds", 2))
        while self.is_running:
            try:
                # Iterate through a snapshot of active positions
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
            client.connect()
            print("[INFO] Websocket Connected.")
            
            while self.is_running:
                # 1. Identify what we NEED to be subscribed to
                with self.lock:
                    needed_syms = set(self.trades.keys())
                
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
                        print(f"[WS] Subscribed to new symbols: {list(to_sub)}")
                    except Exception as e:
                        print(f"[ERROR] WS Sub Failed: {e}")
                
                time.sleep(2) # Re-sync every 2 seconds
                
        except Exception as e:
            print(f"[ERROR] Websocket Worker Error: {e}")
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
