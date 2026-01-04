import logging
import time
from config import config

class RiskManager:
    def __init__(self, order_manager):
        self.om = order_manager
        self.logger = logging.getLogger("RiskManager")
        self.daily_pnl = 0.0
        
    def check_exit_conditions(self, current_prices: dict):
        """
        Iterates active positions and checks SL/Target/TSL.
        current_prices: {'NIFTY28DEC...': 120.5, ...}
        """
        max_daily_loss = config.get("risk_management.max_daily_loss", 1000)
        
        # 1. Circuit Breaker Check
        if max_daily_loss > 0 and self.daily_pnl < -max_daily_loss:
            self.logger.critical(f"💥 DAILY LOSS LIMIT BREACHED ({self.daily_pnl}). Stopping Trading.")
            self.om.close_all("Daily Loss Limit")
            return

        for pos in list(self.om.active_positions):
            symbol = pos['symbol']
            ltp = current_prices.get(symbol, 0)
            
            if ltp == 0: continue # No data
            
            # Update Peak Price (For TSL)
            if ltp > pos['peak_price']:
                pos['peak_price'] = ltp
                
            entry = pos['entry_price']
            qty = pos['qty']
            pnl_pct = ((ltp - entry) / entry) * 100
            
            # --- TSL LOGIC (Highest Wins) ---
            # 1. Standard SL (Fixed %)
            sl_price = entry * (1 - (config.get("risk_management.stop_loss_pct", 30) / 100))
            
            # 2. TSL Logic
            tsl_pct = config.get("risk_management.trailing_stop_pct", 5)
            # Standard TSL Line: Peak - 5%
            tsl_price = pos['peak_price'] * (1 - (tsl_pct / 100))
            
            # 3. Profit Lock (Line C)
            # If PnL > Activation (3%), Lock Profit (1%)
            activation = config.get("risk_management.trailing_activation_pct", 3)
            lock = config.get("risk_management.profit_lock_pct", 1)
            
            profit_lock_price = 0
            curr_peak_pct = ((pos['peak_price'] - entry) / entry) * 100
            
            if curr_peak_pct >= activation:
                profit_lock_price = entry * (1 + (lock / 100))
            
            # Effective Stop = MAX(Fixed SL, TSL Line, Profit Lock)
            effective_stop = max(sl_price, tsl_price, profit_lock_price)
            
            # CHECK EXIT
            if ltp <= effective_stop:
                self.logger.info(f"🔻 TSL HIT: {symbol} @ {ltp} (Stop: {effective_stop:.2f})")
                self.om.close_position(pos, "TSL Hit")
                # Update Mock PnL
                self.daily_pnl += (ltp - entry) * qty
                
            # TARGET CHECK
            target_pct = config.get("risk_management.target_profit_pct", 50)
            target_price = entry * (1 + (target_pct / 100))
            
            if ltp >= target_price:
                 self.logger.info(f"🎯 TARGET HIT: {symbol} @ {ltp}")
                 self.om.close_position(pos, "Target Hit")
                 self.daily_pnl += (ltp - entry) * qty

    def check_pre_entry_risk(self) -> bool:
        """Checks if we are allowed to enter new trades."""
        # 1. Max Positions Check
        max_pos = config.get("risk_management.max_positions", 2)
        if len(self.om.active_positions) >= max_pos:
            return False
            
        # 2. Daily Loss Check (Stop New Entries)
        max_daily_loss = config.get("risk_management.max_daily_loss", 1000)
        if max_daily_loss > 0 and self.daily_pnl < -max_daily_loss:
            return False
            
        return True
