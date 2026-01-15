"""
Backtest Engine - Core Simulation Logic

Replays historical data through the exact same strategy logic used in live trading.
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import sys
import os

# Add parent directory to import live bot components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy import StrategyEngine
from indicators import calculate_ema, calculate_rsi, calculate_stochrsi, calculate_utbot
from config import config
from backtest.option_pricer import OptionPricer

class BacktestEngine:
    """
    Simulates trading strategy on historical data using the same logic as the live bot.
    """
    
    def __init__(self, initial_capital: float = 100000):
        self.logger = logging.getLogger("BacktestEngine")
        self.strategy = StrategyEngine()
        self.pricer = OptionPricer()
        
        # Portfolio state
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.equity = initial_capital
        self.total_brokerage = 0.0
        self.brokerage_per_order = 7.0
        
        # Trade tracking
        self.trades: List[Dict] = []
        self.active_position: Optional[Dict] = None
        self.equity_curve: List[Dict] = []
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.max_drawdown = 0.0
        self.peak_equity = initial_capital
        
    def calculate_indicators(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Apply all necessary indicators to the dataframe"""
        active_htf = config.get("active_indicators.htf", [])
        active_ltf = config.get("active_indicators.ltf", [])
        active = active_htf if timeframe == "HTF" else active_ltf
        
        if "ema" in active:
            df = calculate_ema(df, config.get("indicators.ema_fast", 9))
            df = calculate_ema(df, config.get("indicators.ema_slow", 21))
        
        if "rsi" in active:
            df = calculate_rsi(df, config.get("indicators.rsi_period", 14))
        
        if "stochrsi" in active:
            df = calculate_stochrsi(df)
        
        if "utbot" in active:
            df = calculate_utbot(df, 
                               config.get("indicators.utbot_key", 1), 
                               config.get("indicators.utbot_atr", 10))
        
        return df
    
    def get_strike_price(self, spot_price: float, strike_step: int = 0) -> float:
        """
        Calculate strike price based on spot and step.
        Nifty strikes are in 50 multiples, BankNifty in 100 multiples.
        """
        step_size = 50  # Nifty
        atm = round(spot_price / step_size) * step_size
        return atm + (strike_step * step_size)
    
    def enter_position(self, 
                      signal: Dict, 
                      spot_price: float, 
                      timestamp: datetime,
                      days_to_expiry: int = 3):
        """Simulate entering an options position"""
        
        strike_step = config.get("strike_selection.strike_step", 0)
        strike_price = self.get_strike_price(spot_price, strike_step)
        option_type = signal['type']  # CE or PE
        
        # Estimate option premium
        entry_premium = self.pricer.estimate_option_price(
            spot_price=spot_price,
            strike_price=strike_price,
            option_type=option_type,
            days_to_expiry=days_to_expiry
        )
        
        # Position sizing
        lots = config.get("position_sizing.lots_per_trade", 1)
        lot_size = 50  # Nifty lot size (adjust for BankNifty = 15)
        qty = lots * lot_size
        
        # Deduct cost + brokerage
        position_cost = entry_premium * qty
        self.cash -= (position_cost + self.brokerage_per_order)
        self.total_brokerage += self.brokerage_per_order
        
        self.active_position = {
            'entry_time': timestamp,
            'entry_spot': spot_price,
            'strike': strike_price,
            'type': option_type,
            'entry_premium': entry_premium,
            'qty': qty,
            'peak_premium': entry_premium,
            'lots': lots
        }
        
        self.logger.info(f"ENTRY: {option_type} Strike={strike_price} @ Rs.{entry_premium} | Spot={spot_price:.2f}")
    
    def exit_position(self, 
                     spot_price: float, 
                     timestamp: datetime,
                     reason: str,
                     days_to_expiry: int = 3):
        """Simulate exiting the active position"""
        
        if not self.active_position:
            return
        
        pos = self.active_position
        
        # Calculate exit premium
        exit_premium = self.pricer.estimate_option_price(
            spot_price=spot_price,
            strike_price=pos['strike'],
            option_type=pos['type'],
            days_to_expiry=days_to_expiry
        )
        
        # Calculate PnL
        pnl_per_lot = (exit_premium - pos['entry_premium']) * pos['qty']
        net_pnl = pnl_per_lot - self.brokerage_per_order
        
        self.cash += (exit_premium * pos['qty']) - self.brokerage_per_order
        self.total_brokerage += self.brokerage_per_order
        
        # Record trade
        trade = {
            'entry_time': pos['entry_time'],
            'exit_time': timestamp,
            'type': pos['type'],
            'strike': pos['strike'],
            'entry_premium': pos['entry_premium'],
            'exit_premium': exit_premium,
            'entry_spot': pos['entry_spot'],
            'exit_spot': spot_price,
            'qty': pos['qty'],
            'pnl': net_pnl,
            'exit_reason': reason,
            'duration': (timestamp - pos['entry_time']).total_seconds() / 60  # minutes
        }
        
        self.trades.append(trade)
        self.total_trades += 1
        
        if net_pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        self.logger.info(f"EXIT: {pos['type']} @ Rs.{exit_premium} | PnL: Rs.{net_pnl:+.2f} | Reason: {reason}")
        
        self.active_position = None
    
    def check_exit_conditions(self, 
                             spot_price: float, 
                             timestamp: datetime,
                             current_atr: float = 0,
                             days_to_expiry: int = 3):
        """Check if active position should be closed (TSL, Target, etc.)"""
        
        if not self.active_position:
            return
        
        pos = self.active_position
        
        # Estimate current premium
        current_premium = self.pricer.estimate_option_price(
            spot_price=spot_price,
            strike_price=pos['strike'],
            option_type=pos['type'],
            days_to_expiry=days_to_expiry
        )
        
        # Update peak
        if current_premium > pos['peak_premium']:
            pos['peak_premium'] = current_premium
        
        entry = pos['entry_premium']
        
        # Fixed Stop Loss
        sl_pct = config.get("risk_management.stop_loss_pct", 30)
        sl_price = entry * (1 - (sl_pct / 100))
        
        # Trailing Stop Loss
        tsl_mode = config.get("risk_management.tsl_mode", "PERCENT")
        if tsl_mode == "ATR" and current_atr > 0:
            multiplier = config.get("risk_management.tsl_atr_multiplier", 2.0)
            tsl_price = pos['peak_premium'] - (current_atr * multiplier)
        else:
            tsl_pct = config.get("risk_management.trailing_stop_pct", 10)
            tsl_price = pos['peak_premium'] * (1 - (tsl_pct / 100))
        
        # Profit Lock
        activation = config.get("risk_management.trailing_activation_pct", 10)
        lock = config.get("risk_management.profit_lock_pct", 2)
        profit_lock_price = 0
        curr_peak_pct = ((pos['peak_premium'] - entry) / entry) * 100
        
        if curr_peak_pct >= activation:
            profit_lock_price = entry * (1 + (lock / 100))
        
        effective_stop = max(sl_price, tsl_price, profit_lock_price)
        
        # Check Stop Loss
        if current_premium <= effective_stop:
            reason = "TSL Hit"
            if effective_stop == sl_price:
                reason = "SL Hit"
            if effective_stop == profit_lock_price:
                reason = "Profit Lock Hit"
            
            self.exit_position(spot_price, timestamp, reason, days_to_expiry)
            return
        
        # Check Target
        target_pct = config.get("risk_management.target_profit_pct", 0)
        if target_pct > 0:
            target_price = entry * (1 + (target_pct / 100))
            if current_premium >= target_price:
                self.exit_position(spot_price, timestamp, "Target Hit", days_to_expiry)
    
    def update_equity(self, spot_price: float, timestamp: datetime, days_to_expiry: int = 3):
        """Update total equity (cash + unrealized position value)"""
        
        position_value = 0
        if self.active_position:
            pos = self.active_position
            current_premium = self.pricer.estimate_option_price(
                spot_price=spot_price,
                strike_price=pos['strike'],
                option_type=pos['type'],
                days_to_expiry=days_to_expiry
            )
            position_value = current_premium * pos['qty']
        
        self.equity = self.cash + position_value
        
        # Track peak and drawdown
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        
        dd = ((self.peak_equity - self.equity) / self.peak_equity) * 100
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        
        self.equity_curve.append({
            'timestamp': timestamp,
            'equity': self.equity,
            'cash': self.cash,
            'drawdown': dd
        })
    
    def get_statistics(self) -> Dict:
        """Calculate comprehensive performance statistics"""
        
        total_return = ((self.equity - self.initial_capital) / self.initial_capital) * 100
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        if self.trades:
            avg_win = sum([t['pnl'] for t in self.trades if t['pnl'] > 0]) / max(self.winning_trades, 1)
            avg_loss = sum([t['pnl'] for t in self.trades if t['pnl'] < 0]) / max(self.losing_trades, 1)
            profit_factor = abs(avg_win * self.winning_trades / (avg_loss * self.losing_trades)) if self.losing_trades > 0 else float('inf')
        else:
            avg_win = 0
            avg_loss = 0
            profit_factor = 0
        
        return {
            'initial_capital': self.initial_capital,
            'final_equity': self.equity,
            'total_return': total_return,
            'total_pnl': self.equity - self.initial_capital,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': self.max_drawdown,
            'total_brokerage': self.total_brokerage
        }
