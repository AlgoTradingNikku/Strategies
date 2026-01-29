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
from asyncio import Lock
import re
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging
import time
import os
import threading
import yaml

from core.state_machine import Trade, TradeState, TradeStateMachine
from core.persistence import TradePersistence
from indicators.registry import IndicatorRegistry
from risk.manager import RiskManager, ExitReason
from execution.order_manager import OrderManager
from data.provider import MarketDataProvider
from data.cache import MarketDataCache
from utils import ConfigValidator, CircuitBreaker, ThreadSafeFileWriter, format_error_message


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
        self.data_provider = MarketDataProvider(api_client, self.cache, self.config)
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
        self._cooldown_lock = threading.RLock()  # Thread-safe lock for cooldown dict
        
        # Trading hours config
        self._trading_hours = config.get("trading_hours", {})

        # Strike Status Tracking (for logging)
        self._strike_states = {}
        
        # Entry lock to prevent race conditions in max_positions check
        self._entry_lock = Lock()
        
        # Signal wait state tracking (for conditional wait logic)
        self._signal_wait_state = {}
        
        # Last UTBot state and rejection reasons for each strike (for verbose logging)
        self._last_utbot_state = {}
        self._last_reject_reasons = {}
        
        # BUG FIX #8: Validate manual strikes format
        self._validate_and_load_strikes()
        
        # Restore state from database
        self._restore_state()
        
        print("\n[INFO] Risk Worker (Bodyguard) started.")
        print("[INFO] Scanner Worker (The Brain) started.")
        logger.info("Trading Engine initialized")
    
    def _validate_and_load_strikes(self):
        """Validate manual strikes and warn about invalid formats"""
        strike_cfg = self.config.get("strike_selection", {})
        mode = strike_cfg.get("mode", "AUTO").upper()
        
        if mode != "MANUAL":
            return
        
        manual_strikes = strike_cfg.get("manual_strikes", [])
        if not manual_strikes:
            logger.warning("MANUAL mode enabled but no manual_strikes configured")
            return
        
        # Validate format: SYMBOL[DD][MMM][YY][STRIKE][CE/PE]
        # Examples: NIFTY27JAN2625000CE, BANKNIFTY27JAN2650000PE
        valid_pattern = re.compile(r'^[A-Z]+\d{2}[A-Z]{3}\d{2}\d+[CP]E$')
        
        valid_strikes = []
        invalid_strikes = []
        
        for strike in manual_strikes:
            if valid_pattern.match(strike):
                valid_strikes.append(strike)
            else:
                invalid_strikes.append(strike)
        
        if invalid_strikes:
            print("\n" + "="*60)
            print("⚠️  WARNING: Invalid Strike Symbols Detected")
            print("="*60)
            for strike in invalid_strikes:
                print(f"  ❌ {strike}")
                logger.warning(f"Invalid strike format (skipped): {strike}")
            print("\nExpected format: SYMBOL[DD][MMM][YY][STRIKE][CE/PE]")
            print("Example: NIFTY27JAN2625000CE or BANKNIFTY27JAN2650000PE")
            print("="*60 + "\n")
        
        if not valid_strikes:
            error_msg = "No valid manual strikes found! Cannot proceed."
            logger.critical(error_msg)
            raise ValueError(error_msg)
        
        if valid_strikes and invalid_strikes:
            print(f"✅ Loaded {len(valid_strikes)} valid strikes (ignored {len(invalid_strikes)} invalid)\n")
            logger.info(f"Loaded {len(valid_strikes)} valid strikes, ignored {len(invalid_strikes)} invalid")
    
    def _load_indicators(self) -> dict:
        """Load indicators from config - Option-Centric mode"""
        indicators = {}
        
        # --- OPTION INDICATORS (Primary - Used for signals) ---
        opt_ltf_config = self.config.get("option", {}).get("ltf", {})
        
        # Option UTBot (for signal generation)
        indicators["option_utbot"] = IndicatorRegistry.create("utbot", {
            "sensitivity": opt_ltf_config.get("sensitivity", 1.0),
            "atr_period": opt_ltf_config.get("atr", 10)
        })
        
        # Option Technicals (for entry conditions: EMA, RSI, ADX, VWAP)
        entry_cfg = self.config.get("entry_conditions", {})
        indicators["option_tech"] = IndicatorRegistry.create("technical", {
            "ema_periods": [entry_cfg.get("ema_fast", 9), entry_cfg.get("ema_slow", 20)],
            "rsi_period": entry_cfg.get("rsi_period", 14),
            "adx_period": entry_cfg.get("adx_period", 14),
            "vol_avg_period": entry_cfg.get("vol_avg_period", 5)
        })
        
        return indicators
    
    def _restore_state(self):
        """Restore trades from SQLite on startup (crash recovery)"""
        active_trades = self.persistence.load_active_trades()
        
        for trade in active_trades:
            self.trades[trade.symbol] = trade
            
            # FIX: If trade is stuck in EXITING (e.g. crash during exit), revert to POSITION
            # so it gets picked up by Risk Manager again.
            if trade.state == TradeState.EXITING:
                print(f"[RECOVERY] Found stuck EXITING trade {trade.symbol}. Reverting to POSITION.")
                
                # Manually set state back to POSITION (using transition if allowed, or direct update)
                # We use transition() because EXITING->POSITION is a valid 'retry' transition
                trade = TradeStateMachine.transition(trade, TradeState.POSITION)
                self.trades[trade.symbol] = trade # Update memory
                self.persistence.save_trade(trade) # Update DB
                
            logger.info(
                f"[RECOVERY] Restored {trade.symbol} in {trade.state.name} "
                f"@ INR {trade.entry_price} (P&L: {trade.pnl_pct:.2f}%)"
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
        
        # Initial Position Sync (Vital to detect existing positions)
        print("[INFO] Performing initial position sync...")
        await self._sync_positions()
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
        # print(f"[DEBUG] WS Data: {data}")  # Uncomment to debug WebSocket data
        try:
            if isinstance(data, dict):
                symbol = data.get("symbol", "")
                
                # Extract LTP - check if it's nested in 'data' key
                ltp = data.get("ltp", 0)
                if "data" in data and isinstance(data["data"], dict):
                    ltp = data["data"].get("ltp", ltp)
                
                if symbol and ltp:
                    # Update cache with live price
                    self.cache.set_price(symbol, float(ltp))
                    
                    # Special case for Index symbol mismatch (e.g. 'Nifty 50' vs 'NIFTY')
                    index_query = self.config.get("index_query", "NIFTY")
                    if symbol.upper().replace(" ", "") == index_query.upper().replace(" ", ""):
                        if symbol != index_query:
                            self.cache.set_price(index_query, float(ltp))
                    
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
        """Reload configuration and update components with validation"""
        try:
            print("\n[CONFIG] Change detected in config.yaml...")
            with open(path, 'r') as f:
                new_config = yaml.safe_load(f)
            
            if not new_config:
                print("[CONFIG] Error: Empty config file!")
                return
            
            # ENHANCEMENT #1: Validate configuration before applying
            validator = ConfigValidator()
            is_valid, errors = validator.validate(new_config)
            
            if not is_valid:
                print("[CONFIG] Validation FAILED! Errors:")
                for error in errors[:10]:  # Show first 10 errors
                    print(f"  ❌ {error}")
                if len(errors) > 10:
                    print(f"  ... and {len(errors) - 10} more errors")
                print("[CONFIG] Config NOT applied. Please fix errors and save again.")
                logger.error(f"Config validation failed: {errors}")
                return
            
            print("[CONFIG] Validation passed ✓")
            
            # Check if manual strikes changed (for WebSocket resubscription)
            old_strikes = set(self.config.get("strike_selection", {}).get("manual_strikes", []))
            new_strikes = set(new_config.get("strike_selection", {}).get("manual_strikes", []))
            strikes_changed = (old_strikes != new_strikes)

            # Apply new config
            self.config = new_config
            
            # Update components
            self.risk_manager.update_config(new_config)
            self.order_manager.update_config(new_config)
            self.data_provider.config = new_config  # Update data provider config
            
            # Reload indicators
            # We don't want to lose state (signals), so we just recreate the registry items
            # The next scan loop will use these new parameters
            self.indicators = self._load_indicators()
            
            # Update trading hours
            self._trading_hours = new_config.get("trading_hours", {})
            
            # ENHANCEMENT #2: Resubscribe WebSocket if strikes changed
            if strikes_changed and self.config.get("use_websocket", True):
                print("[CONFIG] Manual strikes changed. Resubscribing WebSocket...")
                # Schedule async resubscription
                asyncio.create_task(self._resubscribe_websocket())
            
            print("[CONFIG] Reload successful. Logic updated.")
            logger.info("Configuration reloaded successfully")
            
        except Exception as e:
            error_msg = format_error_message(e, "Config reload")
            print(f"[CONFIG] Reload Failed: {error_msg}")
            logger.error(f"Config reload failed: {e}", exc_info=True)
    
    async def _resubscribe_websocket(self):
        """Resubscribe WebSocket to updated symbol list"""
        try:
            if not self._ws_connected:
                logger.info("WebSocket not connected, skipping resubscription")
                return
            
            # Disconnect and reconnect with new symbols
            if self.client and hasattr(self.client, 'ws'):
                try:
                    self.client.disconnect()
                except Exception as e:
                    logger.debug(f"Disconnect error (expected): {e}")
            
            self._ws_connected = False
            self._ws_subscribed_symbols = []
            
            # Wait briefly
            await asyncio.sleep(1)
            
            # Reconnect with new symbol list
            success = await self._setup_websocket()
            
            if success:
                print("[CONFIG] WebSocket resubscribed successfully")
                logger.info("WebSocket resubscribed to updated symbols")
            else:
                print("[CONFIG] WebSocket resubscription failed")
                logger.warning("WebSocket resubscription failed")
                
        except Exception as e:
            error_msg = format_error_message(e, "WebSocket resubscription")
            logger.error(error_msg)
    
    async def stop(self):
        """Stop the trading engine"""
        self.running = False
        
        # Disconnect WebSocket if connected
        if self._ws_connected and self.client:
            try:
                self.client.disconnect()
                print("[INFO] WebSocket disconnected.")
            except Exception as e:
                logger.debug(f"WebSocket disconnect cleanup: {e}")
        
        await self.data_provider.close()
        logger.info("Trading Engine stopped")
    
    # ========== BACKGROUND TASKS ==========
    
    async def signal_scanner_task(self):
        """
        Scan for new trading signals (every 5s).
        
        Flow:
        1. Fetch option chart data for manual strikes
        2. Calculate indicators on option data
        3. Check for UTBot setup / Entry conditions
        4. If signal detected and valid, execute entry
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
        """
        Option-Centric Signal Scanner.
        
        Scans each strike in manual_strikes for UTBot buy signals on Option chart.
        When signal detected, validates using entry_conditions before executing.
        """
        # Get manual strikes from config
        strike_cfg = self.config.get("strike_selection", {})
        mode = strike_cfg.get("mode", "AUTO").upper()
        
        if mode != "MANUAL":
            # AUTO mode not implemented in Option-Centric version
            if self._heartbeat_counter % 12 == 0:
                print("[WARN] Option-Centric mode requires MANUAL strike selection.")
            self._heartbeat_counter += 1
            return
        
        manual_strikes = strike_cfg.get("manual_strikes", [])
        if not manual_strikes:
            if self._heartbeat_counter % 12 == 0:
                print("[WARN] No manual_strikes configured.")
            self._heartbeat_counter += 1
            return
        
        # Check trading hours
        if not self._is_within_trading_hours():
            return
        
        # BUG FIX #1: Check max_positions with lock BEFORE parallel scanning
        # This prevents race condition where multiple strikes pass the check simultaneously
        async with self._entry_lock:
            max_positions = self.config.get("max_positions", 4)
            active_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
            if active_count >= max_positions:
                return  # Exit early before processing any strikes
        
        # Get timeframe and bars config
        opt_ltf_tf = self.config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
        exec_bars = self.config.get("system", {}).get("data_limits", {}).get("exec_bars", 50)
        use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
        
        # Check parallel scanning mode
        exec_cfg = self.config.get("execution", {})
        parallel_enabled = exec_cfg.get("parallel_scanning", True)
        
        # OPTIMIZATION B2: Filter strikes to scan (skip those on cooldown only)
        # We still need data for active positions (for indicator display in heartbeat)
        strikes_to_scan = []
        strikes_in_position = []
        skipped_count = 0
        
        for symbol in manual_strikes:
            # Track if already in position (still need data for display)
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                strikes_in_position.append(symbol)
                strikes_to_scan.append(symbol)  # Still fetch data for indicators
                continue
            
            # Skip if on cooldown (no need to fetch data)
            if self._is_symbol_on_cooldown(symbol):
                skipped_count += 1
                continue
            
            strikes_to_scan.append(symbol)
        
        # Log optimization impact (debug)
        if skipped_count > 0 and self._heartbeat_counter % 12 == 0:
            logger.debug(f"[OPTIMIZATION] Skipped fetching data for {skipped_count}/{len(manual_strikes)} strikes (in position or cooldown)")
        
        if parallel_enabled and len(strikes_to_scan) > 0:
            # ========== OPTIMIZED PARALLEL FETCH ==========
            # 1. Define fetch task
            async def fetch_one(symbol):
                try:
                    df = await self.data_provider.fetch_history(
                        symbol, opt_ltf_tf, bars=exec_bars, exchange="NFO"
                    )
                    return (symbol, df)
                except Exception:
                    return (symbol, None)

            # 2. Fetch all concurrently (with timeout to prevent hang)
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*[fetch_one(s) for s in strikes_to_scan]),
                    timeout=10
                )
            except asyncio.TimeoutError:
                print("[WARN] Data fetch timed out (OpenAlgo API slow?). Skipping cycle.")
                return
            data_map = {sym: df for sym, df in results if df is not None and not df.empty}
            
            # DEBUG: Log what's in data_map
            if self._heartbeat_counter % 6 == 0:
                debug_info = [f"{s}:{df['Close'].iloc[-1]:.1f}" for s, df in data_map.items()]
                # print(f"[DEBUG] DataMap Content: {', '.join(debug_info)}")

            # ========== HEARTBEAT (With Shared Data) ==========
            self._heartbeat_counter += 1
            if self._heartbeat_counter % 2 == 0:
                now = datetime.now().strftime("%H:%M:%S")
                active_symbols = [t.symbol for t in self.trades.values() if t.state == TradeState.POSITION]
                
                # Get Nifty price
                index_query = self.config.get("index_query", "NIFTY")
                index_exchange = self.config.get("index_exchange", "NSE_INDEX")
                nifty_price = self.cache.get_price(index_query) or 0.0
                
                # Fallback to API quote
                if nifty_price == 0.0 and self.client:
                    try:
                        quote = self.client.quotes(symbol=index_query, exchange=index_exchange)
                        if quote and isinstance(quote, dict) and quote.get("status") == "success":
                            nifty_price = quote.get("data", {}).get("ltp", 0.0)
                            self.cache.set_price(index_query, nifty_price)
                    except Exception:
                        pass
                
                print(f"[{now}] HB | NIFTY: {nifty_price:.1f} | Strikes: {len(manual_strikes)} | Active: {len(active_symbols)}")
                
                # Show details for active positions using SHARED DATA
                if active_symbols:
                    await self._print_active_trade_details(active_symbols, data_map)
                
                elif not active_symbols:
                    # Verbose Scan Summary code...
                    scan_summary = []
                    # ... (rest of summary code is fine, it uses cache prices)
                    for symbol in manual_strikes:
                        # Get price: Priority 1 = Fetched Data, Priority 2 = Cache
                        strike_price = 0.0
                        if symbol in data_map:
                            strike_price = data_map[symbol]['Close'].iloc[-1]
                            self.cache.set_price(symbol, strike_price) # Update cache
                        else:
                            # If not in data_map (e.g. cooldown), check cache
                            strike_price = self.cache.get_price(symbol) or 0.0
                            # Log reason for 0.0 if symbol definitely in manual_strikes
                            if strike_price == 0 and symbol in manual_strikes:
                                pass # This happens on first run or after errors

                        state_emoji = "[ -- ]"
                        rejection_suffix = ""
                        
                        if symbol in self._last_utbot_state:
                            trend = self._last_utbot_state[symbol]
                            if trend == 1: state_emoji = "[BULL]"
                            elif trend == -1: state_emoji = "[BEAR]"
                            else: state_emoji = "[NEUT]"
                        
                        # Add rejection reason if trend is bullish but filters failed
                        if state_emoji == "[BULL]" and symbol in self._last_reject_reasons:
                            reason = self._last_reject_reasons[symbol]
                            if reason:
                                # Shorten reason for log (e.g. "Low volume (10 < 100)" -> "VOL")
                                if "EMA" in reason: rejection_suffix = ":WAIT_EMA"
                                elif "ADX" in reason: rejection_suffix = ":WAIT_ADX"
                                elif "RSI" in reason: rejection_suffix = ":WAIT_RSI"
                                elif "Volume" in reason: rejection_suffix = ":WAIT_VOL"
                                elif "expensive" in reason: rejection_suffix = ":WAIT_VWAP_CAP"
                                elif "VWAP" in reason: rejection_suffix = ":WAIT_VWAP"
                                elif "Momentum" in reason: rejection_suffix = ":WAIT_MOM"
                                else: rejection_suffix = ":WAIT"
                        
                        # Short symbol using regex (e.g. NIFTY...25500PE -> 25500PE)
                        match = re.search(r'(\d+[CP]E)$', symbol)
                        short_sym = match.group(1) if match else (symbol[-10:] if len(symbol) > 10 else symbol)
                        
                        scan_summary.append(f"{short_sym}:{state_emoji}{rejection_suffix}{strike_price:.1f}")
                    if scan_summary:
                        print(f"        SCAN | {' | '.join(scan_summary)}")

            # ========== PROCESS SIGNALS (Using Shared Data) ==========
            tasks = []
            for symbol, df in data_map.items():
                # Skip if insufficient data (use config exec_bars as minimum)
                min_bars = self.config.get("system", {}).get("data_limits", {}).get("exec_bars", 50)
                if len(df) < min_bars: continue
                tasks.append(self._process_strike_data(symbol, df, use_ha))
            
            if tasks:
                await asyncio.gather(*tasks)

        else:
            # Fallback for empty list or sequential (if needed, but usually parallel covers all)
            pass # Keep it simple, just skip if empty
            
            # Verbose Scan Summary (show UTBot state for each strike)

    
    async def _process_strike_data(self, symbol: str, df_opt, use_ha: bool):
        """
        Process a single strike's data - calculate UTBot and handle entry/exit.
        Used by both parallel and sequential scanning.
        """
        try:
            # Calculate UTBot on Option chart
            utbot_result = self.indicators["option_utbot"].calculate(df_opt, use_ha=use_ha)
            
            # Store UTBot state for verbose logging
            self._last_utbot_state[symbol] = utbot_result.trend
            
            # GUARD: Prevent re-entry if position exists
            if symbol in self.trades and self.trades[symbol].state in [TradeState.ENTERING, TradeState.POSITION, TradeState.EXITING]:
                pass # Skip entry logic if position exists
            
            else:
                # ENTRY LOGIC
                entry_cfg = self.config.get("entry_conditions", {})
                use_indicator = entry_cfg.get("use_indicator", True)
                use_filters = entry_cfg.get("use_filters", True)
                
                trigger_active = False
                
                if not use_indicator:
                    # Indicator disabled -> Always trigger scan (subject to filters)
                    trigger_active = True
                elif utbot_result.signal == 1:
                    # Indicator enabled AND Signal fired
                    print(f"\n[SIGNAL] UTBot BUY detected on {symbol}")
                    trigger_active = True
                
                if trigger_active:
                    # Track signal for re-entry attempts (even if filters fail)
                    if symbol not in self._signal_wait_state:
                        self._signal_wait_state[symbol] = {
                            'signal_time': datetime.now(),
                            're_entry_attempts': 0
                        }
                    
                    filters_pass = True
                    curr_price = df_opt['Close'].iloc[-1]
                    limit_price = curr_price
                    atr_val = 0.0  # Initialize default

                    if use_filters:
                        valid, f_price, reasons, atr_val = await self._check_entry_conditions(symbol, df_opt)
                        if valid:
                            limit_price = f_price
                            self._last_reject_reasons[symbol] = None # Clear on success
                        else:
                            filters_pass = False
                            # Store first reason for summary
                            self._last_reject_reasons[symbol] = reasons[0] if reasons else "Unknown"
                            
                            if use_indicator: # Log reject only if specific signal fired
                                print(f"[REJECT] Filters failed for {symbol}: {reasons}")
                    else:
                        # Filters disabled, but we MUST calculate ATR for TSL/Risk Manager
                        tech_result = self.indicators["option_tech"].calculate(df_opt, use_ha=use_ha)
                        atr_val = tech_result.metadata.get("atr", 0.0)
                    
                    if filters_pass:
                        print(f"[ENTRY] All conditions passed for {symbol} @ {limit_price:.2f}")
                        
                        # Determine side from symbol (CE = CALL, PE = PUT)
                        side = "CALL" if "CE" in symbol else "PUT"
                        
                        # Execute entry
                        await self._execute_entry(
                            symbol=symbol,
                            side=side,
                            price=limit_price,
                            ltf_signal=utbot_result,
                            atr=atr_val if use_filters else 0.0 # Pass ATR if available
                        )
                        
                        # Clear signal state after successful entry
                        if symbol in self._signal_wait_state:
                            del self._signal_wait_state[symbol]
            
                # Check for Pullback Re-entry (only if signal was tracked but entry failed)
                elif symbol not in self.trades and symbol in self._signal_wait_state:
                    # Only attempt re-entry if trend is still bullish
                    trend_ok = (utbot_result.trend == 1)
                    
                    if trend_ok:
                        await self._check_re_entry_trigger(symbol, df_opt, use_ha)
            
            # ========== UTBot SELL SIGNAL EXIT CHECK ==========
            # Check if we have an active position for this symbol
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                exit_cfg = self.config.get("exit_conditions", {})
                
                if exit_cfg.get("use_utbot_sell", True):
                    priority = exit_cfg.get("tsl_priority", "SIGNAL_FIRST").upper()
                    
                    # Check for UTBot sell signal (signal == -1 means fresh sell)
                    if utbot_result.signal == -1:
                        # Clear any pending BUY signal state (trend reversed, stale signal invalid)
                        if symbol in self._signal_wait_state:
                            del self._signal_wait_state[symbol]
                        
                        if priority == "SIGNAL_FIRST":
                            # Exit immediately on UTBot sell
                            print(f"\n[EXIT SIGNAL] UTBot SELL detected on {symbol}. Exiting...")
                            await self._execute_exit(self.trades[symbol], "UTBot Sell Signal")
                        else:
                            # TSL_FIRST: Just log, don't exit
                            print(f"[INFO] UTBot SELL on {symbol} (TSL_FIRST mode - waiting for TSL)")
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

    async def _print_active_trade_details(self, active_symbols, data_map=None):
        """Print detailed status for active trades in heartbeat"""
        if not active_symbols:
            return

        # BUG FIX #4: Fetch all position data concurrently if data_map not provided
        # This prevents sequential API calls (2 positions = 2 separate calls = slow)
        if data_map is None:
            data_map = {}
            opt_ltf_tf = self.config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
            exec_bars = self.config.get("system", {}).get("data_limits", {}).get("exec_bars", 50)
            
            # Fetch all concurrently
            async def fetch_one(sym):
                try:
                    df = await self.data_provider.fetch_history(sym, opt_ltf_tf, bars=exec_bars, exchange="NFO")
                    return (sym, df)
                except:
                    return (sym, None)
            
            results = await asyncio.gather(*[fetch_one(s) for s in active_symbols])
            data_map = {sym: df for sym, df in results if df is not None and not df.empty}

        # Prepare lists for printing to group them
        pos_lines = []
        check_lines = []

        for symbol in active_symbols:
            try:
                if symbol not in self.trades:
                    continue
                    
                trade = self.trades[symbol]
                
                # Get live price
                price = self.cache.get_price(symbol) or trade.current_price
                if price == 0: continue
                
                # Calculate P&L (Always Long Option)
                pnl = (price - trade.entry_price) * trade.quantity
                pnl_pct = (pnl / (trade.entry_price * trade.quantity)) * 100
                
                # TSL Gap (LTP - TSL)
                tsl_gap = price - trade.tsl_level
                
                # Format Position Line
                # NIFTY...PE | Entry: 92.2 | LTP: 92.2 | TSL: 112.2 | Gap: -20.0 | P&L: INR 0.0 (0.0%)
                pos_line = f"{symbol} | Entry: {trade.entry_price:.1f} | LTP: {price:.1f} | TSL: {trade.tsl_level:.1f} | Gap: {tsl_gap:.1f} | P&L: INR {pnl:.1f} ({pnl_pct:.1f}%)"
                pos_lines.append(pos_line)
                
                # Use data from map (now guaranteed to be populated)
                if symbol in data_map:
                    df_stat = data_map[symbol]
                    use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
                    tech = self.indicators["option_tech"].calculate(df_stat, use_ha=use_ha)
                    
                    # Format Checks Line
                    # NIFTY...PE | RSI: 20.0 | ADX: 24.0 | VWAP: 123.4
                    rsi = tech.metadata.get('rsi', 0)
                    adx = tech.metadata.get('adx', 0)
                    vwap = tech.metadata.get('vwap', 0)
                    
                    check_line = f"{symbol} | RSI: {rsi:.1f} | ADX: {adx:.1f} | VWAP: {vwap:.1f}"
                    check_lines.append(check_line)
                else:
                    check_lines.append(f"{symbol} | No Data for Checks")

            except Exception as e:
                logger.error(f"Error printing details for {symbol}: {e}")

        # Print Grouped Sections
        if pos_lines:
            print("  [POSITIONS]")
            for line in pos_lines:
                print(f"    - {line}")
        
        if check_lines:
            print("  [CHECKS STATUS]")
            for line in check_lines:
                print(f"    - {line}")

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
        """Check if symbol is in re-entry cooldown period (thread-safe)"""
        with self._cooldown_lock:
            if symbol not in self._exit_cooldowns:
                return False
                
            expiry_time = self._exit_cooldowns[symbol]
            now = datetime.now()
            
            if now < expiry_time:
                return True
            
            # Cooldown expired, remove from tracking
            del self._exit_cooldowns[symbol]
            return False
    
    async def _check_entry_conditions(self, symbol: str, df_opt) -> tuple:
        """
        Validate entry conditions on Option chart.
        
        Checks (all configurable via entry_conditions in config.yaml):
        1. LTP > VWAP (if enabled)
        2. LTP <= VWAP + (ATR * multiplier) (if enabled)
        3. EMA Fast > EMA Slow (if enabled)
        4. Volume > Avg * multiplier (if enabled)
        5. Upper Wick <= Body (if enabled)
        6. ADX > min (if enabled)
        7. RSI > min AND RSI < max (if enabled)
        
        Returns:
            tuple: (valid: bool, limit_price: float, reasons: list, atr: float)
        """
        cfg = self.config.get("entry_conditions", {})
        use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
        
        # Calculate technical indicators on Option chart
        tech_result = self.indicators["option_tech"].calculate(df_opt, use_ha=use_ha)
        meta = tech_result.metadata
        
        # Extract values
        ema_fast = meta["emas"].get(cfg.get("ema_fast", 9), 0)
        ema_slow = meta["emas"].get(cfg.get("ema_slow", 20), 0)
        vwap = meta.get("vwap", 0)
        atr = meta.get("atr", 0)
        rsi = meta.get("rsi", 50)
        adx = meta.get("adx", 0)
        volume = meta.get("volume", 0)
        vol_avg = meta.get("vol_ma_5", 1)  # Avoid division by zero
        
        close = df_opt['Close'].iloc[-1]
        open_p = df_opt['Open'].iloc[-1]
        high = df_opt['High'].iloc[-1]
        
        reasons = []
        
        # 1. VWAP Bullish Check: LTP > VWAP
        if cfg.get("check_vwap_bullish", True):
            if close < vwap:
                reasons.append(f"Below VWAP ({close:.2f} < {vwap:.2f})")
        
        # 2. VWAP Cap Check: LTP <= VWAP + (ATR * multiplier)
        if cfg.get("check_vwap_cap", True):
            mult = cfg.get("vwap_cap_atr_mult", 1.5)
            max_price = vwap + (atr * mult)
            if close > max_price:
                reasons.append(f"Too expensive ({close:.2f} > {max_price:.2f})")
        
        # 3. EMA & Momentum Checks
        # Calculate status of each *enabled* check
        check_trend = cfg.get("check_ema_trend", False)
        check_mom = cfg.get("check_momentum_candle", False)
        
        # Valid flags (Assume True if check is disabled, but for "ANY" mode we need to know if it passed)
        trend_passed = False
        mom_passed = False
        
        # Check 1: EMA Trend
        if check_trend:
            if ema_fast > ema_slow:
                trend_passed = True
        
        # Check 2: Momentum Candle
        if check_mom:
            # Source Selection
            o_col, c_col, l_col = ("HA_Open", "HA_Close", "HA_Low") if use_ha else ("Open", "Close", "Low")
            c_open = df_opt[o_col].iloc[-1]
            c_close = df_opt[c_col].iloc[-1]
            c_low = df_opt[l_col].iloc[-1]
            
            # Logic
            is_green = c_close > c_open
            mom_wick_threshold = cfg.get("mom_wick_threshold_pct", 0.0005)
            has_lower_wick = (c_open - c_low) > (c_close * mom_wick_threshold)
            wick_ok = not has_lower_wick if cfg.get("mom_no_lower_wick", True) else True
            
            ema_val = meta["emas"].get(cfg.get("mom_ema_period", 9), ema_fast)
            
            # Body Calculation
            if is_green and wick_ok:
                body_top = c_close
                body_bottom = c_open
                
                # Check intersection or above
                if body_bottom >= ema_val:
                    mom_passed = True # Completely above
                elif body_top > ema_val:
                    # Straddles: Check %
                    total_body = body_top - body_bottom
                    above_part = body_top - ema_val
                    pct_above = above_part / total_body if total_body > 0 else 0
                    if pct_above >= cfg.get("mom_body_above_pct", 0.70):
                        mom_passed = True

        # Combine Logical Results
        # If a check is NOT enabled, does it count as "Pass" or is it ignored?
        # Standard: Ignored checks don't block.
        # But for "ANY", we look at the pool of enabled checks.
        
        mode = cfg.get("momentum_check_mode", "ALL").upper()
        
        if mode == "NONE":
            # Pass unconditionally (Skip momentum checks)
            pass
            
        elif mode == "ANY":
            # Pass if ANY enabled check passes
            # If no checks enabled, default to True (pass)
            active_checks = []
            if check_trend: active_checks.append(trend_passed)
            if check_mom: active_checks.append(mom_passed)
            
            if active_checks and not any(active_checks):
                reasons.append("Momentum: No active condition met (Mode: ANY)")
                
        else: # Mode == "ALL" (Default)
            # Fail if ANY enabled check fails
            if check_trend and not trend_passed:
                reasons.append(f"EMA Trend failed ({ema_fast:.1f} <= {ema_slow:.1f})")
            if check_mom and not mom_passed:
                reasons.append("Momentum Candle failed")

        
        # 4. Volume Check: Volume > Avg * multiplier
        if cfg.get("check_volume", True):
            mult = cfg.get("vol_multiplier", 1.2)
            if volume < (vol_avg * mult):
                reasons.append(f"Low volume ({volume:.0f} < {vol_avg * mult:.0f})")
        
        # 5. Wick Ratio Check: Upper Wick <= Body
        if cfg.get("check_wick_ratio", True):
            body = abs(close - open_p)
            upper_wick = high - max(close, open_p)
            if upper_wick > body:
                reasons.append(f"Rejection wick ({upper_wick:.2f} > {body:.2f})")
        
        # 6. ADX Check: ADX > min
        if cfg.get("check_adx", False):
            adx_min = cfg.get("adx_min", 20)
            if adx < adx_min:
                reasons.append(f"Weak trend (ADX {adx:.1f} < {adx_min})")
        
        # 7. RSI Check: RSI > min AND RSI < max
        if cfg.get("check_rsi", False):
            rsi_min = cfg.get("rsi_min", 55)
            rsi_max = cfg.get("rsi_max", 100)
            if rsi < rsi_min or rsi > rsi_max:
                reasons.append(f"RSI out of range ({rsi:.1f} not in {rsi_min}-{rsi_max})")
        
        # 8. Max Option Price Check (from strike_selection)
        max_opt_price = self.config.get("strike_selection", {}).get("max_option_price", 0)
        if max_opt_price > 0 and close > max_opt_price:
            reasons.append(f"Price exceeds cap ({close:.2f} > {max_opt_price})")
        
        # Determine result
        if reasons:
            return False, 0.0, reasons, atr
        
        # Valid! Use close (LTP) for immediate fill, subject to VWAP cap checks already performed
        # We use 'close' instead of 'min(close, vwap)' to ensure we catch explosive moves
        # where the price is running away from the average.
        limit_price = close
        return True, limit_price, [], atr
    
    async def _check_re_entry_trigger(self, symbol: str, df_opt, use_ha: bool):
        """
        Check for pullback re-entry opportunity.
        
        Conditions (from config.yaml re_entry):
        1. Price pulls back into EMA Fast-EMA Slow zone
        2. No close below EMA Slow
        3. Fresh bullish candle closes above EMA Fast
        """
        re_entry_cfg = self.config.get("re_entry", {})
        
        if not re_entry_cfg.get("enabled", False):
            return
        
        # Check max attempts
        state = self._signal_wait_state.get(symbol, {})
        max_attempts = re_entry_cfg.get("max_attempts", 2)
        if state.get("re_entry_attempts", 0) >= max_attempts:
            # Cleanup - no more re-entries for this signal
            if symbol in self._signal_wait_state:
                del self._signal_wait_state[symbol]
            return
        
        # Calculate EMAs
        tech_result = self.indicators["option_tech"].calculate(df_opt, use_ha=use_ha)
        meta = tech_result.metadata
        
        ema_fast = meta["emas"].get(re_entry_cfg.get("pullback_ema_fast", 9), 0)
        ema_slow = meta["emas"].get(re_entry_cfg.get("pullback_ema_slow", 21), 0)
        
        if use_ha:
            close = df_opt['HA_Close'].iloc[-1]
            low = df_opt['HA_Low'].iloc[-1]
        else:
            close = df_opt['Close'].iloc[-1]
            low = df_opt['Low'].iloc[-1]
        
        # Check conditions:
        # 1. Price pulled back into zone (low touched or crossed EMA zone)
        in_zone = low <= ema_fast
        
        # 2. No close below EMA Slow (still respecting support)
        above_slow = close > ema_slow
        
        # 3. Fresh bullish candle closes above EMA Fast
        bullish_close = close > ema_fast
        
        if in_zone and above_slow and bullish_close:
            print(f"[RE-ENTRY] Pullback trigger on {symbol}. Checking conditions...")
            
            # Run entry conditions
            valid, limit_price, reasons, atr_val = await self._check_entry_conditions(symbol, df_opt)
            
            if valid:
                print(f"[RE-ENTRY] Executing re-entry on {symbol} @ {limit_price:.2f}")
                
                side = "CALL" if "CE" in symbol else "PUT"
                await self._execute_entry(
                    symbol=symbol,
                    side=side,
                    price=limit_price,
                    ltf_signal=None,
                    atr=atr_val
                )
                
                # Increment re-entry counter
                if symbol not in self._signal_wait_state:
                    self._signal_wait_state[symbol] = {'re_entry_attempts': 0}
                
                self._signal_wait_state[symbol]['re_entry_attempts'] = self._signal_wait_state[symbol].get("re_entry_attempts", 0) + 1
            else:
                print(f"[RE-ENTRY] Conditions failed for {symbol}: {reasons}")
    
    

    async def _execute_entry(self, symbol: str, side: str, price: float, ltf_signal, atr: float = 0.0):
        """Execute entry order with atomic max_positions check"""
        async with self._entry_lock:
            # Re-check max_positions inside lock to prevent race condition
            max_positions = self.config.get("max_positions", 4)
            active_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
            if active_count >= max_positions:
                print(f"[SKIP] Max positions reached ({active_count}/{max_positions}). Skipping {symbol}.")
                return
            
            try:
                # Check cooldown again just in case
                if self._is_symbol_on_cooldown(symbol):
                    print(f"[SKIP] {symbol} is on cooldown.")
                    return 

                # Get lot size from config
                lots = self.config.get("max_lots", 1)
                
                # Dynamically fetch lot size (cached via DataProvider)
                lot_size = await self.data_provider.get_lot_size(symbol, exchange="NFO")
                    
                quantity = lots * lot_size
                print(f"[INFO] Using Lot Size: {lot_size} for {symbol}. Total Qty: {quantity}")
                
                # Place Order
                print(f"[ENTRY] Placing BUY order for {symbol} @ {price:.2f} Qty={quantity}")
                
                # Determine Order Type from Config
                order_type = self.config.get("execution", {}).get("order_type", "LIMIT").upper()
                
                # Auto-correct to LIMIT if not recognized
                if order_type not in ["LIMIT", "SMART_LIMIT", "MARKET"]:
                     order_type = "LIMIT"
                
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
                        current_price=price,
                        highest_price=price,
                        atr=atr
                    )
                    
                    # Store and persist
                    self.trades[symbol] = trade
                    self.persistence.save_trade(trade)
                    
                    print(f"[POSITION] Entered {symbol} @ {trade.entry_price:.2f}")
                    logger.info(f"Entry executed: {symbol} @ {trade.entry_price:.2f}")
                    
                    # Set short cooldown only on SUCCESS to prevent double-entry within same signal
                    self._set_cooldown(symbol, 10)  # 10 seconds cooldown on success
                else:
                    fail_msg = order_id.message if order_id else "No response from Order Manager"
                    print(f"[ERROR] Order Failed for {symbol}: {fail_msg}")
                    logger.error(f"Order placement failed: {fail_msg}")
                    # NO cooldown on order failure - allow immediate retry
                    
            except Exception as e:
                logger.error(f"Entry execution error for {symbol}: {e}", exc_info=True)
                print(f"[ERROR] Entry execution crashed: {e}")
                self._set_cooldown(symbol)  # 60s cooldown on crash (safety)
    
    def _set_cooldown(self, symbol: str, seconds: int = 0):
        """Set cooldown for a symbol (thread-safe)"""
        if seconds == 0:
            seconds = self.config.get("system", {}).get("cooldowns", {}).get("error_sec", 60)
        with self._cooldown_lock:
            self._exit_cooldowns[symbol] = datetime.now() + timedelta(seconds=seconds)

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
            
            # Evaluate risk (Option-Centric: No Index trend check)
            
            # CRITICAL FIX: Update trade with latest price stats (Highest/Lowest) BEFORE evaluation
            # This ensures TSL calculation uses the correct High Water Mark.
            trade = trade.update_price(price)
            self.trades[symbol] = trade # Memory update
            # We don't save to DB yet to avoid spam IO, unless TSL updates or Exit.
            # But if highest_price changed significantly, we might want to?
            # For now, let's trust memory-to-memory in the loop.
            
            # TSL is purely based on Option price movement
            decision = self.risk_manager.evaluate(trade, price, is_trend_reversed=False)
            
            # Calculate current P&L for display
            curr_pnl = (price - trade.entry_price) * trade.quantity
            # Removed incorrect inversion for PUTs (we are Long Options)
            curr_pnl_pct = (curr_pnl / (trade.entry_price * trade.quantity)) * 100
            
            # Update TSL level (only if changed)
            if decision.new_tsl_level > 0 and decision.new_tsl_level != trade.tsl_level:
                old_tsl = trade.tsl_level
                
                # Check if cushion was applied (TSL moved DOWN instead of exit)
                if decision.cushion_applied:
                    print(f"[RISK] {symbol} | {decision.message}")
                    trade = Trade(**{
                        **trade.__dict__,
                        "tsl_level": decision.new_tsl_level,
                        "last_stage": decision.new_stage,
                        "cushion_attempts": trade.cushion_attempts + 1  # Increment cushion counter
                    })
                else:
                    print(f"[RISK] {symbol} | TSL: {old_tsl:.2f} → {decision.new_tsl_level:.2f} (Stage: {decision.new_stage})")
                    trade = Trade(**{
                        **trade.__dict__,
                        "tsl_level": decision.new_tsl_level,
                        "last_stage": decision.new_stage
                    })
                    
                self.trades[symbol] = trade
                self.persistence.save_trade(trade)
            
            # ... (Exit logic remains)

            # Periodic status report per position (every 5 seconds)
            # User requested cleaner logs - disabling this interleaved log in favor of Heartbeat snapshot
            # if report:
            #    tsl_gap = price - trade.tsl_level
            #    ... (commented out) ...
            #    print(f"[POS] ...")
            
            # Exit if needed
            if decision.should_exit:
                auto_sell = self.config.get("execution", {}).get("enable_bot_auto_sell", True)
                
                if not auto_sell:
                    # Log alert but do NOT execute exit
                    if self._heartbeat_counter % 5 == 0: # Reduce spam (every 5s)
                        print(f"\n[ALERT] {decision.message} | Auto-Sell DISABLED. Please Exit Manually!")
                    logger.info(f"[MANUAL_EXIT_REQ] {symbol}: {decision.message}")
                    continue
                
                print(f"[RISK] Triggering EXIT for {symbol} (Entry: {trade.entry_price:.2f}): {decision.message}")
                logger.info(f"[EXIT] {symbol}: {decision.message}")
                await self._execute_exit(trade, decision.reason.value)
            

            #     except Exception as e:
            #         pass # Don't break logging if stats fail
            #     
            #     print(f"[POS] {symbol} | Entry: {trade.entry_price:.2f} | LTP: {price:.2f} | TSL: {trade.tsl_level:.2f} | Gap: {tsl_gap:.2f} | P&L: INR {curr_pnl:.2f} ({curr_pnl_pct:.1f}%){stats_str}")

    async def _execute_exit(self, trade: Trade, reason: str):
        """Execute exit order"""
        try:
            # Transition to EXITING
            print(f"[EXIT] Sending Market Order for {trade.symbol}...")
            trade = TradeStateMachine.transition(trade, TradeState.EXITING)
            self.trades[trade.symbol] = trade
            self.persistence.save_trade(trade)
            
            # Place exit order
            order_id = await self.order_manager.place_order(
                symbol=trade.symbol,
                action="SELL",  # Always SELL to exit a LONG option position (CE or PE)
                quantity=trade.quantity,
                order_type="MARKET",
                exchange="NFO",
                product=self.config.get("product_type", "MIS")
            )
            
            if order_id:
                # Update trade to EXITED
                trade = TradeStateMachine.transition(trade, TradeState.EXITED, reason)
                
                # Calculate final P&L
                pnl, pnl_pct = trade.calculate_pnl()
                trade = Trade(**{
                    **trade.__dict__,
                    'exit_price': order_id.filled_price if order_id.filled_price else trade.current_price,
                    'exit_time': datetime.now(),
                    'pnl': pnl,
                    'pnl_pct': pnl_pct
                })
                
                self.trades[trade.symbol] = trade
                self.persistence.save_trade(trade)
                self.persistence.archive_trade(trade)
                
                print(f"[EXIT] {trade.symbol} Closed. P&L: {pnl:.2f} ({pnl_pct:.1f}%)")

                # --- CSV REPORTING ---
                try:
                    import csv
                    import os
                    
                    report_dir = "Reporting"
                    if not os.path.exists(report_dir):
                        os.makedirs(report_dir)
                    
                    csv_file = os.path.join(report_dir, "trades.csv")
                    file_exists = os.path.isfile(csv_file)
                    
                    with open(csv_file, mode='a', newline='') as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(["Timestamp", "Symbol", "Side", "Qty", "Entry", "Exit", "PnL", "PnL%", "Reason"])
                        
                        writer.writerow([
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            trade.symbol,
                            trade.side,
                            trade.quantity,
                            f"{trade.entry_price:.2f}",
                            f"{trade.current_price:.2f}",
                            f"{pnl:.2f}",
                            f"{pnl_pct:.2f}%",
                            reason
                        ])
                    logger.info(f"Trade logged to {csv_file}")
                except Exception as e:
                    logger.error(f"Failed to write CSV report: {e}")
                
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
                    f"P&L=INR {pnl:.2f} ({pnl_pct:.2f}%), Reason={reason}"
                )
                
                # Cleanup
                if trade.symbol in self.trades:
                    del self.trades[trade.symbol]
                    
            else:
                # Order Failed! Revert to POSITION to keep monitoring/retrying
                print(f"[ERROR] Exit Order Failed for {trade.symbol}. Reverting to POSITION.")
                trade.state = TradeState.POSITION # Manual revert
                self.trades[trade.symbol] = trade
                self.persistence.save_trade(trade) # Save reverted state
                return
                
        except Exception as e:
            logger.error(f"Error executing exit for {trade.symbol}: {e}", exc_info=True)
            # Safe Revert if crashed mid-execution
            if trade.symbol in self.trades:
                 self.trades[trade.symbol].state = TradeState.POSITION

    
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
            # Use positionbook() with timeout
            broker_data = await asyncio.wait_for(
                loop.run_in_executor(None, self.client.positionbook),
                timeout=10
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
            sym = item.get('symbol')
            qty = int(item.get('quantity', 0))
            
            if sym and qty != 0:
                broker_symbols.add(sym)
                
                # ADOPT EXISTING/EXTERNAL POSITION
                if sym not in self.trades:
                    print(f"[SYNC] Found existing broker position: {sym} ({qty} Qty). Adopting...")
                    logger.info(f"Adopting existing position: {sym}")
                    
                    # Estimate entry price (from avg_price)
                    avg_price = float(item.get('average_price', 0) or item.get('buy_avg', 0) or 0)
                    if avg_price == 0:
                        avg_price = float(item.get('lp', 0)) # Fallback to LTP if avg unknown
                    
                    # BUG FIX #5: Calculate ATR for adopted positions
                    # Without ATR, TSL = current_price (no trailing buffer!)
                    atr_value = 0.0
                    try:
                        opt_ltf_tf = self.config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
                        df = await self.data_provider.fetch_history(sym, opt_ltf_tf, 50, "NFO")
                        if df is not None and not df.empty:
                            use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
                            tech_result = self.indicators["option_tech"].calculate(df, use_ha=use_ha)
                            atr_value = tech_result.metadata.get("atr", 0.0)
                            logger.info(f"Calculated ATR for adopted position {sym}: {atr_value:.2f}")
                    except Exception as e:
                        logger.warning(f"Failed to calculate ATR for {sym}: {e}")
                        # Fallback: estimate ATR as 2% of current price
                        atr_value = avg_price * 0.02
                        
                    # Create Trade Object
                    trade = Trade(
                        symbol=sym,
                        entry_price=avg_price,
                        quantity=qty,
                        side="CALL" if "CE" in sym else "PUT",
                        entry_time=datetime.now(), # Unknown time, just use now
                        state=TradeState.POSITION,
                        current_price=float(item.get('lp', avg_price)),
                        highest_price=float(item.get('lp', avg_price)),
                        atr=atr_value
                    )
                    
                    self.trades[sym] = trade
                    self.persistence.save_trade(trade)

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
            
            # BUG FIX #3: Force cleanup to prevent memory leak
            # 1. Force socket cleanup
            if self.client and hasattr(self.client, 'ws'):
                try:
                    if self.client.ws and hasattr(self.client.ws, 'sock'):
                        self.client.ws.sock.close()  # Force close socket
                    self.client.disconnect()
                except Exception as e:
                    logger.debug(f"Cleanup error (expected): {e}")
            
            # 2. Clear internal state
            self._ws_connected = False
            self._ws_subscribed_symbols = []
            
            # 3. Wait before reconnecting
            await asyncio.sleep(2)
            
            # 4. Setup again (connect + subscribe)
            success = await self._setup_websocket()
            
            if success:
                logger.info("WebSocket successfully reconnected")
                print("[INFO] WebSocket successfully reconnected.")
            else:
                logger.warning("WebSocket reconnection failed")
                # print("[WARN] WebSocket reconnection failed.")
                
        except Exception as e:
            logger.error(f"Reconnection error: {e}")
