"""
trading_adapter.py
===================
Broker-agnostic order placement & LTP fetching for Bot-NSE-Options.
Routes orders through OpenAlgo REST API or Python SDK.
"""

import logging
import requests
from typing import Dict, Any, Optional

from broker_retry import with_retry  # [Sprint-5] exponential backoff wrapper

# [Sprint-6] Metrics — fail-open import.
try:
    import metrics as _metrics
except Exception:  # pragma: no cover
    _metrics = None

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

    def _do_place():
        return client.placeorder(
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

    try:
        # [Sprint-5] Wrap in retry for transient network errors
        res = with_retry(_do_place, cfg=cfg, op_name=f"place_order[{symbol}]")
        log.info("Placed %s order for %s: %s", action, symbol, res)
        # [Sprint-6] Record success.
        if _metrics is not None:
            try:
                _metrics.record_order(action, symbol, success=True)
            except Exception:
                pass
        if isinstance(res, dict):
            return res
        return {"status": "success", "order_id": str(res), "response": res}
    except Exception as exc:
        log.error("Failed to place order for %s: %s", symbol, exc)
        # [Sprint-6] Record failure.
        if _metrics is not None:
            try:
                _metrics.record_order(action, symbol, success=False)
            except Exception:
                pass
        return {"status": "error", "message": str(exc)}


def get_ltp(cfg: dict, symbol: str, exchange: str = "NFO") -> float:
    """Fetch live Last Traded Price (LTP) for an option or index symbol."""
    oa_cfg = cfg.get("openalgo", {})
    client = _get_oa_client(oa_cfg)

    def _do_ltp():
        return client.getltp(symbol=symbol, exchange=exchange)

    try:
        # [Sprint-5] Retry transient network errors only; parse errors are not retried.
        resp = with_retry(_do_ltp, cfg=cfg, op_name=f"get_ltp[{symbol}]")
        if isinstance(resp, dict) and resp.get("status") == "success":
            return float(resp.get("data", {}).get("ltp", 0.0) or resp.get("ltp", 0.0))
        if isinstance(resp, (int, float)):
            return float(resp)
    except Exception as exc:
        log.debug("Failed to fetch LTP for %s: %s", symbol, exc)

    return 0.0


def get_quote(cfg: dict, symbol: str, exchange: str = "NFO") -> dict | None:
    """
    [Sprint-2] Fetch bid, ask, open-interest for a contract via OpenAlgo /quotes.
    Returns dict with keys: bid, ask, ltp, volume, oi (or None on any failure).

    Consumed by signal_quality.check_spread_liquidity — spread + OI filter.
    All errors are logged at DEBUG and return None (fail-open policy).
    """
    oa_cfg = cfg.get("openalgo", {})
    try:
        client = _get_oa_client(oa_cfg)

        def _do_quote():
            # OpenAlgo Python SDK exposes .quotes() returning a dict payload
            if hasattr(client, "quotes"):
                return client.quotes(symbol=symbol, exchange=exchange)
            if hasattr(client, "quote"):
                return client.quote(symbol=symbol, exchange=exchange)
            return None

        # [Sprint-5] Retry transient errors on quote fetch too.
        resp = with_retry(_do_quote, cfg=cfg, op_name=f"get_quote[{symbol}]")
        if not isinstance(resp, dict):
            return None
        data = resp.get("data", resp) if resp.get("status", "success") == "success" else None
        if not data:
            return None
        return {
            "bid": float(data.get("bid") or data.get("best_bid") or 0.0),
            "ask": float(data.get("ask") or data.get("best_ask") or 0.0),
            "ltp": float(data.get("ltp") or 0.0),
            "volume": int(data.get("volume") or 0),
            "oi": int(data.get("oi") or data.get("open_interest") or 0),
            # [Sprint-4] Optional greeks — fail-open (0.0) if broker doesn't return them
            "delta": float(data.get("delta") or 0.0),
            "theta": float(data.get("theta") or 0.0),
            "gamma": float(data.get("gamma") or 0.0),
            "vega": float(data.get("vega") or 0.0),
            "iv": float(data.get("iv") or data.get("implied_volatility") or 0.0),
        }
    except Exception as exc:
        log.debug("Failed to fetch quote for %s: %s", symbol, exc)
        return None

