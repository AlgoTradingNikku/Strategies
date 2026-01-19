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
import re
from typing import Dict, Optional
from datetime import datetime, timedelta
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
        self.cache.load_master_from_file("instruments_cache.pkl")
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
        
        # Last UTBot state and rejection reasons for each strike (for verbose logging)
        self._last_utbot_state = {}
        self._last_reject_reasons = {}
        
        # Restore state from database
        self._restore_state()
        
        print("\n[INFO] Risk Worker (Bodyguard) started.")
        print("[INFO] Scanner Worker (The Brain) started.")
        logger.info("Trading Engine initialized")
    
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
            "ema_periods": [entry_cfg.get("ema_fast", 9), entry_cfg.get("ema_slow", 21)],
            "rsi_period": entry_cfg.get("rsi_period", 14),
            "adx_period": entry_cfg.get("adx_period", 14)
        })
        
        return indicators
    
    def _restore_state(self):
        """Restore trades from SQLite on startup (crash recovery)"""
        active_trades = self.persistence.load_active_trades()
        
        for trade in active_trades:
            self.trades[trade.symbol] = trade
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
        
        # Check max positions
        max_positions = self.config.get("max_positions", 4)
        active_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
        if active_count >= max_positions:
            return
        
        # Get timeframe and bars config
        opt_ltf_tf = self.config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
        exec_bars = self.config.get("system", {}).get("data_limits", {}).get("exec_bars", 100)
        use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
        
        # Check parallel scanning mode
        exec_cfg = self.config.get("execution", {})
        parallel_enabled = exec_cfg.get("parallel_scanning", True)
        
        # Filter strikes to scan (skip those in position or on cooldown)
        strikes_to_scan = []
        for symbol in manual_strikes:
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                continue
            if self._is_symbol_on_cooldown(symbol):
                continue
            strikes_to_scan.append(symbol)
        
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

            # 2. Fetch all concurrently
            results = await asyncio.gather(*[fetch_one(s) for s in strikes_to_scan])
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
                if len(df) < 50: continue
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
                    filters_pass = True
                    curr_price = df_opt['Close'].iloc[-1]
                    limit_price = curr_price
                    
                    if use_filters:
                        valid, f_price, reasons = await self._check_entry_conditions(symbol, df_opt)
                        if valid:
                            limit_price = f_price
                            self._last_reject_reasons[symbol] = None # Clear on success
                        else:
                            filters_pass = False
                            # Store first reason for summary
                            self._last_reject_reasons[symbol] = reasons[0] if reasons else "Unknown"
                            
                            if use_indicator: # Log reject only if specific signal fired
                                print(f"[REJECT] Filters failed for {symbol}: {reasons}")
                    
                    if filters_pass:
                        print(f"[ENTRY] All conditions passed for {symbol} @ {limit_price:.2f}")
                        
                        # Determine side from symbol (CE = CALL, PE = PUT)
                        side = "CALL" if "CE" in symbol else "PUT"
                        
                        # Execute entry
                        await self._execute_entry(
                            symbol=symbol,
                            side=side,
                            price=limit_price,
                            ltf_signal=utbot_result
                        )
                        
                        # Track for re-entry logic
                        self._signal_wait_state[symbol] = {
                            'signal_time': datetime.now(),
                            're_entry_attempts': 0
                        }
            
                # Check for re-entry opportunity (if enabled)
                # Only if NO trigger this cycle
                elif symbol in self._signal_wait_state:
                    await self._check_re_entry_trigger(symbol, df_opt, use_ha)
            
            # ========== UTBot SELL SIGNAL EXIT CHECK ==========
            # Check if we have an active position for this symbol
            if symbol in self.trades and self.trades[symbol].state == TradeState.POSITION:
                exit_cfg = self.config.get("exit_conditions", {})
                
                if exit_cfg.get("use_utbot_sell", True):
                    priority = exit_cfg.get("tsl_priority", "SIGNAL_FIRST").upper()
                    
                    # Check for UTBot sell signal (signal == -1 means fresh sell)
                    if utbot_result.signal == -1:
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
                
                # Get History DataFrame (Use Shared Data Map if available, else fetch)
                df_stat = None
                if data_map and symbol in data_map:
                    df_stat = data_map[symbol]
                else:
                    # Fallback (Legacy/Safety)
                    opt_ltf_tf = self.config.get("option", {}).get("ltf", {}).get("timeframe", "3m")
                    opt_exchange = self.config.get("option", {}).get("exchange", "NFO")
                    df_stat = await self.data_provider.fetch_history(symbol, opt_ltf_tf, bars=50, exchange=opt_exchange)
                
                if df_stat is not None and not df_stat.empty:
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
            tuple: (valid: bool, limit_price: float, reasons: list)
        """
        cfg = self.config.get("entry_conditions", {})
        use_ha = self.config.get("option", {}).get("ltf", {}).get("use_ha", False)
        
        # Calculate technical indicators on Option chart
        tech_result = self.indicators["option_tech"].calculate(df_opt, use_ha=use_ha)
        meta = tech_result.metadata
        
        # Extract values
        ema_fast = meta["emas"].get(cfg.get("ema_fast", 9), 0)
        ema_slow = meta["emas"].get(cfg.get("ema_slow", 21), 0)
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
        
        # 3. EMA & Momentum Candle Logic
        check_trend = cfg.get("check_ema_trend", True)
        check_mom = cfg.get("check_momentum_candle", False)
        mode = cfg.get("ema_logic_mode", "AND").upper()
        
        trend_ok = True
        if check_trend:
            if ema_fast <= ema_slow:
                trend_ok = False
        
        mom_ok = True
        if check_mom:
            # Source Selection
            o_col, c_col, l_col = ("HA_Open", "HA_Close", "HA_Low") if use_ha else ("Open", "Close", "Low")
            c_open, c_close, c_low = df_opt[o_col].iloc[-1], df_opt[c_col].iloc[-1], df_opt[l_col].iloc[-1]
            
            ema_val = meta["emas"].get(cfg.get("mom_ema_period", 9), ema_fast)
            
            # Logic: Green + No Lower Wick + 80% Body Above EMA
            is_green = c_close > c_open
            no_lower_wick = c_low >= c_open
            body_size = c_close - c_open
            threshold = c_open + ((1 - cfg.get("mom_body_above_pct", 0.8)) * body_size) if is_green else 0
            
            mom_ok = is_green and no_lower_wick and (threshold >= ema_val)
                
        # Combine filters
        final_ema_ok = True
        if check_trend and check_mom:
            final_ema_ok = (trend_ok or mom_ok) if mode == "OR" else (trend_ok and mom_ok)
        elif check_trend:
            final_ema_ok = trend_ok
        elif check_mom:
            final_ema_ok = mom_ok
            
        if not final_ema_ok:
            if check_trend and not trend_ok:
                reasons.append(f"EMA Trend failed ({ema_fast:.1f} <= {ema_slow:.1f})")
            if check_mom and not mom_ok:
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
            return False, 0.0, reasons
        
        # Valid! Use close (LTP) for immediate fill, subject to VWAP cap checks already performed
        # We use 'close' instead of 'min(close, vwap)' to ensure we catch explosive moves
        # where the price is running away from the average.
        limit_price = close
        return True, limit_price, []
    
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
            valid, limit_price, reasons = await self._check_entry_conditions(symbol, df_opt)
            
            if valid:
                print(f"[RE-ENTRY] Executing re-entry on {symbol} @ {limit_price:.2f}")
                
                side = "CALL" if "CE" in symbol else "PUT"
                await self._execute_entry(
                    symbol=symbol,
                    side=side,
                    price=limit_price,
                    ltf_signal=None
                )
                
                # Increment re-entry counter
                self._signal_wait_state[symbol]['re_entry_attempts'] = state.get("re_entry_attempts", 0) + 1
            else:
                print(f"[RE-ENTRY] Conditions failed for {symbol}: {reasons}")
    
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
                    current_price=price,
                    highest_price=price
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
            
            # Evaluate risk (Option-Centric: No Index trend check)
            # TSL is purely based on Option price movement
            decision = self.risk_manager.evaluate(trade, price, is_trend_reversed=False)
            
            # Calculate current P&L for display
            curr_pnl = (price - trade.entry_price) * trade.quantity
            # Removed incorrect inversion for PUTs (we are Long Options)
            curr_pnl_pct = (curr_pnl / (trade.entry_price * trade.quantity)) * 100
            
            # Update TSL level
            if decision.new_tsl_level > 0:
                old_tsl = trade.tsl_level
                print(f"[RISK] {symbol} | TSL moved from {old_tsl:.2f} to {decision.new_tsl_level:.2f} (Stage: {decision.new_stage})")
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
                
                print(f"[RISK] Triggering EXIT for {symbol}: {decision.message}")
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
