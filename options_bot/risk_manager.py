import logging
import time
from config import config

class RiskManager:
    # Trailing Stop Loss (TSL) settings are linked to config.json:
    # trailing_stop_pct: Distance to follow. trailing_activation_pct: When to start.
    def __init__(self, order_manager):
        self.om = order_manager
        self.logger = logging.getLogger("RiskManager")
        self.daily_pnl = 0.0
        self.realized_pnl_map = {} # {symbol: total_pnl}
        self.total_brokerage = 0.0
        self.brokerage_per_order = 7.0 # ₹7 per buy/sell order
        
    def record_buy_order(self):
        """Deducts brokerage for a buy order."""
        self.total_brokerage += self.brokerage_per_order
        self.daily_pnl -= self.brokerage_per_order

    def record_sell_order(self, symbol: str, net_trade_pnl: float):
        """Records a sell order, deducts brokerage and updates PnL."""
        self.total_brokerage += self.brokerage_per_order
        final_pnl = net_trade_pnl - self.brokerage_per_order
        self.daily_pnl += final_pnl
        self.realized_pnl_map[symbol] = self.realized_pnl_map.get(symbol, 0) + final_pnl

    def check_exit_conditions(self, current_prices: dict, current_atr: float = 0):
        """
        Iterates active positions and checks SL/Target/TSL.
        current_prices: {'NIFTY28DEC...': 120.5, ...}
        current_atr: Current ATR from the index (NIFTY)
        """
        max_daily_loss = config.get("risk_management.max_daily_acceptable_loss", 0)
        
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
            
            # --- 1. SL & TSL LOGIC ---
            
            # LINE A: Fixed Hard Stop Loss (Percentage based)
            sl_pct = pos.get('sl_pct', config.get("risk_management.stop_loss_pct", 30))
            sl_price = entry * (1 - (sl_pct / 100))
            
            # LINE B: Trailing Stop Loss (Dynamic)
            tsl_mode = config.get("risk_management.tsl_mode", "PERCENT")
            if tsl_mode == "ATR" and current_atr > 0:
                multiplier = config.get("risk_management.tsl_atr_multiplier", 2.0)
                # Distance = ATR * Multiplier (applied to option price)
                # Note: Index ATR is used as a proxy for option volatility here
                tsl_price = pos['peak_price'] - (current_atr * multiplier)
            else:
                # PERCENT Mode
                tsl_pct = pos.get('tsl_pct', config.get("risk_management.trailing_stop_pct", 10))
                tsl_price = pos['peak_price'] * (1 - (tsl_pct / 100))
            
            # LINE C: Profit Lock (Floor)
            activation = pos.get('tsl_activation_pct', config.get("risk_management.trailing_activation_pct", 10))
            lock = pos.get('profit_lock_pct', config.get("risk_management.profit_lock_pct", 2))
            
            profit_lock_price = 0
            curr_peak_pct = ((pos['peak_price'] - entry) / entry) * 100
            
            if curr_peak_pct >= activation:
                profit_lock_price = entry * (1 + (lock / 100))
            
            # Effective Stop = MAX(Fixed SL, TSL Line, Profit Lock)
            effective_stop = max(sl_price, tsl_price, profit_lock_price)
            
            # CHECK EXIT
            if ltp <= effective_stop:
                reason = "TSL Hit"
                if effective_stop == sl_price: reason = "SL Hit"
                if effective_stop == profit_lock_price: reason = "Profit Lock Hit"
                
                self.logger.info(f"🔻 {reason.upper()}: {symbol} @ {ltp} (Stop: {effective_stop:.2f})")
                self.om.close_position(pos, reason)
                self.record_sell_order(symbol, (ltp - entry) * qty)
                continue # Position closed, skip target check
                
            # --- 2. TARGET CHECK ---
            target_pct = pos.get('target_pct', config.get("risk_management.target_profit_pct", 0))
            
            # Zero Target = Unlimited (Skip this block)
            if target_pct > 0:
                target_price = entry * (1 + (target_pct / 100))
                if ltp >= target_price:
                     self.logger.info(f"🎯 TARGET HIT: {symbol} @ {ltp}")
                     self.om.close_position(pos, "Target Hit")
                     self.record_sell_order(symbol, (ltp - entry) * qty)

    def check_pre_entry_risk(self) -> bool:
        """Checks if we are allowed to enter new trades."""
        # 1. Max Positions Check
        max_pos = config.get("risk_management.max_positions", 2)
        if len(self.om.active_positions) >= max_pos:
            return False
            
        # 2. Daily Loss Check (Stop New Entries)
        max_daily_loss = config.get("risk_management.max_daily_acceptable_loss", 1000)
        if max_daily_loss > 0 and self.daily_pnl < -max_daily_loss:
            return False
            
        return True


