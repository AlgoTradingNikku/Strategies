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
        
        # Restore state from database
        self._restore_state()
        
        print("\n[INFO] Risk Worker (Bodyguard) started.")
        print("[INFO] Scanner Worker (The Brain) started.")
        logger.info("Trading Engine initialized")
    
    def _load_indicators(self) -> dict:
        """Load indicators from config"""
        indicators = {}
        
        # Index LTF
        idx_ltf_config = self.config.get("index", {}).get("ltf", {})
        indicators["index_ltf"] = IndicatorRegistry.create("utbot", {
            "sensitivity": idx_ltf_config.get("sensitivity", 1.0),
            "atr_period": idx_ltf_config.get("atr", 10)
        })
        
        # Index HTF (if enabled)
        if self.config.get("index", {}).get("htf", {}).get("enabled", False):
            idx_htf_config = self.config["index"]["htf"]
            indicators["index_htf"] = IndicatorRegistry.create("utbot", {
                "sensitivity": idx_htf_config.get("sensitivity", 1.0),
                "atr_period": idx_htf_config.get("atr", 10)
            })
        
        # Option LTF
        opt_ltf_config = self.config.get("option", {}).get("ltf", {})
        indicators["option_ltf"] = IndicatorRegistry.create("utbot", {
            "sensitivity": opt_ltf_config.get("sensitivity", 1.0),
            "atr_period": opt_ltf_config.get("atr", 10)
        })
        
        # Option HTF (if enabled)
        if self.config.get("option", {}).get("htf", {}).get("enabled", False):
            opt_htf_config = self.config["option"]["htf"]
            indicators["option_htf"] = IndicatorRegistry.create("utbot", {
                "sensitivity": opt_htf_config.get("sensitivity", 1.0),
                "atr_period": opt_htf_config.get("atr", 10)
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
                symbols_to_subscribe = manual_strikes
            
            if not symbols_to_subscribe:
                return True # No symbols needed, proceed
            
            # Build instruments list for WebSocket
            instruments = [
                {"exchange": "NFO", "symbol": sym}
                for sym in symbols_to_subscribe
            ]
            
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
                
            await asyncio.sleep(2) # Check every 2 seconds

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
            
            # Run every 5 seconds
            await asyncio.sleep(5)
    
    async def _scan_for_signals(self):
        """Scan index for trading signals with heartbeat logging"""
        # Get index symbol and exchange from config
        index_query = self.config.get("index_query", "NIFTY")
        index_exchange = self.config.get("index_exchange", "NSE_INDEX")
        
        # Fetch index data (LTF)
        idx_ltf_tf = self.config["index"]["ltf"]["timeframe"]
        df_ltf = await self.data_provider.fetch_history(
            index_query, idx_ltf_tf, bars=100, exchange=index_exchange
        )
        
        if df_ltf is None or len(df_ltf) < 20:
            return
        
        # Get current index price
        index_price = df_ltf['Close'].iloc[-1]
        self._last_index_price = index_price
        
        # Calculate LTF indicator
        use_ha = self.config.get("index_use_ha", True)
        signal_ltf = self.indicators["index_ltf"].calculate(df_ltf, use_ha=use_ha)
        index_trend = signal_ltf.trend
        
        # Determine trend strings
        ltf_trend_str = "BULLISH" if index_trend == 1 else "BEARISH" if index_trend == -1 else "NEUTRAL"
        self._last_ltf_trend = ltf_trend_str
        
        # Check HTF alignment (if enabled)
        htf_aligned = True
        htf_trend_str = "OFF"
        if "index_htf" in self.indicators:
            idx_htf_tf = self.config["index"]["htf"]["timeframe"]
            df_htf = await self.data_provider.fetch_history(
                index_query, idx_htf_tf, bars=50, exchange=index_exchange
            )
            
            if df_htf is not None:
                signal_htf = self.indicators["index_htf"].calculate(df_htf, use_ha=use_ha)
                htf_aligned = (signal_htf.trend == signal_ltf.trend)
                htf_trend_str = "BULLISH" if signal_htf.trend == 1 else "BEARISH" if signal_htf.trend == -1 else "NEUTRAL"
                self._last_htf_trend = htf_trend_str
        
        # Heartbeat logging (every 2 cycles = ~10 seconds)
        self._heartbeat_counter += 1
        if self._heartbeat_counter % 2 == 0:
            now = datetime.now().strftime("%H:%M:%S")
            pos_count = len([t for t in self.trades.values() if t.state == TradeState.POSITION])
            pos_status = f"{pos_count} active" if pos_count > 0 else "No positions"
            
            # Print heartbeat
            print(f"[{now}] HEARTBEAT | Index: {index_price:.2f} | LTF-{idx_ltf_tf}: {ltf_trend_str} | HTF-{self.config['index']['htf']['timeframe']}: {htf_trend_str}")
            
            # Detailed Active/Observing Log (Legacy Style)
            # 1. Show Active Trades
            if self.trades:
                for sym, trade in self.trades.items():
                     state = trade.state.name
                     bias = "BULLISH" if trade.side == "CALL" else "BEARISH"
                     print(f"   ACTIVE: {sym} | State: {state} | Bias: {bias}")
            # 2. Show Observing Symbols (if no active trade for that symbol)
            else:
                 manual_strikes = self.config.get("strike_selection", {}).get("manual_strikes", [])
                 for sym in manual_strikes:
                     # Only show if not already in active list
                     if sym not in self.trades:
                         # Filter based on Index Trend (Show only aligned strikes)
                         is_ce = "CE" in sym
                         is_pe = "PE" in sym
                         if (is_ce and index_trend == -1) or (is_pe and index_trend == 1):
                            continue

                         # Get details from last scan
                         info = self._strike_states.get(sym, {})
                         
                         # Determine Actual Bias from Indicator
                         trend_val = info.get('trend', 0)
                         bias_str = "Up" if trend_val == 1 else "Down" if trend_val == -1 else "Flat"
                         
                         # Get max age for display context
                         max_age = self.config.get("entry_logic", {}).get("option_max_trend_age", 8)
                         
                         age_val = info.get('age', 0)
                         age_part = f" | Signal [LTF] Age: {age_val} (<{max_age})" if 'age' in info else ""
                         
                         print(f"   {sym} | OBSERVING | Bias: {bias_str}{age_part}")
        
        # Check trigger mode based on max_trend_age
        entry_logic = self.config.get("entry_logic", {})
        # Defaults to 8 if not specified (Backward compatibility or safe default)
        index_max_age = entry_logic.get("index_max_trend_age", 8)
        
        should_scan_options = False
        
        # LOGIC 1: Sniper Mode (Age = 0)
        # Only enter on exact crossover signal
        if index_max_age == 0:
            if signal_ltf.has_fresh_buy() or signal_ltf.has_fresh_sell():
                if htf_aligned:
                    logger.info(
                        f"[SIGNAL] Index {index_query}: "
                        f"{'BUY' if signal_ltf.trend == 1 else 'SELL'} "
                        f"Signal={signal_ltf.signal}, HTF Aligned={htf_aligned}"
                    )
                    should_scan_options = True
                    
        # LOGIC 2: Window Mode (Age > 0)
        # Enter if trend is active and young enough
        else:
            # Check if trend is valid (Not Neutral) and Aligned
            if signal_ltf.trend != 0 and htf_aligned:
                # Calculate Trend Age
                age = 0
                if "trend_series" in signal_ltf.metadata:
                    ts = signal_ltf.metadata["trend_series"]
                    if len(ts) >= 2:
                        curr_trend = ts.iloc[-1]
                        # Count backwards (how many candles has this trend existed?)
                        for i in range(1, len(ts)):
                            if ts.iloc[-i] == curr_trend:
                                age += 1
                            else:
                                break
                
                if age <= index_max_age:
                     should_scan_options = True
                else:
                    # Optional: Log rejection if debugging
                    # print(f"[SKIP] Index trend too old (Age: {age} > Max: {index_max_age})")
                    pass

        if should_scan_options:
            # Execute entry logic: scan manual strikes for matching setup
            await self._scan_manual_strikes(signal_ltf, signal_htf if "index_htf" in self.indicators else None)

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
        
        exit_time = self._exit_cooldowns[symbol]
        now = datetime.now()
        
        # Get cooldown duration from config (in minutes)
        re_entry_cfg = self.config.get("re_entry_protection", {})
        if not re_entry_cfg.get("enabled", True):
            return False
        
        # Use a default cooldown (can be made dynamic based on exit reason)
        cooldown_mins = re_entry_cfg.get("cooldown_after_loss_mins", 5)
        cooldown_delta = timedelta(minutes=cooldown_mins)
        
        if now - exit_time < cooldown_delta:
            remaining_secs = (exit_time + cooldown_delta - now).total_seconds()
            logger.info(f"[COOLDOWN] {symbol} blocked for {int(remaining_secs)}s more")
            return True
        
        # Cooldown expired, remove from tracking
        del self._exit_cooldowns[symbol]
        return False
    
    async def _scan_manual_strikes(self, index_ltf_signal, index_htf_signal):
        """
        Scan manual strikes basket for entry opportunities.
        
        Args:
            index_ltf_signal: Index LTF signal object
            index_htf_signal: Index HTF signal object (or None)
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
            return  # AUTO mode not implemented in modular bot yet
        
        manual_strikes = strike_cfg.get("manual_strikes", [])
        if not manual_strikes:
            logger.warning("Manual mode enabled but no manual_strikes configured")
            return
        
        # Get entry logic config
        entry_logic = self.config.get("entry_logic", {})
        index_trigger_mode = entry_logic.get("index_trigger_mode", "SIGNAL").upper()
        option_trigger_mode = entry_logic.get("option_trigger_mode", "SIGNAL").upper()
        
        # Determine which strikes to consider based on index trend
        index_trend = index_ltf_signal.trend
        trend_str = "BULLISH" if index_trend == 1 else "BEARISH"
        
        # Log start of scan cycle
        print(f"\n[SCAN] Index is {trend_str} @ {self._last_index_price:.2f}. Checking manual strikes...")
        
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
            if (is_ce and index_trend != 1):
                # print(f"[SKIP] {symbol}: Wrong side for BULLISH index")
                continue
            if (is_pe and index_trend != -1):
                # print(f"[SKIP] {symbol}: Wrong side for BEARISH index")
                continue
            
            # Fetch option data and check option-level trigger
            # print(f"[CHECK] Analyzing {symbol}...")
            await self._check_and_execute_entry(symbol, index_ltf_signal, index_htf_signal, 
                                                index_trigger_mode, option_trigger_mode)
    
    async def _check_and_execute_entry(self, symbol: str, index_ltf_signal, index_htf_signal,
                                       index_trigger_mode: str, option_trigger_mode: str):
        """
        Check option-level conditions and execute entry if all criteria met.
        """
        try:
            # Fetch option historical data
            opt_ltf_tf = self.config["option"]["ltf"]["timeframe"]
            df_opt_ltf = await self.data_provider.fetch_history(
                symbol, opt_ltf_tf, bars=100, exchange="NFO"
            )
            
            if df_opt_ltf is None or len(df_opt_ltf) < 20:
                print(f"[SKIP] {symbol}: Insufficient data")
                return
            
            # Calculate option LTF indicator
            use_ha = self.config.get("option_use_ha", False)
            opt_signal_ltf = self.indicators["option_ltf"].calculate(df_opt_ltf, use_ha=use_ha)
            
            # Check option HTF alignment if enabled
            opt_htf_aligned = True
            opt_signal_htf = None  # Initialize to prevent UnboundLocalError
            if "option_htf" in self.indicators:
                opt_htf_tf = self.config["option"]["htf"]["timeframe"]
                df_opt_htf = await self.data_provider.fetch_history(
                    symbol, opt_htf_tf, bars=50, exchange="NFO"
                )
                
                if df_opt_htf is not None:
                    opt_signal_htf = self.indicators["option_htf"].calculate(df_opt_htf, use_ha=use_ha)
                    opt_htf_aligned = (opt_signal_htf.trend == opt_signal_ltf.trend)
                    
                    if not opt_htf_aligned:
                        pass # print(f"[SKIP] {symbol}: HTF Mismatch (LTF={opt_signal_ltf.trend}, HTF={opt_signal_htf.trend})")
            
            # Apply trigger logic for OPTION based on max_trend_age
            # Get settings (defaults to 8 for safety/backward compat)
            entry_logic = self.config.get("entry_logic", {})
            max_age_opt = entry_logic.get("option_max_trend_age", 8)
            
            option_entry_valid = False
            rejection_reason = "No Signal"
            
            # LOGIC 1: Sniper Mode (Age = 0)
            # Only enter on fresh crossover
            if max_age_opt == 0:
                if opt_signal_ltf.has_fresh_buy():
                    option_entry_valid = True
                else:
                    rejection_reason = "Waiting for fresh BUY signal"

            # LOGIC 2: Window Mode (Age > 0)
            else:
                # Check if in correctly aligned state
                if opt_signal_ltf.trend == 1 and opt_htf_aligned:
                    # Calculate Age
                    # We want to know how many candles BEFORE the current one were also green.
                    age = 0
                    if "trend_series" in opt_signal_ltf.metadata:
                        ts = opt_signal_ltf.metadata["trend_series"]
                        if len(ts) >= 2:
                            curr_trend = ts.iloc[-1]
                            # Start checking from the candle BEFORE the current one (-2)
                            # Current candle (-1) is "Age 0"
                            for i in range(2, len(ts) + 1):
                                if ts.iloc[-i] == curr_trend:
                                    age += 1
                                else:
                                    break
                    
                    if age <= max_age_opt:
                        option_entry_valid = True
                    else:
                        rejection_reason = f"Trend too old (Age: {age} > Max: {max_age_opt})"
                else:
                    rejection_reason = "Not in BULLISH state"
            
            # Store state for logging (regardless of outcome)
            self._strike_states[symbol] = {
                "age": age if 'age' in locals() else 0,
                "reason": rejection_reason,
                "valid": option_entry_valid,
                "trend": opt_signal_ltf.trend
            }

            if not option_entry_valid:
                # print(f"[SKIP] {symbol}: {rejection_reason}")
                return
            
            # Check max option price cap
            current_price = df_opt_ltf['Close'].iloc[-1]
            max_price = self.config.get("strike_selection", {}).get("max_option_price", 0)
            if max_price > 0 and current_price > max_price:
                logger.info(f"[SKIP] {symbol} price {current_price:.2f} > max {max_price}")
                # print(f"[SKIP] {symbol}: Price {current_price:.2f} > Limit {max_price}")
                return
            
            # All conditions met - execute entry!
            print(f"[TRIGGER] {symbol}: Valid setup found! Executing BUY...")
            
            # Determine side
            side = "CALL" if "CE" in symbol else "PUT"
            await self._execute_entry(symbol, side, current_price, opt_signal_ltf, opt_signal_htf)
            
        except Exception as e:
            logger.error(f"Entry check error for {symbol}: {e}", exc_info=True)
            self._set_cooldown(symbol, 60) # Cooldown on high-level failure
    

    async def _execute_entry(self, symbol: str, side: str, price: float, ltf_signal, htf_signal):
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
            
            order_params = {
                "symbol": symbol,
                "exchange": "NFO",
                "transaction_type": "BUY",
                "quantity": quantity,
                "product": self.config.get("product_type", "MIS"),
                "order_type": "MARKET"
            }
            
            order_id = await self.order_manager.place_order(order_params)
            
            if order_id:
                print(f"[SUCCESS] Order placed: {order_id}")
                # Initialize trade state
                trade = Trade(
                    symbol=symbol,
                    entry_price=price, # Approximation, will sync later
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
            self._set_cooldown(symbol, 60) # 1 min cooldown on failure
                
        except Exception as e:
            logger.error(f"Entry execution error for {symbol}: {e}", exc_info=True)
            print(f"[ERROR] Entry execution crashed: {e}")
            self._set_cooldown(symbol, 60) # 1 min cooldown on crash
    
    def _set_cooldown(self, symbol: str, seconds: int):
        """Set cooldown for a symbol"""
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
            
            # Run every 1 second (fast for TSL)
            await asyncio.sleep(1)
    
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
                self._exit_cooldowns[trade.symbol] = datetime.now()
                
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
            
            # Run every 10 seconds
            await asyncio.sleep(10)
    
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
