import logging
import time
from typing import Dict, Any, List
from config import config

class OrderManager:
    def __init__(self, api_client):
        self.api = api_client
        self.logger = logging.getLogger("OrderManager")
        self.active_positions = [] # List of dicts
        
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
            # Real implementation needs Option Chain Iteration
            # Mock implementation just returns ATM for now
            self.logger.info("Premium Selection Mode requested (Mocking ATM)")
            return self.get_option_symbol("NIFTY", atm, action_type)
            
        return "UNKNOWN"

    def place_entry_order(self, signal: Dict[str, Any], spot_price: float) -> bool:
        """
        Places entry order.
        """
        # 1. GAP CHECK (Cool-down)
        # Instrument-Specific (NIFTY vs BANKNIFTY)
        # In V1 we hardcode 'NIFTY' for simplicity inside select_strike but logic applies per symbol
        symbol = "NIFTY" 
        
        min_gap = config.get("strategy_settings.min_gap_minutes", 15)
        last_exit = self.last_exit_times.get(symbol, 0)
        
        if time.time() - last_exit < (min_gap * 60):
            self.logger.warning(f"🚫 Gap Timer Active for {symbol}. Ignoring Trade.")
            return False

        # 2. Select Symbol
        trading_symbol = self.select_strike(spot_price, signal['type'])
        
        # 3. Place Order (Mock)
        qty = 50 # Default Lot
        self.logger.info(f"🚀 PLACING ORDER: Buy {trading_symbol} Qty={qty} (Signal={signal['type']})")
        
        try:
            # response = self.api.placeorder(...) 
            # Mock Success:
            order_id = f"ORDER_{int(time.time())}"
            
            # Record Virtual Position
            self.active_positions.append({
                'symbol': trading_symbol,
                'qty': qty,
                'entry_price': 100.0, # Dummy Entry Price
                'entry_time': time.time(),
                'sl': 0, # Will be set by Risk Manager
                'target': 0,
                'peak_price': 100.0, # For TSL
                'type': signal['type']
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