"""
================================================================================
WORKED EXAMPLE: "HIGHEST WINS" TSL SYSTEM IN ACTION
================================================================================

Entry Price: ₹100
Config: SL=30%, TSL=5%, Profit Lock=1% (activates at 3% profit), Target=50%

SCENARIO: Price rises to ₹120, then drops to ₹113

┌─────────────────────────────────────────────────────────────────────────────┐
│ TICK 1: Entry                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ Price: ₹100 | Peak: ₹100                                                   │
│ Line A (Fixed SL):    ₹100 - 30% = ₹70                                     │
│ Line B (TSL):         ₹100 - 5%  = ₹95                                     │
│ Line C (Profit Lock): Not activated (need 3% profit first)                 │
│ Effective Stop = max(₹70, ₹95, 0) = ₹95                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TICK 2: Small profit                                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ Price: ₹103 | Peak: ₹103 (3% profit - Profit Lock ACTIVATES!)              │
│ Line A (Fixed SL):    ₹70 (unchanged)                                      │
│ Line B (TSL):         ₹103 - 5% = ₹97.85                                   │
│ Line C (Profit Lock): ₹100 + 1% = ₹101 ✅ (now active, never moves)        │
│ Effective Stop = max(₹70, ₹97.85, ₹101) = ₹101                             │
│ → Breakeven protection is now in place!                                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TICK 3: Good profit                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Price: ₹110 | Peak: ₹110 (10% profit)                                      │
│ Line A (Fixed SL):    ₹70 (unchanged)                                      │
│ Line B (TSL):         ₹110 - 5% = ₹104.50 (moved UP! 📈)                   │
│ Line C (Profit Lock): ₹101 (static, never moves)                           │
│ Effective Stop = max(₹70, ₹104.50, ₹101) = ₹104.50                         │
│ → TSL is now the dominant protector (highest value)                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TICK 4: Peak profit                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Price: ₹120 | Peak: ₹120 (20% profit)                                      │
│ Line A (Fixed SL):    ₹70 (unchanged)                                      │
│ Line B (TSL):         ₹120 - 5% = ₹114 (moved UP again! 📈)                │
│ Line C (Profit Lock): ₹101 (static)                                        │
│ Effective Stop = max(₹70, ₹114, ₹101) = ₹114                               │
│ → If price drops below ₹114, we exit with 14% profit                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TICK 5: Price reversal                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Price: ₹113 | Peak: ₹120 (still, peak doesn't drop)                        │
│ Line A (Fixed SL):    ₹70                                                  │
│ Line B (TSL):         ₹120 - 5% = ₹114 (unchanged, trails peak)            │
│ Line C (Profit Lock): ₹101                                                 │
│ Effective Stop = max(₹70, ₹114, ₹101) = ₹114                               │
│ Current Price (₹113) < Effective Stop (₹114)                               │
│ → 🔻 TSL HIT! Position closed at ₹114                                      │
│ → Profit booked: ₹14 (14% gain) 🎉                                         │
└─────────────────────────────────────────────────────────────────────────────┘

UNDERSTANDING THE 1:2 RATIO (Lock : Buffer):
─────────────────────────────────────────────
The "1:2 ratio" refers to the relationship between the lock level and the buffer:

  Activation Point: 3% profit (₹103)
  Lock Point:       1% profit (₹101)
  Buffer:           3% - 1% = 2% (₹103 → ₹101)
  
  Ratio = Lock : Buffer
        = 1%   : 2%
        = 1    : 2  ✅

Visual representation:
  Entry: ₹100
         ↓
         ├─ +1% (₹101) ← Profit Lock Floor (static, never moves)
         │
         ├─ +2% (₹102) ← Buffer zone (breathing room)
         │
         └─ +3% (₹103) ← Activation Trigger

Why this matters:
- The 2% buffer prevents premature exits from normal market noise
- Gives the trade "breathing room" after hitting activation threshold
- Ensures you don't get shaken out immediately after lock activates
- Allows TSL to take over as the dominant protector for larger profits

KEY INSIGHTS:
1. Fixed SL (₹70) protected us at entry - if price gapped down immediately
2. Profit Lock (₹101) ensured we couldn't go negative after hitting 3% profit
3. TSL (5% trail) did the heavy lifting - moved UP with price, locked in 14% gain
4. We gave back only 6% (₹120 → ₹114) instead of the full 20% profit

This is why it's called "HIGHEST WINS" - the bot always uses the safest (highest)
exit price among the three protective lines!
================================================================================
"""
