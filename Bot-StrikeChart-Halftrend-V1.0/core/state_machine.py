"""
Trade State Machine - Manages trade lifecycle states and transitions.

This module defines the states a trade can be in and enforces valid transitions.
Ensures data integrity and prevents invalid operations (e.g., can't sell what you don't own).
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


class TradeState(Enum):
    """
    Valid states in the trade lifecycle.
    
    Flow:
        IDLE -> OBSERVING -> ENTERING -> POSITION -> EXITING -> EXITED
                ↓                         ↓
            BLOCKED                   BLOCKED
    """
    IDLE = auto()        # No trade, ready for new signal
    OBSERVING = auto()   # Signal detected, waiting for option confirmation
    ENTERING = auto()    # Order placed, awaiting fill
    POSITION = auto()    # Active position held
    EXITING = auto()     # Exit order placed
    EXITED = auto()      # Position closed
    BLOCKED = auto()     # Cooldown period active (re-entry protection)


@dataclass
class Trade:
    """
    Immutable trade state object.
    
    Represents all information about a single trade from signal detection to exit.
    Designed to be persisted to SQLite for crash recovery.
    """
    symbol: str
    state: TradeState = TradeState.IDLE
    side: Optional[str] = None  # "CALL" or "PUT"
    
    # Pricing
    entry_price: float = 0.0
    current_price: float = 0.0
    highest_price: float = 0.0  # For trailing stop
    lowest_price: float = 0.0   # For short trailing stop
    
    # Position tracking
    quantity: int = 0
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: Optional[str] = None
    
    # P&L
    pnl: float = 0.0
    pnl_pct: float = 0.0
    
    # Risk management
    atr: float = 0.0
    tsl_level: float = 0.0  # Current trailing stop level
    last_stage: str = "INIT"  # BE, TRAILING, etc.
    cushion_attempts: int = 0  # Number of TSL cushions applied
    
    # Observation tracking (for OBSERVING state)
    obs_candles: int = 0
    obs_start_time: Optional[datetime] = None
    idx_at_resolution: float = 0.0  # Index price when strike was selected
    
    # Expiry params (for drift guard re-resolution)
    expiry_params: Dict[str, Any] = field(default_factory=dict)
    
    # Metadata (flexible storage for strategy-specific data)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # State management
    trend_reversed: bool = False
    manual_exit_pending: bool = False
    
    def calculate_pnl(self) -> tuple[float, float]:
        """
        Calculate current P&L.
        
        Returns:
            (pnl, pnl_pct) tuple
        """
        if self.entry_price <= 0 or self.quantity == 0:
            return (0.0, 0.0)
        
        pnl = (self.current_price - self.entry_price) * self.quantity
        pnl_pct = ((self.current_price - self.entry_price) / self.entry_price) * 100
        
        return (pnl, pnl_pct)
    
    def is_profitable(self) -> bool:
        """Check if trade is currently in profit"""
        _, pnl_pct = self.calculate_pnl()
        return pnl_pct > 0
    
    def update_price(self, new_price: float) -> "Trade":
        """
        Create new Trade with updated price and recalculated P&L.
        
        Args:
            new_price: Latest price
            
        Returns:
            New Trade instance with updated values
        """
        # Update highest/lowest for trailing stop
        new_highest = max(self.highest_price, new_price)
        new_lowest = min(self.lowest_price, new_price) if self.lowest_price > 0 else new_price
        
        # Calculate P&L using NEW price (not old current_price)
        if self.entry_price > 0 and self.quantity > 0:
            pnl = (new_price - self.entry_price) * self.quantity
            pnl_pct = ((new_price - self.entry_price) / self.entry_price) * 100
        else:
            pnl, pnl_pct = 0.0, 0.0
        
        # Create new instance (immutable pattern)
        return Trade(
            **{
                **self.__dict__,
                "current_price": new_price,
                "highest_price": new_highest,
                "lowest_price": new_lowest,
                "pnl": pnl,
                "pnl_pct": pnl_pct
            }
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence"""
        return {
            "symbol": self.symbol,
            "state": self.state.name,
            "side": self.side,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "atr": self.atr,
            "tsl_level": self.tsl_level,
            "last_stage": self.last_stage,
            "cushion_attempts": self.cushion_attempts,
            "obs_candles": self.obs_candles,
            "obs_start_time": self.obs_start_time.isoformat() if self.obs_start_time else None,
            "idx_at_resolution": self.idx_at_resolution,
            "expiry_params": self.expiry_params,
            "metadata": self.metadata,
            "trend_reversed": self.trend_reversed,
            "manual_exit_pending": self.manual_exit_pending,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        """Create Trade from dictionary (for loading from database)"""
        # Parse datetimes
        if data.get("entry_time"):
            data["entry_time"] = datetime.fromisoformat(data["entry_time"])
        if data.get("exit_time"):
            data["exit_time"] = datetime.fromisoformat(data["exit_time"])
        if data.get("obs_start_time"):
            data["obs_start_time"] = datetime.fromisoformat(data["obs_start_time"])
        
        # Parse state enum
        if isinstance(data.get("state"), str):
            data["state"] = TradeState[data["state"]]
        
        return cls(**data)


class TradeStateMachine:
    """
    Manages valid state transitions for trades.
    
    Enforces business rules and prevents invalid operations.
    """
    
    # Valid state transitions
    VALID_TRANSITIONS = {
        TradeState.IDLE: [TradeState.OBSERVING, TradeState.BLOCKED],
        TradeState.OBSERVING: [TradeState.ENTERING, TradeState.IDLE, TradeState.BLOCKED],
        TradeState.ENTERING: [TradeState.POSITION, TradeState.IDLE],
        TradeState.POSITION: [TradeState.EXITING, TradeState.BLOCKED, TradeState.EXITED], # Added EXITED for external closures
        TradeState.EXITING: [TradeState.EXITED, TradeState.POSITION],  # Can retry
        TradeState.EXITED: [TradeState.IDLE, TradeState.BLOCKED],
        TradeState.BLOCKED: [TradeState.IDLE],
    }
    
    @classmethod
    def can_transition(cls, from_state: TradeState, to_state: TradeState) -> bool:
        """
        Check if transition is valid.
        
        Args:
            from_state: Current state
            to_state: Desired state
            
        Returns:
            True if transition is allowed
        """
        return to_state in cls.VALID_TRANSITIONS.get(from_state, [])
    
    @classmethod
    def transition(cls, trade: Trade, new_state: TradeState, reason: str = "") -> Trade:
        """
        Transition trade to new state.
        
        Args:
            trade: Current trade
            new_state: Desired state
            reason: Reason for transition (for logging)
            
        Returns:
            New Trade instance in new state
            
        Raises:
            ValueError: If transition is invalid
        """
        if not cls.can_transition(trade.state, new_state):
            raise ValueError(
                f"Invalid transition: {trade.state.name} -> {new_state.name}. "
                f"Valid transitions from {trade.state.name}: "
                f"{[s.name for s in cls.VALID_TRANSITIONS.get(trade.state, [])]}"
            )
        
        # Create new instance with updated state
        updates = {"state": new_state}
        
        # Auto-update timestamps based on state
        if new_state == TradeState.OBSERVING and trade.state == TradeState.IDLE:
            updates["obs_start_time"] = datetime.now()
        elif new_state == TradeState.POSITION and trade.state == TradeState.ENTERING:
            updates["entry_time"] = datetime.now()
        elif new_state == TradeState.EXITED:
            updates["exit_time"] = datetime.now()
            if reason:
                updates["exit_reason"] = reason
        
        return Trade(**{**trade.__dict__, **updates})
    
    @classmethod
    def get_valid_transitions(cls, state: TradeState) -> list[TradeState]:
        """Get list of valid next states"""
        return cls.VALID_TRANSITIONS.get(state, [])

