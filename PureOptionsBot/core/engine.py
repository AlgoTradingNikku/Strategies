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
            await self._setup_websocket()
        
        # Launch background tasks
        tasks = [
            self.signal_scanner_task(),
            self.risk_monitor_task(),
            self.position_sync_task(),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            await self.stop()
        except Exception as e:
            logger.error(f"Engine error: {e}", exc_info=True)
            await self.stop()
    
    async def _setup_websocket(self):
        """Setup WebSocket connection and subscribe to symbols"""
        try:
            # Get manual strikes from config
            strike_cfg = self.config.get("strike_selection", {})
            mode = strike_cfg.get("mode", "AUTO").upper()
            
            symbols_to_subscribe = []
            
            if mode == "MANUAL":
                manual_strikes = strike_cfg.get("manual_strikes", [])
                symbols_to_subscribe = manual_strikes
            
            if not symbols_to_subscribe:
                return
            
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
                self._ws_connected = True
                print("[INFO] Websocket Connected.")
                
                # Subscribe to LTP for all symbols
                self.client.subscribe_ltp(
                    instruments,
                    on_data_received=self._on_ws_ltp_update
                )
                
                self._ws_subscribed_symbols = symbols_to_subscribe
                now = datetime.now().strftime("%H:%M:%S")
                print(f"[{now}] [WS] Subscribed to new symbols: {symbols_to_subscribe}")
                
            except Exception as e:
                logger.warning(f"WebSocket connection failed: {e}")
                print(f"[WARN] WebSocket connection failed: {e}")
                self._ws_connected = False
                
        except Exception as e:
            logger.debug(f"WebSocket setup error: {e}")
    
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
        
        # Determine trend strings
        ltf_trend_str = "BULLISH" if signal_ltf.trend == 1 else "BEARISH" if signal_ltf.trend == -1 else "NEUTRAL"
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
            print(f"[{now}] [IDLE] Scanning {index_query} @ {index_price:.2f}... ({pos_status})")
        
        # Check for new signal
        if signal_ltf.has_fresh_buy() or signal_ltf.has_fresh_sell():
            if htf_aligned:
                logger.info(
                    f"[SIGNAL] Index {index_query}: "
                    f"{'BUY' if signal_ltf.trend == 1 else 'SELL'} "
                    f"Signal={signal_ltf.signal}, HTF Aligned={htf_aligned}"
                )
                
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
            if (is_ce and index_trend != 1) or (is_pe and index_trend != -1):
                continue
            
            # Fetch option data and check option-level trigger
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
                return
            
            # Calculate option LTF indicator
            use_ha = self.config.get("option_use_ha", False)
            opt_signal_ltf = self.indicators["option_ltf"].calculate(df_opt_ltf, use_ha=use_ha)
            
            # Check option HTF alignment if enabled
            opt_htf_aligned = True
            if "option_htf" in self.indicators:
                opt_htf_tf = self.config["option"]["htf"]["timeframe"]
                df_opt_htf = await self.data_provider.fetch_history(
                    symbol, opt_htf_tf, bars=50, exchange="NFO"
                )
                
                if df_opt_htf is not None:
                    opt_signal_htf = self.indicators["option_htf"].calculate(df_opt_htf, use_ha=use_ha)
                    opt_htf_aligned = (opt_signal_htf.trend == opt_signal_ltf.trend)
            
            # Apply trigger mode logic for OPTION
            option_entry_valid = False
            
            if option_trigger_mode == "SIGNAL":
                # Wait for fresh signal
                option_entry_valid = opt_signal_ltf.has_fresh_buy()
            elif option_trigger_mode == "STATE":
                # Check if in correct state (both LTF+HTF aligned)
                option_entry_valid = (opt_signal_ltf.trend == 1 and opt_htf_aligned)
                
                # Apply trend age filter if configured
                max_age = self.config.get("entry_logic", {}).get("option_max_trend_age", 8)
                if option_entry_valid and max_age > 0:
                    # Would need to implement trend age calculation like old bot
                    # For now, skip age check
                    pass
            
            if not option_entry_valid:
                return
            
            # Check max option price cap
            current_price = df_opt_ltf['Close'].iloc[-1]
            max_price = self.config.get("strike_selection", {}).get("max_option_price", 0)
            if max_price > 0 and current_price > max_price:
                logger.info(f"[SKIP] {symbol} price {current_price:.2f} > max {max_price}")
                return
            
            # All conditions met - execute entry!
            await self._execute_entry(symbol, current_price, index_ltf_signal.trend)
            
        except Exception as e:
            logger.error(f"Entry check error for {symbol}: {e}", exc_info=True)
    
    async def _execute_entry(self, symbol: str, entry_price: float, index_trend: int):
        """
        Execute BUY order and create Trade object.
        
        Args:
            symbol: Option symbol to buy
            entry_price: Current price (for reference)
            index_trend: Index trend (1=bullish, -1=bearish)
        """
        try:
            # Determine side from symbol
            side = "CALL" if "CE" in symbol else "PUT"
            
            # Get lot size from config
            lots = self.config.get("lots", 1)
            # Standard NSE lot size for NIFTY options is 75
            # TODO: Fetch actual lot size from symbol master
            quantity = lots * 75
            
            # Check live trade mode
            live_mode = self.config.get("live_trade", False)
            
            if not live_mode:
                logger.info(f"[PAPER MODE] Would BUY {symbol} @ ₹{entry_price:.2f} Qty={quantity}")
                return
            
            # Place order via OrderManager
            print(f"[ENTRY] Placing BUY order for {symbol} @ ₹{entry_price:.2f} Qty={quantity}")
            
            result = await self.order_manager.place_order(
                symbol=symbol,
                action="BUY",
                quantity=quantity,
                order_type="MARKET",
                exchange="NFO",
                product="MIS"
            )
            
            if result.success:
                # Create Trade object
                trade = Trade(
                    symbol=symbol,
                    side=side,
                    entry_price=result.filled_price or entry_price,
                    quantity=quantity,
                    state=TradeState.POSITION,
                    entry_time=datetime.now(),
                    tsl_level=0.0,
                    highest_price=result.filled_price or entry_price,
                    index_trend=index_trend
                )
                
                # Store and persist
                self.trades[symbol] = trade
                self.persistence.save_trade(trade)
                
                print(f"[POSITION] Entered {symbol} @ ₹{trade.entry_price:.2f} | Order ID: {result.order_id}")
                logger.info(f"Entry executed: {symbol} @ {trade.entry_price:.2f}")
            else:
                logger.error(f"Order failed for {symbol}: {result.message}")
                
        except Exception as e:
            logger.error(f"Entry execution error for {symbol}: {e}", exc_info=True)
    

    async def risk_monitor_task(self):
        """
        Monitor active positions for risk management (every 1s).
        
        Flow:
        1. Get live prices for all active positions
        2. Evaluate TSL/profit guards
        3. Execute exits if needed
        """
        logger.info("[TASK] Risk monitor started")
        
        while self.running:
            try:
                await self._monitor_risk()
            except Exception as e:
                logger.error(f"Risk monitor error: {e}", exc_info=True)
            
            # Run every 1 second (fast for TSL)
            await asyncio.sleep(1)
    
    async def _monitor_risk(self):
        """Monitor risk for all active positions"""
        for symbol, trade in list(self.trades.items()):
            if trade.state != TradeState.POSITION:
                continue
            
            # Get live price
            price = await self.data_provider.get_live_price(symbol, exchange="NFO")
            if price is None:
                continue
            
            # Evaluate risk
            decision = self.risk_manager.evaluate(trade, price, is_trend_reversed=False)
            
            # Update TSL level
            if decision.new_tsl_level > 0:
                trade = Trade(**{
                    **trade.__dict__,
                    "tsl_level": decision.new_tsl_level,
                    "last_stage": decision.new_stage
                })
                self.trades[symbol] = trade
                self.persistence.save_trade(trade)
            
            # Exit if needed
            if decision.should_exit:
                logger.info(f"[EXIT] {symbol}: {decision.message}")
                await self._execute_exit(trade, decision.reason.value)
    
    async def _execute_exit(self, trade: Trade, reason: str):
        """Execute exit order"""
        # Transition to EXITING
        trade = TradeStateMachine.transition(trade, TradeState.EXITING)
        self.trades[trade.symbol] = trade
        self.persistence.save_trade(trade)
        
        # Place exit order
        result = await self.order_manager.place_order(
            symbol=trade.symbol,
            action="SELL" if trade.side == "CALL" else "BUY",  # Close position
            quantity=trade.quantity,
            order_type="MARKET"
        )
        
        if result.success:
            # Update trade to EXITED
            trade = TradeStateMachine.transition(trade, TradeState.EXITED, reason)
            trade = Trade(**{
                **trade.__dict__,
                "current_price": result.filled_price or trade.current_price
            })
            
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
            logger.error(f"Exit order failed for {trade.symbol}: {result.message}")
            # Retry or alert
    
    async def position_sync_task(self):
        """
        Sync positions with broker (every 10s).
        
        Detects external position closures and updates state accordingly.
        """
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
