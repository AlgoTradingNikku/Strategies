import logging
from typing import Dict, Any
from .models import Position
import trade_db
import trading_adapter
import risk_manager

log = logging.getLogger("UTBotSRChannelsScanner")

def execute_exit(pos: Position, cfg: dict, reason: str) -> bool:
    action = "SELL" if pos.action == "BUY" else "BUY"
    req = {
        "symbol": pos.symbol,
        "exchange": pos.exchange,
        "action": action,
        "quantity": pos.quantity,
        "product": pos.product,
        "price_type": "MARKET",
        "strategy": "UTBot_Options_Exit",
    }
    log.info("Executing auto exit for position %s (reason: %s)", pos.symbol, reason)
    res = trading_adapter.place_order(cfg, req)
    exit_price = pos.current_price
    trade_db.close_trade(pos.trade_id, exit_price=exit_price, exit_reason=reason)
    # [Sprint-1] Record exit time for cool-down / duplicate-entry guard
    try:
        risk_manager.record_exit(pos.symbol)
    except Exception:
        pass
    return True

def execute_update_sl(pos: Position, new_sl: float):
    log.info("Updating trailing SL for %s to %.2f", pos.symbol, new_sl)
    trade_db.update_trade_price(pos.trade_id, current_price=pos.current_price, trailing_sl=new_sl)
