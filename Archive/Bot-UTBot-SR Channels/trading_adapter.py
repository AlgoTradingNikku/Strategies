"""
===============================================================================
  Trading Adapter — Broker-agnostic order placement and LTP fetching
===============================================================================

Dispatches place_order() and get_ltp() to the configured trading_api_source.

Supported sources
-----------------
  openalgo  — Local OpenAlgo server (default, broker-agnostic middleware)
  flattrade — Flattrade REST API  (direct broker)
  mstock    — MStock (Mirae Asset) REST API  (direct broker, same API family as Flattrade/Shoonya)
  shoonya   — Finvasia / Shoonya REST API  (direct broker)
  dhan      — Dhan HQ REST API  (direct broker)

Each adapter implements two operations:
  place_order(cfg, req) -> dict   — place a single order, return broker response
  get_ltp(cfg, symbol, exchange)  -> float   — fetch live last traded price

All adapters use plain requests (no broker SDK required — optional SDKs can be
added later without changing this interface).

For session-token-based brokers (Flattrade, MStock, Shoonya) the session_token
must be obtained via your broker's login flow and placed in config.yml before
the market opens. Dhan uses a long-lived access_token from the Dhan developer
portal.
===============================================================================
"""

import hashlib
import logging
import requests

log = logging.getLogger("UTBotSRChannelsScanner")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _post(url: str, payload: dict, headers: dict = None, timeout: int = 10) -> dict:
    """POST JSON and return parsed response dict. Never raises — returns error dict."""
    try:
        r = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        return {"status": "error", "message": f"HTTP {r.status_code}: {r.text}"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": f"Connection refused: {url}"}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": f"Request timed out: {url}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _get(url: str, params: dict = None, headers: dict = None, timeout: int = 10) -> dict:
    """GET and return parsed response dict. Never raises — returns error dict."""
    try:
        r = requests.get(url, params=params or {}, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        return {"status": "error", "message": f"HTTP {r.status_code}: {r.text}"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": f"Connection refused: {url}"}
    except requests.exceptions.Timeout:
        return {"status": "error", "message": f"Request timed out: {url}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# 1. OpenAlgo adapter
# ---------------------------------------------------------------------------

def _openalgo_place_order(cfg: dict, req) -> dict:
    """Place order via OpenAlgo Python SDK (existing behaviour)."""
    from openalgo import api as oa_api
    oa_cfg = cfg.get("openalgo", {})
    # Re-use the app-level cache if available; fall back to a fresh client
    try:
        from app import _get_oa_client
        client = _get_oa_client(oa_cfg)
    except ImportError:
        client = oa_api(
            api_key=oa_cfg.get("apikey", ""),
            host=oa_cfg.get("base_url", "http://127.0.0.1:5000"),
        )
    response = client.placeorder(
        strategy=req.strategy,
        symbol=req.symbol,
        action=req.action,
        exchange=req.exchange,
        price_type=req.price_type,
        product=req.product,
        quantity=req.quantity,
        price=req.price,
        trigger_price=req.trigger_price,
    )
    if isinstance(response, dict) and response.get("status") == "error":
        return {"status": "error", "message": response.get("message", str(response))}
    return {"status": "success", "orderid": response.get("orderid") or response.get("order_id", ""), "raw": response}


def _openalgo_get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    """Fetch LTP via OpenAlgo quotes endpoint."""
    from openalgo import api as oa_api
    oa_cfg = cfg.get("openalgo", {})
    try:
        from app import _get_oa_client
        client = _get_oa_client(oa_cfg)
    except ImportError:
        client = oa_api(
            api_key=oa_cfg.get("apikey", ""),
            host=oa_cfg.get("base_url", "http://127.0.0.1:5000"),
        )
    resp = client.quotes(symbol=symbol, exchange=exchange)
    if isinstance(resp, dict) and resp.get("status") == "error":
        raise RuntimeError(resp.get("message", str(resp)))
    ltp = resp.get("data", {}).get("ltp") or resp.get("ltp")
    if ltp is None:
        raise RuntimeError(f"LTP not found in OpenAlgo response: {resp}")
    return float(ltp)


# ---------------------------------------------------------------------------
# 2. Flattrade adapter
# ---------------------------------------------------------------------------
# Flattrade uses the NorenApi REST interface.
# Docs: https://flattrade.in/developer
# Auth: session_token obtained via /norentp/QuickAuth after SHA-256(pwd+totp)
# ---------------------------------------------------------------------------

_FLATTRADE_BASE = "https://piconnect.flattrade.in/PiConnectTP"


def _ft_headers(api_key: str, session_token: str) -> dict:
    return {"Content-Type": "application/json"}


def _ft_jkey(api_key: str, session_token: str) -> str:
    """Flattrade jKey = session_token (returned by QuickAuth)."""
    return session_token


def _flattrade_place_order(cfg: dict, req) -> dict:
    ft = cfg.get("flattrade", {})
    api_key       = ft.get("api_key", "")
    session_token = ft.get("session_token", "")
    client_id     = ft.get("client_id", "")

    if not session_token:
        return {"status": "error", "message": "Flattrade session_token missing in config.yml"}

    # Map generic fields → Flattrade/NorenApi fields
    prd_map   = {"MIS": "I", "CNC": "C", "NRML": "M"}
    trans_map = {"BUY": "B", "SELL": "S"}
    ptype_map = {"MARKET": "MKT", "LIMIT": "LMT", "SL": "SL-LMT", "SL-M": "SL-MKT"}

    payload = {
        "uid":    client_id,
        "actid":  client_id,
        "exch":   req.exchange,
        "tsym":   req.symbol,
        "qty":    str(req.quantity),
        "prc":    str(req.price) if req.price else "0",
        "trgprc": str(req.trigger_price) if req.trigger_price else "0",
        "prd":    prd_map.get(req.product.upper(), "I"),
        "trantype": trans_map.get(req.action.upper(), "B"),
        "prctyp": ptype_map.get(req.price_type.upper(), "MKT"),
        "ret":    "DAY",
        "jKey":   session_token,
    }
    resp = _post(f"{_FLATTRADE_BASE}/PlaceOrder", payload)
    if resp.get("stat") == "Ok":
        return {"status": "success", "orderid": resp.get("norenordno", ""), "raw": resp}
    return {"status": "error", "message": resp.get("emsg", str(resp))}


def _flattrade_get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    ft            = cfg.get("flattrade", {})
    session_token = ft.get("session_token", "")
    client_id     = ft.get("client_id", "")
    if not session_token:
        raise RuntimeError("Flattrade session_token missing")
    payload = {"uid": client_id, "exch": exchange, "token": symbol, "jKey": session_token}
    resp = _post(f"{_FLATTRADE_BASE}/GetQuotes", payload)
    if resp.get("stat") == "Ok":
        return float(resp.get("lp", 0))
    raise RuntimeError(resp.get("emsg", str(resp)))


# ---------------------------------------------------------------------------
# 3. MStock (Mirae Asset) adapter
# ---------------------------------------------------------------------------
# MStock exposes a REST API at https://apiconnect.miraeasset.com.
# Auth: Bearer token (access_token) obtained via MStock Developer Portal.
# Docs: https://developer.miraeassetcm.com
# ---------------------------------------------------------------------------

_MSTOCK_BASE = "https://apiconnect.miraeasset.com"


def _mstock_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _mstock_place_order(cfg: dict, req) -> dict:
    ms = cfg.get("mstock", {})
    access_token = ms.get("access_token", "")
    client_id    = ms.get("client_id", "")
    if not access_token:
        return {"status": "error", "message": "MStock access_token missing in config.yml"}

    payload = {
        "ClientCode":     client_id,
        "Exchange":       req.exchange,
        "Symbol":         req.symbol,
        "TransactionType": req.action.upper(),    # "BUY" / "SELL"
        "OrderType":      req.price_type.upper(), # "MARKET" / "LIMIT"
        "ProductType":    req.product.upper(),    # "MIS" / "CNC"
        "Quantity":       req.quantity,
        "Price":          req.price,
        "TriggerPrice":   req.trigger_price,
        "Validity":       "DAY",
    }
    resp = _post(
        f"{_MSTOCK_BASE}/api/v1/order/place",
        payload,
        headers=_mstock_headers(access_token),
    )
    if resp.get("status") == "success" or resp.get("Success"):
        order_id = (
            resp.get("data", {}).get("orderid")
            or resp.get("OrderID", "")
        )
        return {"status": "success", "orderid": order_id, "raw": resp}
    return {"status": "error", "message": resp.get("message") or resp.get("Message") or str(resp)}


def _mstock_get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    ms = cfg.get("mstock", {})
    access_token = ms.get("access_token", "")
    if not access_token:
        raise RuntimeError("MStock access_token missing")
    resp = _get(
        f"{_MSTOCK_BASE}/api/v1/quote",
        params={"symbol": symbol, "exchange": exchange},
        headers=_mstock_headers(access_token),
    )
    ltp = (
        resp.get("data", {}).get("ltp")
        or resp.get("ltp")
        or resp.get("LastPrice")
    )
    if ltp is None:
        raise RuntimeError(f"LTP not found in MStock response: {resp}")
    return float(ltp)


# ---------------------------------------------------------------------------
# 4. Shoonya / Finvasia adapter
# ---------------------------------------------------------------------------
# Shoonya shares the same NorenApi REST interface as Flattrade.
# Docs: https://shoonya.finvasia.com/
# ---------------------------------------------------------------------------

_SHOONYA_BASE = "https://api.shoonya.com/NorenWClientTP"


def _shoonya_place_order(cfg: dict, req) -> dict:
    sh = cfg.get("shoonya", {})
    session_token = sh.get("session_token", "")
    client_id     = sh.get("client_id", "")
    if not session_token:
        return {"status": "error", "message": "Shoonya session_token missing in config.yml"}

    prd_map   = {"MIS": "I", "CNC": "C", "NRML": "M"}
    trans_map = {"BUY": "B", "SELL": "S"}
    ptype_map = {"MARKET": "MKT", "LIMIT": "LMT", "SL": "SL-LMT", "SL-M": "SL-MKT"}

    payload = {
        "uid":      client_id,
        "actid":    client_id,
        "exch":     req.exchange,
        "tsym":     req.symbol,
        "qty":      str(req.quantity),
        "prc":      str(req.price) if req.price else "0",
        "trgprc":   str(req.trigger_price) if req.trigger_price else "0",
        "prd":      prd_map.get(req.product.upper(), "I"),
        "trantype": trans_map.get(req.action.upper(), "B"),
        "prctyp":   ptype_map.get(req.price_type.upper(), "MKT"),
        "ret":      "DAY",
        "jKey":     session_token,
    }
    resp = _post(f"{_SHOONYA_BASE}/PlaceOrder", payload)
    if resp.get("stat") == "Ok":
        return {"status": "success", "orderid": resp.get("norenordno", ""), "raw": resp}
    return {"status": "error", "message": resp.get("emsg", str(resp))}


def _shoonya_get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    sh            = cfg.get("shoonya", {})
    session_token = sh.get("session_token", "")
    client_id     = sh.get("client_id", "")
    if not session_token:
        raise RuntimeError("Shoonya session_token missing")
    payload = {"uid": client_id, "exch": exchange, "token": symbol, "jKey": session_token}
    resp = _post(f"{_SHOONYA_BASE}/GetQuotes", payload)
    if resp.get("stat") == "Ok":
        return float(resp.get("lp", 0))
    raise RuntimeError(resp.get("emsg", str(resp)))


# ---------------------------------------------------------------------------
# 5. Dhan adapter
# ---------------------------------------------------------------------------
# Dhan uses a REST API with Bearer auth.
# Docs: https://dhanhq.co/docs/v2/
# Note: Dhan uses numeric "securityId" internally; the adapter accepts the
#       trading symbol and resolves it via the /v2/instruments search endpoint.
# ---------------------------------------------------------------------------

_DHAN_BASE = "https://api.dhan.co/v2"


def _dhan_headers(access_token: str, client_id: str) -> dict:
    return {
        "access-token": access_token,
        "client-id":    client_id,
        "Content-Type": "application/json",
    }


def _dhan_place_order(cfg: dict, req) -> dict:
    dh = cfg.get("dhan", {})
    access_token = dh.get("access_token", "")
    client_id    = dh.get("client_id", "")
    if not access_token or not client_id:
        return {"status": "error", "message": "Dhan access_token / client_id missing in config.yml"}

    exch_seg_map = {"NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO", "MCX": "MCX_COMM"}
    order_type_map = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}
    product_map    = {"MIS": "INTRA", "CNC": "CNC", "NRML": "MARGIN"}

    payload = {
        "dhanClientId":    client_id,
        "transactionType": req.action.upper(),
        "exchangeSegment": exch_seg_map.get(req.exchange.upper(), "NSE_EQ"),
        "productType":     product_map.get(req.product.upper(), "INTRA"),
        "orderType":       order_type_map.get(req.price_type.upper(), "MARKET"),
        "validity":        "DAY",
        "tradingSymbol":   req.symbol,
        "securityId":      "",      # Dhan requires securityId; left blank for MARKET orders
        "quantity":        req.quantity,
        "price":           req.price,
        "triggerPrice":    req.trigger_price,
    }
    resp = _post(
        f"{_DHAN_BASE}/orders",
        payload,
        headers=_dhan_headers(access_token, client_id),
    )
    # Dhan returns HTTP 200 with orderId on success
    order_id = resp.get("orderId") or resp.get("data", {}).get("orderId", "")
    if order_id:
        return {"status": "success", "orderid": order_id, "raw": resp}
    err = resp.get("errorMessage") or resp.get("message") or str(resp)
    return {"status": "error", "message": err}


def _dhan_get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    dh = cfg.get("dhan", {})
    access_token = dh.get("access_token", "")
    client_id    = dh.get("client_id", "")
    if not access_token:
        raise RuntimeError("Dhan access_token missing")
    exch_seg_map = {"NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO"}
    payload = {
        "NSE_EQ": [symbol] if exch_seg_map.get(exchange.upper(), "NSE_EQ") == "NSE_EQ" else [],
        "BSE_EQ": [symbol] if exch_seg_map.get(exchange.upper()) == "BSE_EQ" else [],
    }
    resp = _post(
        f"{_DHAN_BASE}/marketfeed/ltp",
        payload,
        headers=_dhan_headers(access_token, client_id),
    )
    # Response: { "data": { "NSE_EQ": { "<symbol>": { "last_price": 1423.55 } } } }
    seg = exch_seg_map.get(exchange.upper(), "NSE_EQ")
    ltp = resp.get("data", {}).get(seg, {}).get(symbol, {}).get("last_price")
    if ltp is None:
        raise RuntimeError(f"LTP not found in Dhan response: {resp}")
    return float(ltp)


# ---------------------------------------------------------------------------
# Public dispatch interface
# ---------------------------------------------------------------------------

_PLACE_ORDER_DISPATCH = {
    "openalgo":  _openalgo_place_order,
    "flattrade": _flattrade_place_order,
    "mstock":    _mstock_place_order,
    "shoonya":   _shoonya_place_order,
    "dhan":      _dhan_place_order,
}

_GET_LTP_DISPATCH = {
    "openalgo":  _openalgo_get_ltp,
    "flattrade": _flattrade_get_ltp,
    "mstock":    _mstock_get_ltp,
    "shoonya":   _shoonya_get_ltp,
    "dhan":      _dhan_get_ltp,
}


def place_order(cfg: dict, req) -> dict:
    """
    Place an order using the configured trading_api_source.

    Parameters
    ----------
    cfg : full config dict (from config.yml)
    req : OrderRequest pydantic model from app.py

    Returns
    -------
    dict with keys:
        status   : "success" | "error"
        orderid  : str (on success)
        message  : str (on error)
        raw      : dict — raw broker response (on success)
    """
    source = cfg.get("trading_api_source", "openalgo").lower()
    fn = _PLACE_ORDER_DISPATCH.get(source)
    if fn is None:
        return {
            "status":  "error",
            "message": f"Unknown trading_api_source: '{source}'. "
                       f"Valid options: {list(_PLACE_ORDER_DISPATCH)}",
        }
    log.info("Placing %s order for %s via %s", req.action, req.symbol, source.upper())
    result = fn(cfg, req)
    if result.get("status") == "success":
        log.info("✅ Order placed [%s] %s %s qty=%d → orderid=%s",
                 source.upper(), req.action, req.symbol, req.quantity, result.get("orderid", "?"))
    else:
        log.error("❌ Order failed [%s] %s %s: %s",
                  source.upper(), req.action, req.symbol, result.get("message", ""))
    return result


def get_ltp(cfg: dict, symbol: str, exchange: str) -> float:
    """
    Fetch live LTP using the configured trading_api_source.

    Parameters
    ----------
    cfg      : full config dict
    symbol   : trading symbol (e.g. "INFY")
    exchange : exchange code (e.g. "NSE")

    Returns
    -------
    float — last traded price

    Raises
    ------
    RuntimeError if the source is unknown or the broker call fails.
    """
    source = cfg.get("trading_api_source", "openalgo").lower()
    fn = _GET_LTP_DISPATCH.get(source)
    if fn is None:
        raise RuntimeError(
            f"Unknown trading_api_source: '{source}'. "
            f"Valid options: {list(_GET_LTP_DISPATCH)}"
        )
    log.info("Fetching LTP for %s (%s) via %s", symbol, exchange, source.upper())
    return fn(cfg, symbol, exchange)
