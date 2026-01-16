"""
Risk Manager - Centralized risk management logic.

Extracts all risk-related decisions from live_trader.py into a clean, testable module.
Handles trailing stop losses, profit guards, time-based exits, and daily limits.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum


class ExitReason(Enum):
    """Possible reasons for exiting a trade"""
    TSL_HIT = "TSL Hit"
    TREND_REVERSAL = "Trend Reversal"
    TIME_BASED = "Time-based Exit"
    MANUAL = "Manual Exit"
    DRIFT_GUARD = "Drift Guard"
    DAILY_LIMIT = "Daily Limit Reached"
    ERROR = "Error Recovery"


@dataclass
class RiskDecision:
    """
    Output from risk evaluation.
    
    Tells the engine whether to exit and why.
    """
    should_exit: bool
    reason: Optional[ExitReason] = None
    message: str = ""
    new_tsl_level: float = 0.0
    new_stage: str = ""


class TrailingStopManager:
    """
    Manages trailing stop loss calculation.
    
    Supports 3 modes:
    - ATR: Trail based on ATR distance
    - PERCENT: Trail based on percentage of price
    - POINTS: Trail based on absolute points
    
    Also implements 3-stage profit guard from current bot.
    """
    
    def __init__(self, config: dict):
        """
        Initialize TSL manager.
        
        Args:
            config: TSL configuration from config.yaml
        """
        self.config = config
        self.mode = config.get("mode", "ATR")  # ATR, PERCENT, or POINTS
        
        # ATR mode params
        self.atr_multiplier = config.get("atr_multiplier", 1.5)
        
        # Percent mode params
        self.trail_pct = config.get("trail_pct", 2.0)  # % to trail below high
        
        # Points mode params
        self.trail_points = config.get("trail_points", 50)
        
        # 3-stage profit guard (from current bot)
        self.enable_profit_guard = config.get("enable_profit_guard", True)
        self.guard_3_pct = config.get("guard_3_pct", 5.0)  # Stage 3
        self.guard_3_trail = config.get("guard_3_trail", 3.0)
        self.guard_2_pct = config.get("guard_2_pct", 3.0)  # Stage 2
        self.guard_2_trail = config.get("guard_2_trail", 2.0)
        self.guard_1_pct = config.get("guard_1_pct", 1.5)  # Stage 1
        self.guard_1_trail = config.get("guard_1_trail", 1.0)
    
    def calculate_tsl(
        self, 
        entry_price: float, 
        current_price: float, 
        highest_price: float,
        atr: float,
        last_stage: str
    ) -> Tuple[float, str]:
        """
        Calculate trailing stop level.
        
        Args:
            entry_price: Entry price
            current_price: Current price
            highest_price: Highest price seen
            atr: Current ATR value
            last_stage: Last profit guard stage ("INIT", "BE", "TRAILING", "G1", "G2", "G3")
            
        Returns:
            (tsl_level, stage) tuple
        """
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        
        # === 3-STAGE PROFIT GUARD ===
        if self.enable_profit_guard:
            # Stage 3: Aggressive protection (e.g., +5% → trail 3%)
            if pnl_pct >= self.guard_3_pct:
                tsl_level = highest_price * (1 - self.guard_3_trail / 100)
                return (tsl_level, "G3")
            
            # Stage 2: Medium protection (e.g., +3% → trail 2%)
            elif pnl_pct >= self.guard_2_pct:
                tsl_level = highest_price * (1 - self.guard_2_trail / 100)
                return (tsl_level, "G2")
            
            # Stage 1: Light protection (e.g., +1.5% → trail 1%)
            elif pnl_pct >= self.guard_1_pct:
                tsl_level = highest_price * (1 - self.guard_1_trail / 100)
                return (tsl_level, "G1")
        
        # === STANDARD TRAILING STOP ===
        if self.mode == "ATR":
            tsl_level = highest_price - (atr * self.atr_multiplier)
            return (tsl_level, "TRAILING")
        
        elif self.mode == "PERCENT":
            tsl_level = highest_price * (1 - self.trail_pct / 100)
            return (tsl_level, "TRAILING")
        
        elif self.mode == "POINTS":
            tsl_level = highest_price - self.trail_points
            return (tsl_level, "TRAILING")
        
        else:
            # Fallback: No TSL
            return (entry_price, "BE")


class RiskManager:
    """
    Central risk management engine.
    
    Evaluates all exit criteria:
    - Trailing stop loss (3-stage profit guard)
    - Trend reversal detection
    - Time-based exits
    - Daily P&L limits
    """
    
    def __init__(self, config: dict):
        """
        Initialize risk manager.
        
        Args:
            config: Full bot configuration
        """
        self.config = config
        
        # Initialize TSL manager
        tsl_config = config.get("tsl", {})
        self.tsl_manager = TrailingStopManager(tsl_config)
        
        # Time-based exit settings
        self.enable_time_exit = config.get("enable_time_based_exit", False)
        self.max_hold_minutes = config.get("max_hold_minutes", 60)
        
        # Daily limits
        self.daily_loss_limit = config.get("daily_loss_limit", -5000)
        self.daily_profit_target = config.get("daily_profit_target", 10000)
        
        # Trend reversal exit
        self.exit_on_reversal = config.get("exit_on_reversal", True)
        
        # Daily P&L tracking
        self.daily_pnl = 0.0
        self.daily_trades = 0
    
    def evaluate(
        self, 
        trade,  # Trade object
        current_price: float, 
        is_trend_reversed: bool = False
    ) -> RiskDecision:
        """
        Evaluate all exit criteria for a trade.
        
        Args:
            trade: Current Trade object
            current_price: Latest price
            is_trend_reversed: Whether trend has reversed (from indicator)
            
        Returns:
            RiskDecision with exit recommendation
        """
        # Update price first
        trade = trade.update_price(current_price)
        
        # === 1. TREND REVERSAL CHECK ===
        if self.exit_on_reversal and is_trend_reversed:
            return RiskDecision(
                should_exit=True,
                reason=ExitReason.TREND_REVERSAL,
                message=f"Trend reversed at {current_price:.2f}"
            )
        
        # === 2. CALCULATE TRAILING STOP ===
        tsl_level, stage = self.tsl_manager.calculate_tsl(
            trade.entry_price,
            current_price,
            trade.highest_price,
            trade.atr,
            trade.last_stage
        )
        
        # Check if TSL hit
        if current_price <= tsl_level:
            return RiskDecision(
                should_exit=True,
                reason=ExitReason.TSL_HIT,
                message=f"TSL Hit: Price {current_price:.2f} <= TSL {tsl_level:.2f} (Stage: {stage})",
                new_tsl_level=tsl_level,
                new_stage=stage
            )
        
        # === 3. TIME-BASED EXIT ===
        if self.enable_time_exit and trade.entry_time:
            hold_duration = datetime.now() - trade.entry_time
            if hold_duration > timedelta(minutes=self.max_hold_minutes):
                return RiskDecision(
                    should_exit=True,
                    reason=ExitReason.TIME_BASED,
                    message=f"Max hold time ({self.max_hold_minutes}min) exceeded"
                )
        
        # === 4. DAILY LIMIT CHECK ===
        projected_pnl = self.daily_pnl + trade.pnl
        
        if projected_pnl <= self.daily_loss_limit:
            return RiskDecision(
                should_exit=True,
                reason=ExitReason.DAILY_LIMIT,
                message=f"Daily loss limit reached: {projected_pnl:.2f}"
            )
        
        if projected_pnl >= self.daily_profit_target:
            return RiskDecision(
                should_exit=True,
                reason=ExitReason.DAILY_LIMIT,
                message=f"Daily profit target reached: {projected_pnl:.2f}"
            )
        
        # === NO EXIT NEEDED ===
        # Just update TSL level
        return RiskDecision(
            should_exit=False,
            new_tsl_level=tsl_level,
            new_stage=stage,
            message=f"TSL Updated: {tsl_level:.2f} ({stage}), P&L: {trade.pnl_pct:.2f}%"
        )
    
    def update_daily_pnl(self, pnl: float):
        """Update daily P&L tracker"""
        self.daily_pnl += pnl
        self.daily_trades += 1
    
    def reset_daily_stats(self):
        """Reset daily stats (call at start of new trading day)"""
        self.daily_pnl = 0.0
        self.daily_trades = 0
    
    def update_config(self, new_config: dict):
        """Update configuration dynamically"""
        self.config = new_config
        
        # Re-initialize TSL manager with new config
        tsl_config = new_config.get("tsl", {})
        self.tsl_manager = TrailingStopManager(tsl_config)
        
        # Update other params
        self.enable_time_exit = new_config.get("enable_time_based_exit", False)
        self.max_hold_minutes = new_config.get("max_hold_minutes", 60)
        self.daily_loss_limit = new_config.get("daily_loss_limit", -5000)
        self.daily_profit_target = new_config.get("daily_profit_target", 10000)
        self.exit_on_reversal = new_config.get("exit_on_reversal", True)

    def get_daily_stats(self) -> dict:
        """Get current daily statistics"""
        return {
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "limit_reached": (
                self.daily_pnl <= self.daily_loss_limit or 
                self.daily_pnl >= self.daily_profit_target
            )
        }
