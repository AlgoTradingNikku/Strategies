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
        
        # Gap Timer Tracker: {'NIFTY': last_exit_time_timestamp}
        self.last_exit_times = {} 

    def get_atm_strike(self, spot_price: float, step: int = 50) -> int:
        """Calculates ATM strike based on spot price."""
        return round(spot_price / step) * step

    def get_option_symbol(self, base_symbol: str, strike: int, option_type: str, expiry: str = "CURRENT") -> str:
        """
        Constructs option symbol. 
        In V1 Simulation/Mock, we return a standardized string.
        Real implementation requires Option Chain Fetching to get exact token/symbol.
        """
        # Expiry Selection Logic Placeholder
        # If expiry == "NEXT", we would shift date etc.
        expiry_str = "28DEC23" # Dummy for example
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
        step = 100 if "BANKNIFTY" in self.last_exit_times else 50 # Simplification
        
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

    def place_entry_order(self, signal: Dict[str, Any], spot_price: float) -> bool:
        """
        Places entry order.
        """
        # 1. GAP CHECK (Cool-down)
        # Instrument-Specific (NIFTY vs BANKNIFTY)
        # In V1 we hardcode 'NIFTY' for simplicity inside select_strike but logic applies per symbol
        symbol = "NIFTY" 
        
        min_gap = config.get("strategy_settings.min_gap_time", 15)
        last_exit = self.last_exit_times.get(symbol, 0)
        
        if time.time() - last_exit < (min_gap * 60):
            self.logger.warning(f"🚫 Gap Timer Active for {symbol}. Ignoring Trade.")
            return False

        # 2. Select Symbol
        trading_symbol = self.select_strike(spot_price, signal['type'])
        
        # 3. Place Order (Mock/Active)
        qty = 50 # Default Lot
        self.logger.info(f"🚀 PLACING ORDER: Buy {trading_symbol} Qty={qty} (Signal={signal['type']})")
        
        # Subscribe to this option in WebSocket for real-time tracking
        if self.ws_handler:
            try:
                # Format: SYMBOL.EXCHANGE
                ws_key = f"{trading_symbol}.NFO"
                self.ws_handler.subscribe([ws_key])
                self.logger.info(f"📡 Subscribed to {ws_key} for real-time tracking.")
            except Exception as ws_err:
                self.logger.error(f"Failed to subscribe to {trading_symbol} on WS: {ws_err}")
                
        try:
            live_trading = config.get("live_trading", False)
            order_id = "MOCK_ORDER"
            
            if live_trading:
                # REAL ORDER PLACEMENT
                # OpenAlgo Params: exchange, symbol, action, quantity, price, valid, product, price_type...
                # Docs: client.placeorder(strategy, symbol, action, exchange, price_type, product, quantity)
                response = self.api.placeorder(
                    symbol=trading_symbol,
                    action="BUY",        # was buy_sell
                    exchange="NFO",
                    quantity=qty,
                    price=0,
                    product="NRML",      # Options usually NRML
                    price_type="MARKET"  # was order_type
                )
                self.logger.info(f"API Response: {response}")
                # Assuming response carries order_id
                # order_id = response['order_id'] or similar
                order_id = f"REAL_{int(time.time())}"
            else:
                # Mock Success:
                order_id = f"ORDER_{int(time.time())}"
            
            # Record Virtual Position
            self.active_positions.append({
                'symbol': trading_symbol,
                'ws_key': f"{trading_symbol}.NFO",
                'qty': qty,
                'entry_price': spot_price if live_trading else 100.0, # Use LTP for live
                'entry_time': time.time(),
                'sl': 0, # Will be set by Risk Manager
                'target': 0,
                'peak_price': spot_price if live_trading else 100.0,
                'type': signal['type'],
                'order_id': order_id
            })
            return True
            
        except Exception as e:
            self.logger.error(f"Order Placement Failed: {e}")
            return False

    def close_position(self, position: Dict, reason: str = "Signal"):
        """Closes a specific position."""
        self.logger.info(f"🛑 CLOSING POSITION: {position['symbol']} | Reason: {reason}")
        
        # Mock Close
        # Update Gap Timer
        symbol_base = "NIFTY" # Extract base from symbol ideally
        self.last_exit_times[symbol_base] = time.time()
        
        if position in self.active_positions:
            self.active_positions.remove(position)

    def close_all(self, reason: str = "Emergency"):
        """Closes ALL positions."""
        for pos in list(self.active_positions):
            self.close_position(pos, reason)
