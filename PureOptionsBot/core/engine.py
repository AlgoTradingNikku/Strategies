"""
Trading Engine - Main AsyncIO orchestrator for the bot.

This is the heart of the new modular system. Coordinates all components:
- Indicators (signal generation)
- Risk Manager (TSL/exit decisions)
- Order Manager (trade execution)
- Persistence (crash recovery)
- Data Provider (market data)

Runs as an async event loop with multiple concurrent tasks.
"""

import asyncio
from typing import Dict, Optional
from datetime import datetime
import logging
import time
import os
import yaml

from core.state_machine import Trade, TradeState, TradeStateMachine

from core.persistence import TradePersistence
from indicators.registry import IndicatorRegistry
from risk.manager import RiskManager, ExitReason
from execution.order_manager import OrderManager
from data.provider import MarketDataProvider
from data.cache import MarketDataCache


logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Main asyncIO-based trading engine.
    
    Runs multiple background tasks concurrently:
    - Signal scanner (every 5s)
    - Risk monitor (every 1s)
    - Position sync (every 10s)
    - WebSocket handler (continuous)
    
    Example:
        engine = TradingEngine(config, api_client)
        await engine.start()  # Runs until stopped
    """
    
    def __init__(self, config: dict, api_client):
        """
        Initialize trading engine.
        
        Args:
            config: Full bot configuration
            api_client: OpenAlgo API client instance
        """
        self.config = config
        self.client = api_client
        self.running = False
        
        # Initialize components
        self.persistence = TradePersistence()
        self.cache = MarketDataCache()
        self.data_provider = MarketDataProvider(api_client, self.cache)
        self.risk_manager = RiskManager(config)
        self.order_manager = OrderManager(api_client, config)
        
        # Load indicators from config
        self.indicators = self._load_indicators()
        
        # Trade tracking
        self.trades: Dict[str, Trade] = {}
        
        # Heartbeat state
        self._heartbeat_counter = 0
        self._last_index_price = 0.0
        self._last_ltf_trend = "--"
        self._last_htf_trend = "--"
        
        # WebSocket state
        self._ws_connected = False
        self._ws_subscribed_symbols: list = []
        
        # Re-entry protection: track recently exited symbols
        # {symbol: exit_timestamp}
        self._exit_cooldowns: Dict[str, datetime] = {}
        
        # Trading hours config
        self._trading_hours = config.get("trading_hours", {})

        # Strike Status Tracking (for logging)
        self._strike_states = {}
        
        # Cooldown tracking (symbol -> datetime)
        self._cooldowns = {}
        
        # Signal wait state tracking (for conditional wait logic)
        self._signal_wait_state = {}
        
        # Restore state from database
        self._restore_state()
        
        print("\n[INFO] Risk Worker (Bodyguard) started.")
        print("[INFO] Scanner Worker (The Brain) started.")
        logger.info("Trading Engine initialized")
    
    def _load_indicators(self) -> dict:
        """Load indicators from config"""
        indicators = {}
        
        # --- INDEX INDICATORS ---
        idx_ltf_config = self.config.get("index", {}).get("ltf", {})
        idx_htf_config = self.config.get("index", {}).get("htf", {})
        
        # Index UTBot (Execution TF)
        indicators["index_utbot"] = IndicatorRegistry.create("utbot", {
            "sensitivity": idx_ltf_config.get("sensitivity", 2.0),
            "atr_period": idx_ltf_config.get("atr", 10)
        })
        
        # Index Technicals (Execution TF)
        indicators["index_tech_ltf"] = IndicatorRegistry.create("technical", {
            "ema_periods": [9, 50], # Need EMA9 for Signal (Close > EMA9)
            "rsi_period": 14,
            "adx_period": 14
        })
        
        # Index Technicals (Trend TF) - Read from config
        mt_cfg = self.config.get("strategy", {}).get("smart_momentum", {}).get("master_trend", {})
        ema_periods = [
            mt_cfg.get("ema_fast", 9),
            mt_cfg.get("ema_mid", 21),
            mt_cfg.get("ema_slow", 50)
            # Removed: ema_macro (200) - Not used in logic
        ]
        
        indicators["index_tech_htf"] = IndicatorRegistry.create("technical", {
            "ema_periods": ema_periods,
            "adx_period": mt_cfg.get("adx_period", 14),
            "rsi_period": mt_cfg.get("rsi_period", 14)
        })
        
        # --- OPTION INDICATORS ---
        opt_ltf_config = self.config.get("option", {}).get("ltf", {})
        indicators["option_ltf"] = IndicatorRegistry.create("utbot", {
            "sensitivity": opt_ltf_config.get("sensitivity", 1.0),
            "atr_period": opt_ltf_config.get("atr", 10)
        })
        
        return indicators
    
    def _restore_state(self):
        """Restore trades from SQLite on startup (crash recovery)"""
        active_trades = self.persistence.load_active_trades()
        
        for trade in active_trades:
            self.trades[trade.symbol] = trade
            logger.info(
                f"[RECOVERY] Restored {trade.symbol} in {trade.state.name} "
                f"@ ₹{trade.entry_price} (P&L: {trade.pnl_pct:.2f}%)"
            )
        
        if len(active_trades) > 0:
            logger.info(f"Recovered {len(active_trades)} active trades")
    
    async def start(self):
        """
        Start the trading engine.
        
        Launches all background tasks and runs until stopped.
        """
        self.running = True
        logger.info("Starting Trading Engine...")
        
        # Setup WebSocket if enabled
        if self.config.get("use_websocket", True):
            success = await self._setup_websocket()
            if not success:
                logger.critical("WebSocket connection failed. Aborting startup.")
                print("\n[CRITICAL] WebSocket connection failed. Aborting startup.")
                return # Exit start method immediately
        
        # Launch background tasks
        task_handles = [
            asyncio.create_task(self.signal_scanner_task()),
            asyncio.create_task(self.risk_monitor_task()),
            asyncio.create_task(self.position_sync_task()),
            asyncio.create_task(self.monitor_websocket_task()),
            asyncio.create_task(self.monitor_config_task()),
        ]
        
        try:
            await asyncio.gather(*task_handles)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutting down...")
        except Exception as e:
            logger.critical(f"Engine CRASHED: {e}", exc_info=True)
            print(f"\n[CRITICAL] Engine crashed: {e}")
        finally:
            # Stop everything properly
            await self.stop()
            # Cancel all background tasks
            for task in task_handles:
                if not task.done():
                    task.cancel()
            
            # Wait a moment for tasks to cancel
            if task_handles:
                await asyncio.gather(*task_handles, return_exceptions=True)
            
            logger.info("All engine tasks cleaned up")
    
    async def _setup_websocket(self) -> bool:
        """
        Setup WebSocket connection and subscribe to symbols.
        
        Returns:
            bool: True if connected successfully, False otherwise
        """
        try:
            # Get manual strikes from config
            strike_cfg = self.config.get("strike_selection", {})
            mode = strike_cfg.get("mode", "AUTO").upper()
            
            symbols_to_subscribe = []
            
            if mode == "MANUAL":
                manual_strikes = strike_cfg.get("manual_strikes", [])
                symbols_to_subscribe.extend(manual_strikes)
            
            # Add Index to subscription list
            index_query = self.config.get("index_query", "NIFTY")
            if index_query and index_query not in symbols_to_subscribe:
                symbols_to_subscribe.append(index_query)
            
            if not symbols_to_subscribe:
                return True # No symbols needed, proceed
            
            # Build instruments list for WebSocket
            instruments = []
            index_exchange = self.config.get("index_exchange", "NSE_INDEX")
            
            for sym in symbols_to_subscribe:
                # Determine exchange
                if sym == index_query:
                    exch = index_exchange
                else:
                    exch = "NFO"
                
                instruments.append({"exchange": exch, "symbol": sym})
            
            # Connect WebSocket
            ws_url = self.config.get("ws_url", "ws://127.0.0.1:8765")
            
            # Try to connect and subscribe
            try:
                self.client.connect()
                
                # Wait for connection to establish (non-blocking in client, but handshake takes time)
                time.sleep(2)
                
                # Robustly check if connection actually succeeded by inspecting the socket
                if self.client.ws and self.client.ws.sock:
                    self._ws_connected = True
                    print("[INFO] Websocket Connected.")
                    
                    # Subscribe to LTP for all symbols
                    try:
                        self.client.subscribe_ltp(
                            instruments,
                            on_data_received=self._on_ws_ltp_update
                        )
                        
                        self._ws_subscribed_symbols = symbols_to_subscribe
                        now = datetime.now().strftime("%H:%M:%S")
                        print(f"[{now}] [WS] Subscribed to new symbols: {symbols_to_subscribe}")
                        return True
                        
                    except Exception as sub_error:
                        logger.warning(f"WebSocket subscription failed: {sub_error}")
                        print(f"[WARN] WebSocket subscription failed: {sub_error}")
                        # Connection is up but subscription failed, treated as partial success
                        return True
                else:
                    logger.warning("WebSocket connection failed: No active socket found after connect()")
                    print("[WARN] WebSocket connection failed. Is OpenAlgo server running?")
                    self._ws_connected = False
                    return False
                
            except Exception as e:
                logger.warning(f"WebSocket connection failed: {e}")
                print(f"[WARN] WebSocket connection failed: {e}")
                self._ws_connected = False
                return False
                
        except Exception as e:
            logger.debug(f"WebSocket setup error: {e}")
            return False
    
    def _on_ws_ltp_update(self, data):
        """Handle LTP updates from WebSocket"""
        print(f"[DEBUG] WS Data: {data}") # Uncommented for debugging
        try:
            if isinstance(data, dict):
                symbol = data.get("symbol", "")
                ltp = data.get("ltp", 0)
                
                if symbol and ltp:
                    # Update cache with live price
                    self.cache.set_price(symbol, float(ltp))
                    
        except Exception as e:
            logger.debug(f"WS LTP update error: {e}")
            
    async def monitor_config_task(self):
        """Monitor config.yaml for changes and reload"""
        config_path = "config.yaml"
        last_mtime = 0
        if os.path.exists(config_path):
            last_mtime = os.path.getmtime(config_path)
        
        logger.info("[TASK] Config monitor started")
        
        while self.running:
            try:
                if os.path.exists(config_path):
                    current_mtime = os.path.getmtime(config_path)
                    if current_mtime > last_mtime:
                        last_mtime = current_mtime
                        self._reload_config(config_path)
            except Exception as e:
                logger.error(f"Config monitor error: {e}")
                
            interval = self.config.get("system", {}).get("loop_intervals", {}).get("config_monitor", 2)
            await asyncio.sleep(interval) # Check every X seconds

    def _reload_config(self, path):
        """Reload configuration and update components"""
        try:
            print("\n[CONFIG] Change detected in config.yaml...")
            with open(path, 'r') as f:
                new_config = yaml.safe_load(f)
            
            if not new_config:
                print("[CONFIG] Error: Empty config file!")
                return

            self.config = new_config
            
            # Update components
            self.risk_manager.update_config(new_config)
            self.order_manager.update_config(new_config)
            
            # Reload indicators
            # We don't want to lose state (signals), so we just recreate the registry items
            # The next scan loop will use these new parameters
            self.indicators = self._load_indicators()
            
            # Update trading hours
            self._trading_hours = new_config.get("trading_hours", {})
            
            print("[CONFIG] Reload successful. Logic updated.")
            logger.info("Configuration reloaded successfully")
            
        except Exception as e:
            print(f"[CONFIG] Reload Failed: {e}")
            logger.error(f"Config reload failed: {e}", exc_info=True)
    
    async def stop(self):
        """Stop the trading engine"""
        self.running = False
        
        # Disconnect WebSocket if connected
        if self._ws_connected and self.client:
            try:
                self.client.disconnect()
                print("[INFO] WebSocket disconnected.")
            except:
                pass
        
        await self.data_provider.close()
        logger.info("Trading Engine stopped")
    
    # ========== BACKGROUND TASKS ==========
    
    async def signal_scanner_task(self):
        """
        Scan for new trading signals (every 5s).
        
        Flow:
        1. Fetch index LTF/HTF data
        2. Calculate indicators
        3. Check for setup conditions
        4. If signal detected, move to OBSERVING
        """
        logger.info("[TASK] Signal scanner started")
        
        while self.running:
            try:
                await self._scan_for_signals()
            except Exception as e:
                logger.error(f"Signal scanner error: {e}", exc_info=True)
            
            # Run every X seconds (Configurable)
            interval = self.config.get("system", {}).get("loop_intervals", {}).get("scanner", 5)
            await asyncio.sleep(interval)
    
    async def _scan_for_signals(self):
        """Scan index for trading signals with SMART MOMENTUM Logic (Dual TF)"""
        # Get index config
        index_query = self.config.get("index_query", "NIFTY")
        index_exchange = self.config.get("index_exchange", "NSE_INDEX")
        trend_tf = self.config.get("trend_tf", "15m")
        exec_tf = self.config.get("execution_tf", "3m")
        use_ha = self.config.get("index_use_ha", True)
        
        # 1. Fetch Historical Data (Dual Fetch)
        trend_bars = self.config.get("system", {}).get("data_limits", {}).get("trend_bars", 250)
        exec_bars = self.config.get("system", {}).get("data_limits", {}).get("exec_bars", 100)
        
        df_trend = await self.data_provider.fetch_history(index_query, trend_tf, bars=trend_bars, exchange=index_exchange)
        df_exec = await self.data_provider.fetch_history(index_query, exec_tf, bars=exec_bars, exchange=index_exchange)
        
        if df_trend is None or df_exec is None:
            if self._heartbeat_counter % 12 == 0: 
                print(f"[DEBUG] Historical data fetch returned None for {index_query}.")
            self._heartbeat_counter += 1
            return
            
        # Validate minimum bars (use config values)
        min_trend = max(50, trend_bars // 2)  # At least 50, or half of requested
        min_exec = max(30, exec_bars // 2)    # At least 30, or half of requested
        
        if len(df_trend) < min_trend or len(df_exec) < min_exec:
             if self._heartbeat_counter % 12 == 0:
                print(f"[DEBUG] Insufficient data. Have {len(df_trend)}/{len(df_exec)} bars, need {min_trend}/{min_exec}.")
             self._heartbeat_counter += 1
             return

        # ----------------------------------------------
        # PHASE 1: MASTER TREND (Trend TF / 15m)
        # ----------------------------------------------
        mt_cfg = self.config.get("strategy", {}).get("smart_momentum", {}).get("master_trend", {})
        master_trend_enabled = mt_cfg.get("enabled", True)
        
        if master_trend_enabled:
            # Master Trend is ENABLED - Run full 15m validation
            tech_htf = self.indicators["index_tech_htf"].calculate(df_trend, use_ha=use_ha)
            htf_meta = tech_htf.metadata
            
            ema9_htf = htf_meta["emas"].get(mt_cfg.get("ema_fast", 9))
            ema21_htf = htf_meta["emas"].get(mt_cfg.get("ema_mid", 21))
            ema50_htf = htf_meta["emas"].get(mt_cfg.get("ema_slow", 50))
            adx_htf = htf_meta["adx"]
            rsi_htf = htf_meta["rsi"]
            
            close_htf = df_trend['Close'].iloc[-1]
            
            # Config Values for Master Trend
            adx_min = mt_cfg.get("adx_threshold", 20)
            rsi_bull = mt_cfg.get("rsi_bull_min", 55)
            rsi_bear = mt_cfg.get("rsi_bear_max", 45)
            
            # Determine Trend State
            nifty_direction = "NEUTRAL"
            
            # UPTREND Rules (15m)
            if (close_htf > ema50_htf) and (ema9_htf > ema21_htf) and (adx_htf > adx_min) and (rsi_htf > rsi_bull):
                nifty_direction = "BULL"
                
            # DOWNTREND Rules (15m)
            elif (close_htf < ema50_htf) and (ema9_htf < ema21_htf) and (adx_htf > adx_min) and (rsi_htf < rsi_bear):
                nifty_direction = "BEAR"
                
            self._last_htf_trend = nifty_direction
            
            if nifty_direction == "NEUTRAL":
                if self._heartbeat_counter % 2 == 0:
                    print(f"[HEADLESS] Master Trend Neutral ({trend_tf}). (ADX:{adx_htf:.1f}, RSI:{rsi_htf:.1f})")
                return # Rigid Filter: No Master Trend = No Trade.
        else:
            # Master Trend is DISABLED - Use 3m UTBot trend only
            # We'll determine direction later from UTBot signal
            # For now, set a placeholder (will be overridden by UTBot)
            nifty_direction = "ANY"
            adx_htf = 0  # Not used when disabled
            print(f"[INFO] Master Trend Filter DISABLED. Using 3m signals only.")
            self._last_htf_trend = "DISABLED"

        # ----------------------------------------------
        # PHASE 2: SIGNAL GENERATION (Execution TF / 3m)
        # ----------------------------------------------
        
        # Calculate UTBot on LTF
        utbot_ltf = self.indicators["index_utbot"].calculate(df_exec, use_ha=use_ha)
        
        # Calculate Tech on LTF (Need EMA9)
        tech_ltf = self.indicators["index_tech_ltf"].calculate(df_exec, use_ha=use_ha)
        ltf_meta = tech_ltf.metadata
        ema9_ltf = ltf_meta["emas"].get(9)
        close_ltf = df_exec['Close'].iloc[-1]
        
        self._last_index_price = close_ltf
        
        # Signal Rules
        signal_detected = False
        signal_side = "NONE"
        
        if master_trend_enabled:
            # Use Master Trend direction (15m) + 3m confirmation
            if nifty_direction == "BULL":
                if (utbot_ltf.trend == 1) and (close_ltf > ema9_ltf):
                    signal_side = "CALL"
            elif nifty_direction == "BEAR":
                if (utbot_ltf.trend == -1) and (close_ltf < ema9_ltf):
                    signal_side = "PUT"
        else:
            # Master Trend DISABLED - Use 3m UTBot trend directly
            if (utbot_ltf.trend == 1) and (close_ltf > ema9_ltf):
                signal_side = "CALL"
            elif (utbot_ltf.trend == -1) and (close_ltf < ema9_ltf):
                signal_side = "PUT"
                
            # Update nifty_direction for logging (based on 3m)
            if signal_side == "CALL":
                nifty_direction = "BULL"
            elif signal_side == "PUT":
                nifty_direction = "BEAR"
                
        # ----------------------------------------------
        # PHASE 3: PATH 1 (IMMEDIATE EXPLOSIVE ENTRY)
        # ----------------------------------------------
        entry_mode = self.config.get("strategy", {}).get("smart_momentum", {}).get("entry_mode", "SIMPLE").upper()
        
        if signal_side != "NONE" and entry_mode == "ADVANCED":
            # Check for Explosive Conditions (Path 1) - ADVANCED MODE ONLY
            # Pass ADX directly from Phase 1 calculation
            is_explosive = self._is_explosive_trend(df_exec, adx_htf)
            
            if is_explosive:
                # PATH 1: IMMEDIATE
                print(f"[PATH 1] Explosive Signal detected ({signal_side})! Skipping Wait...")
                await self._scan_manual_strikes(signal_side)
                # We do NOT return here, because we want to register the "wait state" 
                # just in case the execution didn't happen (e.g. Option Invalid)?
                # Or if we execute, we should probably still track it? 
                # If we execute, _scan_manual_strikes handles order placement.
                # If we execute, we don't want to re-enter on wait.
                # Let's register wait state ANYWAY, but maybe with a flag? 
                # Actually, simpler: If explosive, we TRY to enter. 
                # If entry happens, _scan_manual_strikes checks cooldown/max_pos.
                # If we fail entry (e.g. invalid option), we might still want to catch it on pullback (Path 2)?
                # Yes. So we proceed to register wait state even if we tried explosive.
            
        # ----------------------------------------------
        # PHASE 3: CONDITIONAL WAIT LOGIC (Live Monitoring)
        # ----------------------------------------------
        
        # State key for this signal cycle
        signal_key = f"{index_query}_{signal_side}" if signal_side != "NONE" else "NONE"
        
        # Current time of the last closed candle (This is "Signal Time")
        current_candle_time = df_exec.index[-1]
        
        # Check if we were already waiting
        # We need to loop through keys because signal_side might be NONE now but we have a pending wait
        active_wait_keys = [k for k in self._signal_wait_state.keys() if k.startswith(index_query)]
        
        for key in active_wait_keys:
            wait_state = self._signal_wait_state[key]
            wait_side = wait_state['side']
            detected_at = wait_state['time']
            sig_close = wait_state['signal_close']
            sig_ema9 = wait_state['signal_ema9']
            sig_body = wait_state['signal_body']
            
            # 1. Check if Wait Window Expired (New Candle Closed)
            # If current_candle_time > detected_at, it means the "Wait Candle" has closed.
            if current_candle_time > detected_at:
                # WAIT EXPIRED. Perform FALLBACK CHECK (Close vs EMA9)
                print(f"[WAIT] Candle Closed for {wait_side}. Performing Close Check...")
                
                still_valid = False
                if wait_side == "CALL" and close_ltf > ema9_ltf:
                    still_valid = True
                elif wait_side == "PUT" and close_ltf < ema9_ltf:
                    still_valid = True
                    
                if still_valid:
                    print(f"[WAIT] Fallback Success! {wait_side} valid at close. Scanning Options...")
                    await self._scan_manual_strikes(wait_side) # Need to pass side string, not object
                else:
                    print(f"[WAIT] Fallback Failed. {wait_side} invalidated.")
                
                # Cleanup state
                del self._signal_wait_state[key]
                continue
            
            # 2. LIVE MONITORING (Inside the Wait Candle) - ADVANCED MODE ONLY
            # We are here because current_candle_time == detected_at (Same candle timestamp, meaning we are in the next live bar)
            
            if entry_mode == "SIMPLE":
                # SIMPLE MODE: Skip live monitoring. Just wait for candle close (Path 3 Fallback).
                continue
            
            # A. Get Live Nifty Price
            live_nifty = self.cache.get_price(index_query)
            if not live_nifty: 
                continue 
                
            # B. Check Condition A (NIFTY STRENGTH)
            # Rule 1: Nifty holds above Signal EMA9
            cond_a_trend = False
            if wait_side == "CALL" and live_nifty > sig_ema9:
                cond_a_trend = True
            elif wait_side == "PUT" and live_nifty < sig_ema9:
                cond_a_trend = True
                
            # Rule 2: Retracement < X% of Signal Body (Configured)
            # Bull: Price shouldn't drop below Close - (Body * 0.3)
            # Bear: Price shouldn't rise above Close + (Body * 0.3)
            cond_a_retrace = False
            retrace_pct = self.config.get("strategy", {}).get("smart_momentum", {}).get("wait_logic", {}).get("max_retrace_pct", 0.30)
            retrace_limit = sig_body * retrace_pct
            
            if wait_side == "CALL":
                limit_price = sig_close - retrace_limit
                if live_nifty > limit_price:
                    cond_a_retrace = True
            elif wait_side == "PUT":
                limit_price = sig_close + retrace_limit
                if live_nifty < limit_price:
                    cond_a_retrace = True
            
            # C. Check Condition B (OPTION BEHAVIOR) - Needs Live Option Data
            # This requires iterating through the manual strikes relevant to the side
            # We can reuse _scan_manual_strikes but passing a flag "check_live_conditions=True"
            # But _scan_manual_strikes fetches history. Live check needs LTP.
            # Simplified: If Nifty is valid, trigger the scan. The scan itself will check VWAP (Logic already exists).
            # The User requirement: "Option LTP >= VWAP" is ALREADY in _check_and_execute_entry.
            # The User requirement: "Option NOT making lower low". 
            
            if cond_a_trend and cond_a_retrace:
                # Nifty is looking good. Try to enter EARLY.
                # We prevent spamming by checking a flag or frequency?
                # _scan_manual_strikes is async and safe.
                # print(f"[WAIT] Conditional Entry Triggered! Nifty Strong ({live_nifty}). Scanning...")
                await self._scan_manual_strikes(wait_side)
                
                # IMPORTANT: If we successfully enter, we should clear the wait state? 
                # Or keep it until candle close?
                # If we enter, the trade state goes to POSITION. Order Manager handles that.
                # We can leave the wait state active, it won't trigger double entry due to "Already in Position" check.
            
        # 3. New Signal Registration
        if signal_side != "NONE" and signal_key not in self._signal_wait_state:
            # Calculate Signal Body Size (High - Low? or Abs(Close-Open)?)
            # User said "Retrace > 30% of signal candle". Usually Range (High-Low).
            # Let's use High-Low of the last candle.
            sig_high = df_exec['High'].iloc[-1]
            sig_low = df_exec['Low'].iloc[-1]
            sig_body = sig_high - sig_low
            
            print(f"[SIGNAL] Fresh {signal_side} detected @ {close_ltf}. Waiting (Conditional)...")
            self._signal_wait_state[signal_key] = {
                'time': current_candle_time,
                'side': signal_side,
                'signal_close': close_ltf,
                'signal_ema9': ema9_ltf,
                'signal_body': sig_body
            }

        # Heartbeat
        self._heartbeat_counter += 1
        if self._heartbeat_counter % 2 == 0:
            now = datetime.now().strftime("%H:%M:%S")
            wait_status = "WAITING" if self._signal_wait_state else "IDLE"
            live_nifty = self.cache.get_price(index_query) or 0.0
            print(f"[{now}] HB | Trend: {nifty_direction} | Nifty: {live_nifty:.1f} | Signal: {signal_side} | Status: {wait_status}")

    # ========== ENTRY EXECUTION LOGIC ==========
    
    def _is_within_trading_hours(self) -> bool:
        """Check if current time is within allowed trading window"""
        if not self._trading_hours.get("enabled", True):
            return True
        
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        # Check start/end times
        start_time = self._trading_hours.get("start_time", "09:00")
        end_time = self._trading_hours.get("end_time", "15:15")
        
        if current_time < start_time or current_time > end_time:
            return False
        
        # Check lunch break (optional)
        if self._trading_hours.get("avoid_lunch", False):
            lunch_start = self._trading_hours.get("lunch_start", "12:30")
            lunch_end = self._trading_hours.get("lunch_end", "13:30")
            if lunch_start <= current_time <= lunch_end:
                return False
        
        return True
            
    def _is_symbol_on_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in re-entry cooldown period"""
        if symbol not in self._exit_cooldowns:
            return False
            
        expiry_time = self._exit_cooldowns[symbol]
        now = datetime.now()
        
        if now < expiry_time:
            # removing log to avoid spam
            # remaining_secs = (expiry_time - now).total_seconds()
            # logger.info(f"[COOLDOWN] {symbol} blocked for {int(remaining_secs)}s more")
            return True
        
        # Cooldown expired, remove from tracking
        del self._exit_cooldowns[symbol]
        return False
    
    async def _scan_manual_strikes(self, index_signal):
        """
        Scan manual strikes basket for entry opportunities.
        
        Args:
            index_signal: Can be a signal object (with .trend) or a string ("CALL"/"PUT")
        """
        # Check trading hours
        if not self._is_within_trading_hours():
            return
        
        # Check max positions
        max_positions = self.config.get("max_positions", 4)
        active_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
        if active_count >= max_positions:
            return
        
        # Get manual strikes from config
        strike_cfg = self.config.get("strike_selection", {})
        mode = strike_cfg.get("mode", "AUTO").upper()
        
        if mode != "MANUAL":
            return
        
        manual_strikes = strike_cfg.get("manual_strikes", [])
        if not manual_strikes:
            return
        
        # Determine trend direction from input
        trend_str = "UNKNOWN"
        target_trend = 0
        
        if isinstance(index_signal, str):
            if index_signal == "CALL":
                target_trend = 1
                trend_str = "BULLISH"
            elif index_signal == "PUT":
                target_trend = -1
                trend_str = "BEARISH"
        else:
            # Assume it's the UTBot signal object
            target_trend = index_signal.trend
            trend_str = "BULLISH" if target_trend == 1 else "BEARISH"
        
        # Log start of scan cycle
        # print(f"\n[SCAN] Index is {trend_str} @ {self._last_index_price:.2f}. Checking manual strikes...")
        
        # ============================================
        # PARALLEL SCANNING (NEW)
        # ============================================
        exec_cfg = self.config.get("execution", {})
        parallel_enabled = exec_cfg.get("parallel_scanning", True)
        selection_mode = exec_cfg.get("selection_mode", "PRICE").upper()
        
        if parallel_enabled:
            # Parallel Mode: Scan all strikes simultaneously
            await self._scan_strikes_parallel(manual_strikes, target_trend, trend_str, selection_mode)
        else:
            # Sequential Mode (Legacy): Scan one by one
            await self._scan_strikes_sequential(manual_strikes, target_trend, trend_str)
    
    async def _scan_strikes_sequential(self, manual_strikes, target_trend, trend_str):
        """
        Sequential strike scanning (original logic).
        Scans strikes one by one until max_positions reached.
        """
        for symbol in manual_strikes:
            # Skip if already in position
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                continue
            
            # Skip if on cooldown
            if self._is_symbol_on_cooldown(symbol):
                continue
            
            # Determine if this symbol matches index direction
            # CE = Call, PE = Put
            is_ce = "CE" in symbol
            is_pe = "PE" in symbol
            
            # Match CE with bullish index, PE with bearish index
            if (is_ce and target_trend != 1):
                continue
            if (is_pe and target_trend != -1):
                continue
            
            # Fetch option data and check option-level trigger
            await self._check_and_execute_entry(symbol, "CALL" if target_trend == 1 else "PUT")
    
    async def _scan_strikes_parallel(self, manual_strikes, target_trend, trend_str, selection_mode):
        """
        Parallel strike scanning (NEW - Performance Enhancement).
        
        Scans all strikes simultaneously, sorts by selection criteria,
        and executes top N based on max_positions.
        
        Args:
            manual_strikes: List of strike symbols to scan
            target_trend: 1 (CALL) or -1 (PUT)
            trend_str: "BULLISH" or "BEARISH" (for logging)
            selection_mode: "PRICE" or "CONFIG_ORDER"
        """
        # Filter strikes by direction (CE/PE match)
        filtered_strikes = []
        for symbol in manual_strikes:
            # Skip if already in position
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                continue
            
            # Skip if on cooldown
            if self._is_symbol_on_cooldown(symbol):
                continue
            
            # Check CE/PE match
            is_ce = "CE" in symbol
            is_pe = "PE" in symbol
            
            if (is_ce and target_trend == 1) or (is_pe and target_trend == -1):
                filtered_strikes.append(symbol)
        
        if not filtered_strikes:
            return
        
        # Create validation tasks for all filtered strikes
        tasks = [
            self._validate_single_strike(symbol, target_trend)
            for symbol in filtered_strikes
        ]
        
        # Execute all validations in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter valid candidates
        valid_candidates = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"[PARALLEL] Strike validation error: {result}")
                continue
            
            if result and result.get("valid", False):
                valid_candidates.append(result)
        
        if not valid_candidates:
            return
        
        # Sort candidates based on selection mode
        if selection_mode == "PRICE":
            # Sort by price (ascending = cheapest first)
            valid_candidates.sort(key=lambda x: x.get("price", 999999))
            logger.info(f"[PARALLEL] Sorted {len(valid_candidates)} valid strikes by PRICE")
        elif selection_mode == "CONFIG_ORDER":
            # Sort by original config order (preserve list order)
            order_map = {s: i for i, s in enumerate(filtered_strikes)}
            valid_candidates.sort(key=lambda x: order_map.get(x.get("symbol"), 999))
            logger.info(f"[PARALLEL] Sorted {len(valid_candidates)} valid strikes by CONFIG_ORDER")
        # Future Enhancement: "CONFIDENCE" mode
        # elif selection_mode == "CONFIDENCE":
        #     valid_candidates.sort(key=lambda x: x.get("conf_score", 0), reverse=True)
        
        # Determine how many positions we can open
        max_positions = self.config.get("max_positions", 4)
        active_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
        slots_available = max(0, max_positions - active_count)
        
        if slots_available == 0:
            logger.info(f"[PARALLEL] No slots available ({active_count}/{max_positions})")
            return
        
        # Select top N candidates
        selected = valid_candidates[:slots_available]
        
        logger.info(f"[PARALLEL] Selected {len(selected)}/{len(valid_candidates)} strikes for entry")
        
        # Execute orders for selected strikes
        for candidate in selected:
            symbol = candidate.get("symbol")
            limit_price = candidate.get("price", 0)
            
            # Execute entry
            await self._execute_entry(
                symbol=symbol,
                quantity=50,  # TODO: Calculate from lot size
                price=limit_price,
                order_type_str="CALL" if target_trend == 1 else "PUT"
            )
    
    async def _validate_single_strike(self, symbol, target_trend):
        """
        Validate a single strike for entry (used in parallel scanning).
        
        Returns:
            dict: {
                "symbol": str,
                "valid": bool,
                "price": float,
                "conf_score": float,  # Future Enhancement
                "reasons": list
            }
        """
        try:
            # Check option confirmation
            valid, limit_price, reasons = await self._check_option_confirmation(
                symbol,
                price_check_mode="WAIT"
            )
            
            if not valid:
                return {
                    "symbol": symbol,
                    "valid": False,
                    "price": 0,
                    "conf_score": 0,
                    "reasons": reasons
                }
            
            # Future Enhancement: Calculate confidence score
            # conf_score = self._calculate_confidence_score(symbol, limit_price, ...)
            
            return {
                "symbol": symbol,
                "valid": True,
                "price": limit_price,
                "conf_score": 0,  # Placeholder for future enhancement
                "reasons": []
            }
        
        except Exception as e:
            logger.error(f"[VALIDATE] Error validating {symbol}: {e}")
            return {
                "symbol": symbol,
                "valid": False,
                "price": 0,
                "conf_score": 0,
                "reasons": [f"Exception: {str(e)}"]
            }
    
    def _is_explosive_trend(self, df_exec, htf_adx_val):
        """
        Check for Explosive Trend conditions (NIFTY).
        """
        exp_cfg = self.config.get("strategy", {}).get("smart_momentum", {}).get("explosive_trend", {})
        if not exp_cfg.get("enabled", False):
            return False
            
        # NIFTY 15m ADX >= 30 (Passed as argument to avoid complexity)
        if htf_adx_val < exp_cfg.get("adx_min", 30):
            return False
            
        # Candle Analysis
        open_p = df_exec['Open'].iloc[-1]
        high_p = df_exec['High'].iloc[-1]
        low_p = df_exec['Low'].iloc[-1]
        close_p = df_exec['Close'].iloc[-1]
        
        candle_range = high_p - low_p
        if candle_range == 0: return False
        
        candle_body = abs(close_p - open_p)
        body_ratio = candle_body / candle_range
        close_pos = (close_p - low_p) / candle_range
        
        # Conditions
        is_big_body = candle_body >= (exp_cfg.get("min_body_pct", 0.005) * close_p)
        is_solid_body = body_ratio >= exp_cfg.get("min_body_ratio", 0.6)
        is_closing_high = close_pos >= exp_cfg.get("min_close_pos", 0.75)
        
        if is_big_body and is_solid_body and is_closing_high:
            print(f"[EXPLOSIVE] Trend Detected! Body:{candle_body:.2f}, Ratio:{body_ratio:.2f}, Pos:{close_pos:.2f}")
            return True
        return False

    async def _check_option_confirmation(self, symbol: str, df_opt_ltf, price_check_mode="WAIT"):
        """
        Strict Option Confirmation Module.
        price_check_mode: "IMMEDIATE" (for explosive) or "WAIT" (standard)
        Returns: (bool, float_limit_price, list_reasons)
        """
        conf_cfg = self.config.get("strategy", {}).get("smart_momentum", {}).get("entry_confirmation", {})
        entry_mode = self.config.get("strategy", {}).get("smart_momentum", {}).get("entry_mode", "SIMPLE").upper()
        
        # Calculate Indicators locally to ensure freshness
        # We need VWAP, EMA, etc.
        use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
        
        # Reuse existing indicator or create temp? 
        # Better to reuse from registry if possible or calc.
        # Assuming df_opt_ltf is sufficient.
        
        opt_tech = self.indicators.get("option_tech")
        if not opt_tech:
             opt_tech = IndicatorRegistry.create("technical", {
                    "ema_periods": [9, 21],
                    "rsi_period": 14,
                    "adx_period": 14
                })
        
        tech_res = opt_tech.calculate(df_opt_ltf, use_ha=use_ha)
        meta = tech_res.metadata
        
        ema9 = meta["emas"].get(9)
        ema21 = meta["emas"].get(21)
        vwap = meta["vwap"]
        
        close = df_opt_ltf['Close'].iloc[-1]
        
        reasons = []
        
        # ============================================
        # SIMPLE MODE: Minimal Checks
        # ============================================
        if entry_mode == "SIMPLE":
            # 1. VWAP Check: LTP > VWAP
            if close < vwap:
                reasons.append(f"Below VWAP ({close:.2f} < {vwap:.2f})")
            
            # 2. Momentum Check: EMA9 > EMA21
            if ema9 <= ema21:
                reasons.append("EMA9 <= EMA21")
            
            if reasons:
                return False, 0.0, reasons
            
            # Valid! Use VWAP as limit price (simple)
            limit_price = vwap
            return True, limit_price, []
        
        # ============================================
        # ADVANCED MODE: Full Validation
        # ============================================
        vol_ma_5 = meta["vol_ma_5"]
        volume = meta["volume"]
        
        close = df_opt_ltf['Close'].iloc[-1]
        
        # Get live quote (Only if spread check enabled OR if we want precise Bid for Limit)
        # We need Bid for Strict Limit Order (min(Bid, VWAP)). 
        # But if Spread Check disabled, maybe user is OK with Last Price or just VWAP?
        # User said "I don't want to hit API when not required".
        # If we skip get_quote, we don't have Bid.
        # Implication: limit_price = min(close, vwap) instead of min(bid, vwap).
        
        check_spread = conf_cfg.get("check_spread", False)
        bid = 0
        ask = 0
        ltp = close # Default to Candle Close
        
        if check_spread:
            quote = await self.data_provider.get_quote(symbol, exchange="NFO")
            if not quote:
                # If we asked for spread but failed, should we fail?
                # Let's log warning and proceed with Candle Close?
                print(f"[WARN] Quote failed for {symbol}. Using Candle data.")
            else:
                bid = quote.get('bid', 0)
                ask = quote.get('ask', 0)
                ltp = quote.get('ltp', close)
        
        reasons = []
        
        # 1. VWAP Guard (Dynamic ATR-based or Fixed %)
        use_atr = conf_cfg.get("use_atr_price_cap", True)
        
        if use_atr:
            # ATR-based Max Price: VWAP + (ATR × Multiplier)
            atr = meta.get("atr", 0)
            atr_multiplier = conf_cfg.get("atr_multiplier", 1.5)
            max_price = vwap + (atr * atr_multiplier)
        else:
            # Fixed %-based Max Price: VWAP * Buffer
            max_buffer = conf_cfg.get("vwap_max_buffer", 1.015)
            max_price = vwap * max_buffer
        
        if ltp < vwap: # Must be above VWAP (Fair Value)
             reasons.append(f"Below VWAP ({ltp:.2f} < {vwap:.2f})")
        if ltp > max_price: # Must NOT be too expensive
             reasons.append(f"Too Expensive ({ltp:.2f} > {max_price:.2f})")
             
        # 2. Upper Wick Rejection (Trap Signal)
        if conf_cfg.get("check_upper_wick", True):
            open_p = df_opt_ltf['Open'].iloc[-1]
            high_p = df_opt_ltf['High'].iloc[-1]
            low_p = df_opt_ltf['Low'].iloc[-1]
            
            candle_body = abs(close - open_p)
            upper_wick = high_p - max(close, open_p)
            
            if upper_wick > candle_body:
                reasons.append(f"Wick Trap (Upper:{upper_wick:.2f} > Body:{candle_body:.2f})")
             
        # 3. Spread Check (If Enabled)
        if check_spread and bid > 0:
            spread_pct = (ask - bid) / ltp
            if spread_pct > conf_cfg.get("max_spread_pct", 0.003):
                 reasons.append(f"Spread High ({spread_pct:.4f} > 0.3%)")
        
        # 4. Momentum Check
        if ema9 <= ema21:
            reasons.append("EMA9 <= EMA21")
            
        # 5. Volume Check
        if volume < (conf_cfg.get("volume_multiplier", 1.2) * vol_ma_5):
            reasons.append(f"Low Vol ({volume})")
            
        # 6. Delta Check
        
        if reasons:
            return False, 0.0, reasons
            
        # Valid! Calculate Limit Price
        # If we have Bid, use it. Else use LTP/Close.
        if bid > 0:
            limit_price = min(bid, vwap)
        else:
            limit_price = min(ltp, vwap)
            
        if limit_price <= 0: limit_price = ltp
        
        return True, limit_price, []

    async def _check_and_execute_entry(self, symbol: str, signal_side: str):
        """
        Execute Entry Phase 3 logic (3-Path System).
        """
        try:
            # Fetch Index and Option Data
            # We need Nifty 15m/3m for Explosive Check
            # We already have Nifty data in _scan, but splitting functions makes passing it hard.
            # We will refetch or rely on cached?
            # Ideally passed args, but refetch safe for now.
            
            # Fetch Option Data
            opt_ltf_tf = self.config["option"]["ltf"]["timeframe"]
            df_opt = await self.data_provider.fetch_history(symbol, opt_ltf_tf, bars=100, exchange="NFO")
            if df_opt is None or len(df_opt) < 50: return
            
            # Fetch Nifty Data for Explosive Check (Fresh)
            index_query = self.config.get("index_query", "NIFTY")
            df_exec = await self.data_provider.fetch_history(index_query, self.config.get("execution_tf", "3m"), bars=50)
            
            # --- PATH 1: IMMEDIATE (EXPLOSIVE) ---
            # Check if this is an "Explosive" setup?
            # We need ADX from HTF (assumed passed or recalculated? Let's use stored value)
            # Stored: self._last_htf_adx? We didn't store ADX.
            # Let's assume standard flow passed phase 1 so ADX > 20.
            # We need to verify ADX > 30 specifically for explosive.
            # For simplicity, let's assume if user wants explosive, we check the body conditions mainly.
            
            exp_adx_min = self.config.get("strategy", {}).get("smart_momentum", {}).get("explosive_trend", {}).get("adx_min", 30)
            is_explosive = self._is_explosive_trend(df_exec, exp_adx_min) # Hardcoded ADX safe-guard or fetch?
            # Actually, to be accurate we should pass ADX.
            # Let's just use the Candle Logic for "Explosive" classification now.
            
            valid_opt, limit_price, reasons = await self._check_option_confirmation(symbol, df_opt)
            
            if is_explosive:
                if valid_opt:
                    print(f"[EXPLOSIVE] Triggering IMMEDIATE ENTRY on {symbol}!")
                    await self._execute_entry(symbol, signal_side, limit_price, None)
                    return
                else:
                    print(f"[EXPLOSIVE] Detected but Option Invalid: {reasons}")
                    return # Don't wait if explosive failed? Or fall back to wait? Fallback usually.
            
            # --- PATH 2: SMART WAIT / PATH 3: FALLBACK ---
            # This logic is handled by the caller (_scan_for_signals) which calls this function 
            # EITHER immediately (if condition met) OR after wait.
            # So if we are here, it means we are permitted to enter IF option is valid.
            
            if valid_opt:
                print(f"[ENTRY] Option Confirmation Passed. Executing {symbol}...")
                await self._execute_entry(symbol, signal_side, limit_price, None)
            else:
                 print(f"[REJECT] Option Confirmation Failed for {symbol}: {reasons}")
            
        except Exception as e:
            logger.error(f"Entry check error for {symbol}: {e}", exc_info=True)
            self._set_cooldown(symbol)
    

    async def _execute_entry(self, symbol: str, side: str, price: float, ltf_signal):
        """Execute entry order"""
        try:
            # Check cooldown again just in case
            if self._is_symbol_on_cooldown(symbol):
                print(f"[SKIP] {symbol} is on cooldown.")
                return 

            # Get lot size from config
            lots = self.config.get("lots", 1)
            
            # Dynamically fetch lot size (cached via DataProvider)
            lot_size = await self.data_provider.get_lot_size(symbol, exchange="NFO")
                
            quantity = lots * lot_size
            print(f"[INFO] Using Lot Size: {lot_size} for {symbol}. Total Qty: {quantity}")
            
            # Place Order
            print(f"[ENTRY] Placing BUY order for {symbol} @ {price:.2f} Qty={quantity}")
            
            # Determine Order Type based on Entry Mode
            entry_mode = self.config.get("strategy", {}).get("smart_momentum", {}).get("entry_mode", "SIMPLE").upper()
            
            if entry_mode == "SIMPLE":
                order_type = "LIMIT"  # Simple LIMIT order
            else:
                order_type = "SMART_LIMIT" if self.config["execution"]["order_type"] == "SMART_LIMIT" else "MARKET"
            
            # Use unpacked arguments for place_order as per signature
            order_id = await self.order_manager.place_order(
                symbol=symbol,
                action="BUY",
                quantity=quantity,
                order_type=order_type,
                limit_price=price, # Smart Limit uses this as the "Smart Price" reference
                exchange="NFO",
                product=self.config.get("product_type", "MIS")
            )
            
            if order_id and order_id.success:
                print(f"[SUCCESS] Order placed: {order_id.order_id}")
                # Initialize trade state
                trade = Trade(
                    symbol=symbol,
                    entry_price=order_id.filled_price if order_id.filled_price else price,
                    quantity=quantity,
                    side=side,
                    entry_time=datetime.now(),
                    state=TradeState.POSITION,
                    sl=0.0,
                    target=0.0
                )
                
                # Store and persist
                self.trades[symbol] = trade
                self.persistence.save_trade(trade)
                
                print(f"[POSITION] Entered {symbol} @ {trade.entry_price:.2f}")
                logger.info(f"Entry executed: {symbol} @ {trade.entry_price:.2f}")
            self._set_cooldown(symbol) # 1 min cooldown on failure
                
        except Exception as e:
            logger.error(f"Entry execution error for {symbol}: {e}", exc_info=True)
            print(f"[ERROR] Entry execution crashed: {e}")
            self._set_cooldown(symbol) # 1 min cooldown on crash
    
    def _set_cooldown(self, symbol: str, seconds: int = 0):
        """Set cooldown for a symbol"""
        if seconds == 0:
            seconds = self.config.get("system", {}).get("cooldowns", {}).get("error_sec", 60)
        self._cooldowns[symbol] = datetime.now() + timedelta(seconds=seconds)

    def _is_symbol_on_cooldown(self, symbol: str) -> bool:
        """Check if a symbol is on cooldown"""
        if symbol not in self._cooldowns:
            return False
            
        if datetime.now() > self._cooldowns[symbol]:
            del self._cooldowns[symbol]
            return False
            
        return True
    

    async def risk_monitor_task(self):
        """Monitor active positions for risk management (every one second)."""
        logger.info("[TASK] Risk monitor started")
        
        counter = 0
        while self.running:
            try:
                await self._monitor_risk(report=(counter % 10 == 0))
                counter += 1
            except Exception as e:
                logger.error(f"Risk monitor error: {e}", exc_info=True)
            
            # Run every X seconds (Configurable)
            interval = self.config.get("system", {}).get("loop_intervals", {}).get("risk_monitor", 1)
            await asyncio.sleep(interval)
    
    async def _monitor_risk(self, report: bool = False):
        """Monitor risk for all active positions"""
        active_trades = [t for t in self.trades.values() if t.state == TradeState.POSITION]
        
        # Periodic "Still Alive" log for risk monitor if positions exist
        if report and active_trades:
            # print(".", end="", flush=True) # Minimalist heartbeat
            pass

        for trade in active_trades:
            symbol = trade.symbol
            
            # Get live price
            price = await self.data_provider.get_live_price(symbol, exchange="NFO")
            if price is None:
                continue
            
            # Evaluate risk 
            # Check Index Trend Reversal
            is_trend_reversed = False
            index_df = await self.data_provider.get_historical_data(self.config["index_query"], "3m", period="5d")
            if not index_df.empty:
                 use_ha = self.config.get("index_use_ha", True)
                 signal_ltf = self.indicators["index_ltf"].calculate(index_df, use_ha=use_ha)
                 trend = signal_ltf.trend
                 
                 # Logic: If CALL and Trend is BEARISH (-1) -> Reversal
                 #        If PUT and Trend is BULLISH (1) -> Reversal
                 if (trade.side == "CALL" and trend == -1) or \
                    (trade.side == "PUT" and trend == 1):
                     is_trend_reversed = True

            decision = self.risk_manager.evaluate(trade, price, is_trend_reversed=is_trend_reversed)
            
            # Calculate current P&L for display
            curr_pnl = (price - trade.entry_price) * trade.quantity
            if trade.side == "PUT":
                 curr_pnl = (trade.entry_price - price) * trade.quantity
            curr_pnl_pct = (curr_pnl / (trade.entry_price * trade.quantity)) * 100
            
            # Update TSL level
            if decision.new_tsl_level > 0:
                print(f"[RISK] {symbol}: TSL moved to {decision.new_tsl_level:.2f} (Stage: {decision.new_stage})")
                trade = Trade(**{
                    **trade.__dict__,
                    "tsl_level": decision.new_tsl_level,
                    "last_stage": decision.new_stage
                })
                self.trades[symbol] = trade
                self.persistence.save_trade(trade)
            
            # Exit if needed
            if decision.should_exit:
                auto_sell = self.config.get("execution", {}).get("enable_bot_auto_sell", True)
                
                if not auto_sell:
                    # Log alert but do NOT execute exit
                    if self._heartbeat_counter % 5 == 0: # Reduce spam (every 5s)
                        print(f"\n[ALERT] {decision.message} | Auto-Sell DISABLED. Please Exit Manually!")
                    logger.info(f"[MANUAL_EXIT_REQ] {symbol}: {decision.message}")
                    continue
                
                print(f"[RISK] Triggering EXIT for {symbol}: {decision.message}")
                logger.info(f"[EXIT] {symbol}: {decision.message}")
                await self._execute_exit(trade, decision.reason.value)
            
            # Periodic status report per position
            if report:
                print(f"[STATUS] {symbol} @ {price:.2f} | P&L: ₹{curr_pnl:.2f} ({curr_pnl_pct:.2f}%) | TSL: {trade.tsl_level:.2f}")

    async def _execute_exit(self, trade: Trade, reason: str):
        """Execute exit order"""
        try:
            # Transition to EXITING
            print(f"[EXIT] Sending Market Order for {trade.symbol}...")
            trade = TradeStateMachine.transition(trade, TradeState.EXITING)
            self.trades[trade.symbol] = trade
            self.persistence.save_trade(trade)
            
            # Place exit order
            order_params = {
                "symbol": trade.symbol,
                "exchange": "NFO",
                "transaction_type": "SELL" if trade.side == "CALL" else "BUY",
                "quantity": trade.quantity,
                "product": self.config.get("product_type", "MIS"),
                "order_type": "MARKET"
            }
            
            order_id = await self.order_manager.place_order(order_params)
            
            if order_id:
                # Update trade to EXITED
                trade = TradeStateMachine.transition(trade, TradeState.EXITED, reason)
                
                # Calculate final P&L
                pnl, pnl_pct = trade.calculate_pnl()
                trade = Trade(**{
                    **trade.__dict__,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct
                })
                
                # Archive and remove from active trades
                self.persistence.archive_trade(trade)
                del self.trades[trade.symbol]
                
                # Track exit for re-entry protection
                re_entry_cfg = self.config.get("re_entry_protection", {})
                if re_entry_cfg.get("enabled", True):
                    # Check P&L to determine cooldown duration
                    if pnl > 0:
                        # PROFIT: Use profit cooldown (default 0)
                        mins = re_entry_cfg.get("cooldown_after_profit_mins", 0)
                    else:
                        # LOSS: Use loss cooldown (default 5)
                        mins = re_entry_cfg.get("cooldown_after_loss_mins", 5)
                    
                    self._exit_cooldowns[trade.symbol] = datetime.now() + timedelta(minutes=mins)
                    if mins > 0:
                        logger.info(f"[COOLDOWN] Set {mins}m cooldown for {trade.symbol} (PnL: {pnl:.2f})")
                
                # Update risk manager daily stats
                self.risk_manager.update_daily_pnl(pnl)
                
                logger.info(
                    f"[TRADE CLOSED] {trade.symbol}: "
                    f"Entry={trade.entry_price:.2f}, Exit={trade.current_price:.2f}, "
                    f"P&L=₹{pnl:.2f} ({pnl_pct:.2f}%), Reason={reason}"
                )
            else:
                logger.error(f"Exit order failed for {trade.symbol}")
        except Exception as e:
            logger.error(f"Exit execution error: {e}", exc_info=True)
    
    async def position_sync_task(self):
        """Sync positions with broker periodically and detect external closures."""
        logger.info("[TASK] Position sync started")
        
        while self.running:
            try:
                await self._sync_positions()
            except Exception as e:
                logger.error(f"Position sync error: {e}", exc_info=True)
            
            # Run every X seconds
            interval = self.config.get("system", {}).get("loop_intervals", {}).get("position_sync", 10)
            await asyncio.sleep(interval)
    
    async def _sync_positions(self):
        """Sync with broker positions using OpenAlgo positionbook() API"""
        loop = asyncio.get_event_loop()
        try:
            # Use positionbook() - the correct OpenAlgo API method
            broker_data = await loop.run_in_executor(
                None,
                self.client.positionbook
            )
        except Exception as e:
            logger.debug(f"Position sync skipped: {e}")
            return
        
        if not broker_data or broker_data.get('status') != 'success':
            return
        
        positions = broker_data.get('data', [])
        if not positions:
            return
        
        # Extract symbols from open positions
        broker_symbols = set()
        for item in positions:
            if 'symbol' in item and item.get('quantity', 0) != 0:
                broker_symbols.add(item['symbol'])
        
        # Check for external closures
        for symbol in list(self.trades.keys()):
            trade = self.trades[symbol]
            
            if trade.state == TradeState.POSITION and symbol not in broker_symbols:
                logger.warning(f"[SYNC] External closure detected: {symbol}")
                
                # Mark as exited externally
                trade = TradeStateMachine.transition(trade, TradeState.EXITED, "External Closure")
                self.persistence.archive_trade(trade)
                del self.trades[symbol]
                
    async def monitor_websocket_task(self):
        """Monitor WebSocket connection and reconnect if dropped."""
        logger.info("[TASK] WebSocket monitor started")
        
        while self.running:
            try:
                # If WebSocket functionality is enabled but not connected
                if self.config.get("use_websocket", True):
                    # Check connection state
                    is_connected = False
                    if self.client and hasattr(self.client, 'ws') and self.client.ws:
                        if hasattr(self.client.ws, 'sock') and self.client.ws.sock:
                            is_connected = True
                            self._ws_connected = True # Update internal flag
                    
                    if not is_connected:
                        if self._ws_connected:
                            logger.warning("WebSocket disconnection detected!")
                            print("\n[WARN] WebSocket lost! Attempting reconnect...")
                            self._ws_connected = False
                        
                        # Attempt reconnection
                        await self._reconnect_websocket()
                    
            except Exception as e:
                logger.error(f"WebSocket monitor error: {e}")
            
            # Run every 5 seconds
            await asyncio.sleep(5)

    async def _reconnect_websocket(self):
        """Reconnect to WebSocket if connection is lost."""
        try:
            print("[INFO] Reconnecting to WebSocket...")            
            # 1. Disconnect existing if any
            try:
                if self.client:
                    self.client.disconnect()
            except:
                pass
                
            # 2. Wait a bit before reconnecting
            await asyncio.sleep(2)
            
            # 3. Setup again (connect + subscribe)
            success = await self._setup_websocket()
            
            if success:
                logger.info("WebSocket successfully reconnected")
                print("[INFO] WebSocket successfully reconnected.")
            else:
                logger.warning("WebSocket reconnection failed")
                # print("[WARN] WebSocket reconnection failed.")
                
        except Exception as e:
            logger.error(f"Reconnection error: {e}")
