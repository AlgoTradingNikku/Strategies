"""
===============================================================================
  Bot-Options / execution / order_engine.py
  Option contract order routing — placing offset-based optionsorder() and
  direct symbol placeorder() with NFO routing, retry backoffs, and duplicate check.
===============================================================================
"""

import time
import logging
from typing import dict, Any, Optional

log = logging.getLogger(__name__)

# Cache to prevent duplicate order placement in short windows (e.g. 30 seconds)
# format: {(underlying, expiry, strike, option_type, direction): timestamp}
_recent_orders: dict[tuple, float] = {}
_DEDUPLICATE_WINDOW_SEC = 30.0

def _is_duplicate_order(key: tuple) -> bool:
    """Check if order was placed recently within safety window."""
    now = time.time()
    if key in _recent_orders:
        elapsed = now - _recent_orders[key]
        if elapsed < _DEDUPLICATE_WINDOW_SEC:
            return True
    return False


def _record_order(key: tuple):
    """Record order timestamp for deduplication."""
    _recent_orders[key] = time.time()


def place_offset_options_order(
    config: dict,
    underlying: str,
    option_type: str,     # 'CE' or 'PE'
    action: str,          # 'BUY' or 'SELL'
    quantity: int,
    offset: str,          # 'ATM', 'ITM1', 'OTM1' etc.
    expiry_date: str,     # format '28OCT25'
    oa_client
) -> dict[str, Any]:
    """
    Place options order using OpenAlgo's native optionsorder endpoint (offset-based).
    """
    exec_cfg = config.get("execution", {})
    strategy_tag = exec_cfg.get("strategy_tag", "OptionsBot")
    price_type = exec_cfg.get("order_type", "MARKET")
    product = exec_cfg.get("order_product", "MIS")
    
    order_key = (underlying, expiry_date, offset, option_type, action)
    
    if _is_duplicate_order(order_key):
        msg = f"Duplicate options order blocked: {underlying} {offset} {option_type} {action}"
        log.warning(msg)
        return {"status": "error", "message": msg}

    max_retries = 3
    delay = 2.0
    
    for attempt in range(1, max_retries + 1):
        try:
            log.info("[%s] Option offset order attempt %d/%d: %s %s %s Qty:%d",
                     underlying, attempt, max_retries, offset, option_type, action, quantity)
            
            resp = oa_client.optionsorder(
                strategy=strategy_tag,
                underlying=underlying,
                exchange="NSE_INDEX",
                expiry_date=expiry_date,
                offset=offset,
                option_type=option_type,
                action=action,
                quantity=quantity,
                pricetype=price_type,
                product=product,
                splitsize=0
            )
            
            if isinstance(resp, dict) and resp.get("status") == "success":
                _record_order(order_key)
                log.info("[%s] Order placed successfully: OrderID=%s | Symbol=%s", 
                         underlying, resp.get("orderid"), resp.get("symbol"))
                return {"status": "success", "orderid": resp.get("orderid"), "symbol": resp.get("symbol"), "raw": resp}
            else:
                msg = resp.get("message") if isinstance(resp, dict) else str(resp)
                log.warning("[%s] Order attempt %d failed: %s", underlying, attempt, msg)
                
        except Exception as e:
            log.error("[%s] Connection error during order placement: %s", underlying, e)
            
        time.sleep(delay)
        delay *= 2.0  # Exponential backoff

    return {"status": "error", "message": "Failed to place offset option order after max retries."}


def place_direct_options_order(
    config: dict,
    symbol: str,          # Full symbol e.g. NIFTY28OCT2525950CE
    action: str,          # 'BUY' or 'SELL'
    quantity: int,
    price: float = 0.0,
    oa_client = None
) -> dict[str, Any]:
    """
    Place direct options order using OpenAlgo's standard placeorder endpoint (symbol-based).
    Used primarily for squaring off / exiting positions.
    """
    exec_cfg = config.get("execution", {})
    strategy_tag = exec_cfg.get("strategy_tag", "OptionsBot")
    product = exec_cfg.get("order_product", "MIS")
    
    # If price is specified > 0 and price_type in config or default, use LIMIT, else MARKET
    price_type = "LIMIT" if price > 0.0 else "MARKET"
    
    max_retries = 3
    delay = 2.0
    
    for attempt in range(1, max_retries + 1):
        try:
            log.info("Direct NFO order attempt %d/%d: %s %s Qty:%d Price:%.2f",
                     attempt, max_retries, symbol, action, quantity, price)
            
            params = {
                "strategy": strategy_tag,
                "symbol": symbol,
                "action": action,
                "exchange": "NFO",
                "price_type": price_type,
                "product": product,
                "quantity": quantity
            }
            if price > 0.0:
                params["price"] = price
                params["trigger_price"] = 0.0
                params["disclosed_quantity"] = 0
                
            resp = oa_client.placeorder(**params)
            
            if isinstance(resp, dict) and resp.get("status") == "success":
                log.info("Direct order placed successfully: OrderID=%s", resp.get("orderid"))
                return {"status": "success", "orderid": resp.get("orderid"), "symbol": symbol, "raw": resp}
            else:
                msg = resp.get("message") if isinstance(resp, dict) else str(resp)
                log.warning("Direct order attempt %d failed: %s", attempt, msg)
                
        except Exception as e:
            log.error("Connection error during direct order placement: %s", e)
            
        time.sleep(delay)
        delay *= 2.0

    return {"status": "error", "message": f"Failed to place direct option order for {symbol} after max retries."}
