from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class Position:
    trade_id: int
    order_id: str
    symbol: str
    exchange: str
    action: str
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: float
    target: float
    trailing_sl: Optional[float] = None
    status: str = "OPEN"
    product: str = "NRML"
    pnl_pts: float = 0.0
    pnl_amount: float = 0.0
    opened_at: str = field(default_factory=lambda: datetime.now().isoformat())
    closed_at: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None

    @property
    def return_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.action == "BUY":
            return ((self.current_price - self.entry_price) / self.entry_price) * 100.0
        return ((self.entry_price - self.current_price) / self.entry_price) * 100.0
