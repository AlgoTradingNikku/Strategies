"""
===============================================================================
  Bot-Options / core / option_risk.py
  Risk Management Engine — validates trade sizes, capital allocation limits,
  and circuit breakers (max daily drawdown, consecutive loss cooldowns, and
  max open positions count).
===============================================================================
"""

import logging
from datetime import datetime, timedelta
from typing import Tuple

log = logging.getLogger(__name__)

# State trackers for current day cooldowns
_cooldown_until: datetime = None

def set_risk_cooldown(minutes: int):
    """Trigger a manual/automatic cooldown phase."""
    global _cooldown_until
    _cooldown_until = datetime.now() + timedelta(minutes=minutes)
    log.info("Risk cooldown active until: %s", _cooldown_until.strftime("%H:%M:%S"))


def check_risk_circuit_breakers(
    config: dict,
    active_positions_count: int,
    trades_today: int,
    daily_pnl: float,
    consecutive_losses: int
) -> Tuple[bool, str]:
    """
    Evaluate platform limits and daily stats to determine if trading must be paused.

    Returns
    -------
    (is_ok, reason)
    """
    global _cooldown_until
    risk_cfg = config.get("risk_management", {})
    if not risk_cfg.get("enabled", True):
        return True, ""

    now = datetime.now()
    
    # 1. Cooldown Period Check
    if _cooldown_until and now < _cooldown_until:
        remaining = int((_cooldown_until - now).total_seconds() / 60)
        return False, f"Risk cooldown active. Paused for another {remaining} min."

    # 2. Maximum Simultaneous Positions
    max_positions = int(risk_cfg.get("max_simultaneous_positions", 5))
    if active_positions_count >= max_positions:
        return False, f"Max simultaneous positions limit reached: {active_positions_count}/{max_positions}"

    # 3. Maximum Trades Per Day
    max_trades = int(risk_cfg.get("max_trades_per_day", 10))
    if trades_today >= max_trades:
        return False, f"Daily trade limit reached: {trades_today}/{max_trades}"

    # 4. Maximum Daily Loss Amount (₹)
    max_loss = float(risk_cfg.get("max_daily_loss_amount", 5000))
    if daily_pnl <= -max_loss:
        return False, f"Daily drawdown limit hit: PnL is ₹{daily_pnl:.2f} (Limit: -₹{max_loss:.2f})"

    # 5. Consecutive Losses Limit
    loss_limit = int(risk_cfg.get("consecutive_loss_limit", 3))
    if consecutive_losses >= loss_limit:
        cooldown_min = int(risk_cfg.get("cooldown_minutes", 30))
        set_risk_cooldown(cooldown_min)
        return False, f"Consecutive loss limit hit: {consecutive_losses} losses. Pausing trading for {cooldown_min} min."

    return True, ""


def validate_capital_allocation(
    config: dict,
    estimated_trade_cost: float,
    current_deployed_capital: float
) -> Tuple[bool, str]:
    """
    Ensure the estimated premium outlay does not exceed capital caps.
    """
    risk_cfg = config.get("risk_management", {})
    if not risk_cfg.get("enabled", True):
        return True, ""

    # Total capital allocation
    total_capital = float(risk_cfg.get("capital_allocation", 100000))
    
    # Maximum capital per trade (Premium outlay limit)
    max_trade_cap = float(risk_cfg.get("max_capital_per_trade", 50000))
    if estimated_trade_cost > max_trade_cap:
        return False, f"Estimated cost ₹{estimated_trade_cost:.2f} exceeds Max Capital Per Trade ₹{max_trade_cap:.2f}"

    # Verify we aren't exceeding total allocation
    if current_deployed_capital + estimated_trade_cost > total_capital:
        return False, f"Trade cost ₹{estimated_trade_cost:.2f} exceeds remaining allocated capital (Used: ₹{current_deployed_capital:.2f}/{total_capital:.2f})"

    return True, ""
