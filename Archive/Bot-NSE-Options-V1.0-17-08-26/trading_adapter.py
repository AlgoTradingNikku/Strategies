"""
trading_adapter.py
===================
Broker-agnostic order placement & LTP fetching for Bot-NSE-Options.
Routes orders through OpenAlgo REST API or Python SDK.
"""

import logging
import requests
from typing import Dict, Any, Optional

log = logging.getLogger("UTBotSRChannelsScanner")

_oa_client_cache: Dict[tuple, Any] = {}


def _get_oa_client(oa_cfg: dict):
    key = (oa_cfg.get("apikey", ""), oa_cfg.get("base_url", "http://127.0.0.1:5000"))
    if key not in _oa_client_cache:
        from openalgo import api as oa_api
        _oa_client_cache[key] = oa_api(api_key=key[0], host=key[1])
    return _oa_client_cache[key]


def place_order(cfg: dict, order_req: dict) -> dict:
    """
    Place order via OpenAlgo API.

    order_req keys:
      - symbol: str (e.g. "NIFTY18AUG2624600CE")
      - exchange: str (e.g. "NFO")
      - action: str ("BUY" or "SELL")
      - quantity: int
      - product: str ("NRML" or "MIS")
      - price_type: str ("MARKET" or "LIMIT")
      - price: float (optional)
      - trigger_price: float (optional)
      - strategy: str
    """
    oa_cfg = cfg.get("openalgo", {})
    client = _get_oa_client(oa_cfg)

    symbol = order_req.get("symbol")
    exchange = order_req.get("exchange", "NFO")
    action = order_req.get("action", "BUY").upper()
    quantity = int(order_req.get("quantity", 65))
    product = order_req.get("product", "NRML").upper()
    price_type = order_req.get("price_type", "MARKET").upper()
    price = float(order_req.get("price", 0.0))
    trigger_price = float(order_req.get("trigger_price", 0.0))
    strategy = order_req.get("strategy", cfg.get("trading", {}).get("strategy_name", "UTBot_Options"))

    try:
        res = client.placeorder(
            strategy=strategy,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type=price_type,
            product=product,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
        )
        log.info("Placed %s order for %s: %s", action, symbol, res)
        if isinstance(res, dict):
            return res
        return {"status": "success", "order_id": str(res), "response": res}
    except Exception as exc:
        log.error("Failed to place order for %s: %s", symbol, exc)
        return {"status": "error", "message": str(exc)}


def get_ltp(cfg: dict, symbol: str, exchange: str = "NFO") -> float:
    """Fetch live Last Traded Price (LTP) for an option or index symbol."""
    oa_cfg = cfg.get("openalgo", {})
    client = _get_oa_client(oa_cfg)

    try:
        resp = client.getltp(symbol=symbol, exchange=exchange)
        if isinstance(resp, dict) and resp.get("status") == "success":
            return float(resp.get("data", {}).get("ltp", 0.0) or resp.get("ltp", 0.0))
        if isinstance(resp, (int, float)):
            return float(resp)
    except Exception as exc:
        log.debug("Failed to fetch LTP for %s: %s", symbol, exc)

    return 0.0
