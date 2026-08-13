"""
trading_adapter.py
====================
Slim, OpenAlgo-only order placement + LTP adapter for BOT-Options.

Bot-Stocks has a multi-broker version of this module (openalgo, flattrade,
mstock, shoonya, dhan) because it lets the user pick a `trading_api_source`.
BOT-Options only ever trades through OpenAlgo — per ARCHITECTURE.md, that's
the only data source that supports live LTP streaming and order placement —
so this module is intentionally a single-broker subset with the exact same
`place_order(cfg, req)` / `get_ltp(cfg, symbol, exchange)` interface that
trade_management/monitor.py and executor.py expect. Keeping the interface
identical is what let trade_management port over with no changes.

req is duck-typed (see trade_management/models.py:ExitOrderRequest) and only
needs: symbol, exchange, action, quantity, product, price_type, price,
trigger_price, strategy.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("UTBot.TradeManagement")

_client_lock = threading.Lock()
_client_cache: dict[tuple[str, str], object] = {}


def _get_client(cfg: dict):
    """Return a cached OpenAlgo client for this (apikey, base_url), creating one if needed."""
    from openalgo import api as oa_api

    oa_cfg = cfg.get("openalgo", {})
    api_key = oa_cfg.get("apikey", "")
    base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
    key = (api_key, base_url)

    with _client_lock:
        client = _client_cache.get(key)
        if client is None:
            client = oa_api(api_key=api_key, host=base_url)
            _client_cache[key] = client
        return client


def place_order(cfg: dict, req) -> dict:
    """
    Place an order via OpenAlgo.

    Returns
    -------
    dict with keys:
        status   : "success" | "error"
        orderid  : str (on success)
        message  : str (on error)
        raw      : dict — raw OpenAlgo response (on success)
    """
    try:
        client = _get_client(cfg)
        response = client.placeorder(
            strategy=getattr(req, "strategy", "BOT-Options-TM"),
            symbol=req.symbol,
            action=req.action,
            exchange=req.exchange,
            price_type=req.price_type,
            product=req.product,
            quantity=req.quantity,
            price=req.price,
            trigger_price=getattr(req, "trigger_price", 0.0),
        )
    except Exception as exc:
        log.error("[trading_adapter] Order placement error for %s: %s", req.symbol, exc)
        return {"status": "error", "message": str(exc)}

    if isinstance(response, dict) and response.get("status") == "error":
        msg = response.get("message", str(response))
        log.error("[trading_adapter] Order rejected for %s: %s", req.symbol, msg)
        return {"status": "error", "message": msg}

    orderid = ""
    if isinstance(response, dict):
        orderid = response.get("orderid") or response.get("order_id", "")
    log.info(
        "[trading_adapter] Order placed: %s %s x%d (%s) -> orderid=%s",
        req.action, req.symbol, req.quantity, req.product, orderid,
    )
    return {"status": "success", "orderid": orderid, "raw": response}


def get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    """
    Fetch live LTP via the OpenAlgo quotes endpoint.

    Used by trade_management as the HTTP-polling fallback when its own
    WebSocket connection is briefly down — the fast path is WS ticks, this
    is only hit intermittently, so a plain REST call is fine here.

    Raises RuntimeError if the LTP can't be resolved.
    """
    client = _get_client(cfg)
    try:
        resp = client.quotes(symbol=symbol, exchange=exchange)
    except Exception as exc:
        raise RuntimeError(f"OpenAlgo quotes() call failed for {symbol}: {exc}") from exc

    if isinstance(resp, dict) and resp.get("status") == "error":
        raise RuntimeError(resp.get("message", str(resp)))

    ltp = None
    if isinstance(resp, dict):
        ltp = (resp.get("data") or {}).get("ltp") if isinstance(resp.get("data"), dict) else None
        if ltp is None:
            ltp = resp.get("ltp")
    if ltp is None:
        raise RuntimeError(f"LTP not found in OpenAlgo response for {symbol}: {resp}")
    return float(ltp)
