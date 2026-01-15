import logging
import time
from typing import Dict, Any, List
from config import config

class OrderManager:
    def __init__(self, api_client, ws_handler=None):
        self.api = api_client
        self.ws_handler = ws_handler
        self.logger = logging.getLogger("OrderManager")
        self.active_positions = [] # List of dicts
        self.trade_history = []
        
        # Gap Timer Tracker: REMOVED
        self.traded_candles = {} # {symbol: 'timestamp_str'} - NEW: One Trade Per Candle
        self.risk_manager = None # Will be set during main initialization
        self.consecutive_loss_count = 0 # Track consecutive losses for Re-entry Limit
        self.closed_positions_count = 0 # Track total closed trades (Used to distinguish Recovery vs Pullback)
        self.reversal_confirmation_count = 0 # Track consecutive opposite signals for confirmation

    def sync_positions(self):
        """Fetches active positions from the broker and populates active_positions."""
        try:
            live_trading = config.get("live_trading", False)
            if not live_trading:
                return

            self.logger.info("🔄 Syncing active positions from broker...")
            # OpenAlgo API: positions() returns list of dicts
            positions = self.api.positions()
            
            if not positions:
                self.logger.info("ℹ️ No active positions found in broker.")
                return

            # Filter for NIFTY options (or relevant symbols)
            # Position typically has: symbol, quantity, average_price, product, etc.
            for pos in positions:
                qty = float(pos.get('quantity', 0))
                if qty != 0:
                    symbol = pos.get('symbol', '')
                    # Simplified matching: if it's an option symbol (contains CE/PE and NIFTY)
                    if "NIFTY" in symbol and ("CE" in symbol or "PE" in symbol):
                        # Don't add if already tracked
                        if not any(p['symbol'] == symbol for p in self.active_positions):
                            self.active_positions.append({
                                'symbol': symbol,
                                'ws_key': f"{symbol}.NFO",
                                'qty': abs(qty),
                                'entry_price': float(pos.get('average_price', 0)),
                                'entry_time': time.time(), # We don't know exact time, so use now
                                'peak_price': float(pos.get('average_price', 0)),
                                'type': 'CE' if "CE" in symbol else 'PE',
                                'order_id': 'SYNCED',
                                # Use default risk settings
                                'sl_pct': config.get("risk_management.entry_stop_loss_pct", 30),
                                'target_pct': config.get("risk_management.target_profit_pct", 50),
                                'tsl_pct': config.get("risk_management.trailing_stop_pct", 5),
                                'tsl_activation_pct': config.get("risk_management.trailing_activation_pct", 3),
                                'profit_lock_pct': config.get("risk_management.profit_lock_pct", 1)
                            })
                            self.logger.info(f"✅ Synced existing position: {symbol} | Qty: {qty}")
                            # Subscribe to synced symbol
                            if self.ws_handler:
                                self.ws_handler.subscribe([f"{symbol}.NFO"])
        except Exception as e:
            self.logger.error(f"❌ Failed to sync positions: {e}")

    def get_atm_strike(self, spot_price: float, step: int = 50) -> int:
        """Calculates ATM strike based on spot price."""
        return round(spot_price / step) * step

    def get_option_symbol(self, base_symbol: str, strike: int, option_type: str) -> str:
        from utils import get_expiry_date
        
        # 1. Try to fetch from API for 100% accuracy
        expiry_str = None
        if hasattr(self.api, 'get_expiries'):
            expiries = self.api.get_expiries(base_symbol)
            if expiries:
                target_type = config.get("strike_selection.expiry_type", "CURRENT_WEEKLY")
                if target_type == "NEXT_WEEKLY" and len(expiries) > 1:
                    expiry_str = expiries[1]
                else:
                    expiry_str = expiries[0]
                self.logger.info(f"📅 Using API-provided expiry for {base_symbol}: {expiry_str}")
        
        # 2. Fallback to calculation if API fails
        if not expiry_str:
            target_type = config.get("strike_selection.expiry_type", "CURRENT_WEEKLY")
            expiry_str = get_expiry_date(base_symbol, target_type)
        
        return f"{base_symbol}{expiry_str}{strike}{option_type}"

    def select_strike(self, spot_price: float, action_type: str) -> str:
        """
        Selects strike based on Config (ATM_OFFSET vs PREMIUM).
        
        ATM_OFFSET mode: Distance from ATM
            strike_step = 0  → ATM
            strike_step = +1 → 1 strike OTM
            strike_step = -1 → 1 strike ITM
            
        PREMIUM mode: Target option premium
            Scans option chain to find strike with premium closest to target_premium
        """
        mode = config.get("strike_selection.mode", "ATM_OFFSET")
        # Default step size (Improvement: Pass symbol to select_strike to be accurate)
        step = 50 # Default NIFTY
        
        atm = self.get_atm_strike(spot_price, step)
        
        if mode == "ATM_OFFSET":
            offset = config.get("strike_selection.strike_step", 0)
            # CE: +1 is OTM (Higher Strike), -1 is ITM (Lower Strike)
            # PE: +1 is OTM (Lower Strike), -1 is ITM (Higher Strike)
            
            if action_type == "CE":
                selected_strike = atm + (offset * step)
            else:
                selected_strike = atm - (offset * step)
                
            return self.get_option_symbol("NIFTY", selected_strike, action_type)
            
        elif mode == "PREMIUM":
            target_premium = config.get("strike_selection.target_premium", 100)
            
            # Generate option chain (Mock implementation)
            # In real implementation, fetch from OpenAlgo API: api.get_option_chain(symbol, expiry)
            # For now, we simulate an option chain
            option_chain = self._generate_mock_option_chain(atm, step, action_type)
            
            # Find strike with premium closest to target
            closest_strike = atm
            min_diff = float('inf')
            
            for strike, premium in option_chain.items():
                diff = abs(premium - target_premium)
                if diff < min_diff:
                    min_diff = diff
                    closest_strike = strike
            
            self.logger.info(f"PREMIUM mode: Target ₹{target_premium}, Selected {closest_strike} with premium ₹{option_chain[closest_strike]}")
            return self.get_option_symbol("NIFTY", closest_strike, action_type)
            
        return "UNKNOWN"
    
    def _generate_mock_option_chain(self, atm: int, step: int, option_type: str) -> dict:
        """
        Generates option chain for strike selection.
        - If live_trading=true: Fetches real option chain from OpenAlgo API
        - If live_trading=false: Generates mock data for testing
        
        Returns: {strike: premium}
        """
        # Check if live trading is enabled
        live_trading = config.get("live_trading", False)
        
        if live_trading:
            # LIVE MODE: Fetch real option chain from OpenAlgo
            try:
                # Real API call
                # response = self.api.get_option_chain(
                #     symbol="NIFTY",
                #     expiry=self._get_current_expiry()
                # )
                # return self._parse_chain(response)
                raise NotImplementedError("OpenAlgo option chain API not yet integrated.")
            except Exception as e:
                self.logger.error(f"Failed to fetch option chain: {e}")
                # Fall through to mock generation
        
        # PAPER TRADING MODE: Generate mock option chain
        import random
        
        chain = {}
        # Generate 5 strikes around ATM
        for i in range(-2, 3):
            strike = atm + (i * step)
            
            # Mock premium calculation (decreases as you go OTM)
            if option_type == "CE":
                # CE: Higher strikes = Lower premium
                base_premium = 150 - (i * 25)
            else:
                # PE: Lower strikes = Lower premium
                base_premium = 150 + (i * 25)
            
            # Add some randomness
            premium = max(10, base_premium + random.randint(-10, 10))
            chain[strike] = premium
        
        return chain

    def place_entry_order(self, signal: Dict[str, Any], spot_price: float, candle_time: str = None, reason: str = "") -> bool:
        """
        Places entry order.
        """
        # 1. Instrument-Specific (NIFTY vs BANKNIFTY)
        # In V1 we hardcode 'NIFTY' for simplicity inside select_strike but logic applies per symbol
        symbol = "NIFTY" 

        # 1.5 ONE TRADE PER CANDLE CHECK
        if candle_time:
            last_traded = self.traded_candles.get(symbol)
            if last_traded == candle_time:
                 # Log only once every 30s to avoid spam... actually, since we removed last_gap_log_time, let's just log every time or use a simpler throttle if needed.
                 # For now, standard logging is fine as main loop throttles checks.
                 self.logger.warning(f"⏳ Trade ignored: Signal for candle {candle_time} already traded.")
                 return False

        # 2. MAX RE-ENTRIES CHECK
        max_retries = config.get("strategy_settings.max_reentries", 3)
        if self.consecutive_loss_count >= max_retries:
             self.logger.warning(f"⛔ Max Re-entries ({max_retries}) reached. Waiting for fresh signal.")
             # We only allow entry if it is a FRESH signal (i.e. trend changed), but logic for "Fresh" vs "Mid" is in strategy.py
             # If we are here, Strategy sent a signal. If mid-entry is ON, we get signals continuously.
             # We need to block "Mid" entries but allow "Fresh" entries. 
             # For V1 simplicity: We just BLOCK everything until self.consecutive_loss_count is reset.
             # AND we reset it externally when trend flips (in main.py).
             return False

        # 1.5. Log Actionable Signal
        self.logger.info(f"✅ SIGNAL GENERATED: BUY {signal['type']} (One Trade Per Candle Passed)")

        # 2. Select Symbol
        trading_symbol = self.select_strike(spot_price, signal['type'])
        
        # 3. Extract Base Symbol (NIFTY, BANKNIFTY, etc.)
        import re
        base_match = re.match(r'^([A-Z]+)', trading_symbol)
        base_symbol = base_match.group(1) if base_match else "NIFTY"
        
        # 3. Fetch Dynamic Lot Size from API
        lot_size = 50 # Default Nifty Fallback
        if hasattr(self.api, 'get_lot_size'):
            lot_size = self.api.get_lot_size(trading_symbol)
            self.logger.info(f"📊 Dynamic Lot Size for {trading_symbol}: {lot_size}")
        
        # 4. Calculate Quantity based on Config (LOTS vs CAPITAL)
        sizing_mode = config.get("position_sizing.mode", "LOTS")
        self.logger.info(f"🔍 Position Sizing Mode detected: {sizing_mode}")
        
        if sizing_mode == "LOTS":
            lots = config.get("position_sizing.lots_per_trade", 1)
            qty = lots * lot_size
            
            # Fetch LTP just for logging/records
            option_ltp = 100.0
            if hasattr(self.api, 'get_ltp'):
                quote = self.api.get_ltp(trading_symbol, "NFO")
                if quote and quote.get('ltp'):
                    option_ltp = quote.get('ltp')
        else:
            # CAPITAL mode
            capital = config.get("position_sizing.capital_per_trade", 10000)
            
            # Get current option price for calculation
            option_ltp = 100.0 # Default fallback
            if hasattr(self.api, 'get_ltp'):
                quote = self.api.get_ltp(trading_symbol, "NFO")
                if quote and quote.get('ltp'):
                    option_ltp = quote.get('ltp')
            
            # Qty = (Capital // Price) rounded down to nearest Lot
            max_qty_by_capital = capital // option_ltp
            qty = int(max_qty_by_capital // lot_size) * lot_size
            
            # Ensure at least 1 lot if capital allows, or use 1 lot as minimum
            if qty < lot_size:
                qty = lot_size
                self.logger.warning(f"⚠️ Capital ₹{capital} insufficient for {trading_symbol} @ ₹{option_ltp}. Minimum 1 Lot ({lot_size}) forced.")
        # --- NEW: MAX PRICE CHECK ---
        max_price = config.get("risk_management.max_entry_price", 0)
        if max_price > 0 and option_ltp > max_price:
            self.logger.warning(f"⛔ Skipped Trade: Price {option_ltp} > Limit {max_price}")
            return False # Don't place order
        
        # 5. Place the Order via API
        # Cleaner logic-focused logging
        reason_short = "PB" if "Pullback" in reason else "FRESH"
        self.logger.info(f"🚀 BUY ORDER [{reason_short}]: {trading_symbol} | Price={option_ltp} | Qty={qty} {signal['type']}")
        
        # 5. Subscribe to this option in WebSocket for real-time tracking
        if self.ws_handler:
            try:
                # Format: SYMBOL.EXCHANGE
                ws_key = f"{trading_symbol}.NFO"
                self.ws_handler.subscribe([ws_key])
            except Exception as ws_err:
                self.logger.error(f"Failed to subscribe to {trading_symbol} on WS: {ws_err}")
                
        try:
            live_trading = config.get("live_trading", False)
            order_id = "MOCK_ORDER"
            
            if live_trading:
                # REAL ORDER PLACEMENT
                order_type = config.get("strategy_settings.order_type", "LIMIT")
                limit_price = option_ltp if order_type == "LIMIT" else 0
                
                response = self.api.placeorder(
                    symbol=trading_symbol,
                    action="BUY",
                    exchange="NFO",
                    quantity=qty,
                    price=limit_price,
                    product="NRML",
                    price_type=order_type
                )
                # Assuming response carries order_id
                # order_id = response['order_id'] or similar
                order_id = f"REAL_{int(time.time())}"
            else:
                # Mock Success:
                order_id = f"ORDER_{int(time.time())}"
            
            # Record Brokerage
            if self.risk_manager:
                self.risk_manager.record_buy_order()
            
            # Record Virtual Position
            self.active_positions.append({
                'symbol': trading_symbol,
                'ws_key': f"{trading_symbol}.NFO",
                'qty': qty,
                'entry_price': option_ltp,
                'entry_time': time.time(),
                'peak_price': option_ltp,
                'type': signal['type'],
                'order_id': order_id,
                
                # Snapshot current risk settings for this specific position
                'sl_pct': config.get("risk_management.entry_stop_loss_pct", 30),
                'target_pct': config.get("risk_management.target_profit_pct", 50),
                'tsl_pct': config.get("risk_management.trailing_stop_pct", 5),
                'tsl_activation_pct': config.get("risk_management.trailing_activation_pct", 3),
                'profit_lock_pct': config.get("risk_management.profit_lock_pct", 1)
            })
            
            # SUCCESS: Record this candle as traded
            if candle_time:
                self.traded_candles[symbol] = candle_time
                
            return True
            
        except Exception as e:
            self.logger.error(f"Order Placement Failed: {e}")
            return False

    def close_position(self, position: Dict, reason: str = "Signal"):
        """Closes a specific position by placing a SELL order."""
        symbol = position['symbol']
        qty = position['qty']

        # 1. Place SELL Order
        live_trading = config.get("live_trading", False)
        exit_price = position.get('entry_price', 0) # Fallback
        
        if live_trading:
            try:
                order_type = config.get("strategy_settings.order_type", "LIMIT")
                
                # Fetch latest LTP for the Limit price and Logging
                quote = self.api.get_ltp(symbol, "NFO")
                ltp = quote.get('ltp') if quote else 0
                if ltp: exit_price = ltp
                
                if order_type == "LIMIT" and exit_price <= 0:
                     self.logger.error(f"❌ Could not fetch LTP for exit limit. Symbol: {symbol}. Falling back to MARKET.")
                     order_type = "MARKET"

                response = self.api.placeorder(
                    symbol=symbol,
                    action="SELL",
                    exchange="NFO",
                    quantity=qty,
                    price=exit_price if order_type == "LIMIT" else 0,
                    product="NRML",
                    price_type=order_type
                )
                
                # VALIDATION: Check if order was accepted
                if isinstance(response, dict) and response.get('status') == 'error':
                    self.logger.error(f"❌ Critical: Exit Order Failed! Keeping position active. Message: {response.get('message')}")
                    return
            except Exception as e:
                self.logger.error(f"❌ Failed to place Exit Order: {e}")
                self.logger.error(f"⚠️ Position retained in tracker due to failure.")
                return

        # 3. Final Exit Log (Visible to User)
        self.logger.info(f"📤 SELL ORDER: {symbol} | Price={exit_price} | Qty={qty} | Reason: {reason}")

        # 3. Remove from active tracking
        if position in self.active_positions:
            self.active_positions.remove(position)
            self.closed_positions_count += 1 # Mark that we have traded this session
            
            # Update Consecutive Loss Counter
            if reason in ["SL Hit", "TSL Hit"]:
                 self.consecutive_loss_count += 1
                 self.logger.info(f"📉 Loss Recorded. Consecutive Losses: {self.consecutive_loss_count}/{config.get('strategy_settings.max_reentries', 3)}")
            elif reason in ["Target Hit", "Profit Lock Hit"]:
                 self.consecutive_loss_count = 0 # Reset on Win/Profit Lock
                 self.logger.info(f"🏆 Profit Hit ({reason})! Consecutive Losses Reset to 0.")
            
            self.logger.info(f"✅ Trade cleared from tracker.")

    def close_all(self, reason: str = "Emergency"):
        """Closes ALL positions."""
        for pos in list(self.active_positions):
            self.close_position(pos, reason)
