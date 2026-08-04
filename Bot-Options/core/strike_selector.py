"""
===============================================================================
  Bot-Options / core / strike_selector.py
  Strike Selection Engine — selects the target CE/PE contract for a signal.

  Two execution paths:

  FAST PATH  (ATM, OTM, ITM, TREND)
  ─────────────────────────────────
  The target strike is fully determined by arithmetic:
      ATM = round(spot / strike_step) × strike_step
      target = ATM ± (N × strike_step)
  The symbol is constructed directly and a single /quotes call fetches LTP+OI
  for that one contract. Zero option-chain fetches needed.

  CHAIN PATH  (PREMIUM, LIQUIDITY, DELTA)
  ────────────────────────────────────────
  These methods must compare multiple strikes simultaneously so a chain is
  required. option_scanner.py passes a pre-fetched chain only when the method
  is in _CHAIN_REQUIRED_METHODS.

  option_scanner.py decides which path to take via needs_chain().
===============================================================================
"""

import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

# Methods that need a full chain fetch vs those that only need a single quote.
_CHAIN_REQUIRED_METHODS = {"PREMIUM", "LIQUIDITY", "DELTA"}


def needs_chain(selection_cfg: dict) -> bool:
    """Return True when the configured method requires a full option chain."""
    return selection_cfg.get("method", "ATM").upper() in _CHAIN_REQUIRED_METHODS


def _round_to_strike(price: float, step: float) -> float:
    """Round spot price to the nearest valid strike grid."""
    return round(round(price / step) * step, 2)


def _build_symbol(underlying: str, expiry_str: str, strike: float, option_type: str) -> str:
    """
    Construct NSE option symbol in OpenAlgo format.
    e.g. NIFTY + 11AUG26 + 24500 + CE → 'NIFTY11AUG2624500CE'
    """
    strike_int = int(strike) if strike == int(strike) else strike
    return f"{underlying}{expiry_str}{strike_int}{option_type}"


def select_strike_fast(
    underlying: str,
    expiry_str: str,         # OpenAlgo format e.g. '11AUG26'
    spot_ltp: float,
    option_type: str,        # 'CE' or 'PE'
    selection_cfg: dict,
    strike_step: float,
    oa_client,
) -> Optional[Dict[str, Any]]:
    """
    Fast path: compute target strike from spot + arithmetic, build symbol,
    fetch a single /quotes call. No chain required.

    Used for methods: ATM, OTM, ITM, TREND.
    """
    method = selection_cfg.get("method", "ATM").upper()

    atm = _round_to_strike(spot_ltp, strike_step)

    if method == "ATM":
        target_strike = atm

    elif method == "OTM":
        offset = int(selection_cfg.get("otm_strikes", 1)) * strike_step
        target_strike = atm + offset if option_type == "CE" else atm - offset

    elif method == "ITM":
        offset = int(selection_cfg.get("itm_strikes", 1)) * strike_step
        target_strike = atm - offset if option_type == "CE" else atm + offset

    elif method == "TREND":
        offset = int(selection_cfg.get("trend_itm_offset", 1)) * strike_step
        # ITM bias: CE → lower strike, PE → higher strike
        target_strike = atm - offset if option_type == "CE" else atm + offset

    else:
        log.warning("select_strike_fast called for unsupported method '%s'. Defaulting to ATM.", method)
        target_strike = atm

    symbol = _build_symbol(underlying, expiry_str, target_strike, option_type)
    log.debug("[%s] Fast strike: method=%s spot=%.2f ATM=%.0f target=%.0f symbol=%s",
              underlying, method, spot_ltp, atm, target_strike, symbol)

    # Single quotes call for this one symbol
    try:
        resp = oa_client.quotes(symbol=symbol, exchange="NFO")
        ltp = float(resp.get("data", {}).get("ltp") or resp.get("ltp") or 0)
        oi  = int(  resp.get("data", {}).get("oi")  or resp.get("oi")  or 0)
        vol = int(  resp.get("data", {}).get("volume") or resp.get("volume") or 0)
    except Exception as e:
        log.warning("[%s] quotes() failed for %s: %s", underlying, symbol, e)
        ltp, oi, vol = 0.0, 0, 0

    if ltp <= 0:
        log.warning("[%s] Zero LTP for %s — contract may be illiquid or market closed.", underlying, symbol)

    # Hard liquidity filters
    min_oi  = float(selection_cfg.get("oi_min_threshold", 0))
    min_vol = float(selection_cfg.get("liquidity_min_volume", 0))
    if oi < min_oi:
        log.warning("%s rejected: OI %d < threshold %d", symbol, oi, min_oi)
        return None
    if vol < min_vol:
        log.warning("%s rejected: volume %d < threshold %d", symbol, vol, min_vol)
        return None

    return {
        "symbol":      symbol,
        "strike":      target_strike,
        "option_type": option_type,
        "ltp":         ltp,
        "oi":          oi,
        "volume":      vol,
        "iv":          0.0,   # not available from a plain quotes call
    }


def select_strike(
    chain_data: Dict[str, Any],
    option_type: str,
    selection_cfg: dict,
    strike_step: float = 50.0,
) -> Optional[Dict[str, Any]]:
    """
    Chain path: used for PREMIUM, LIQUIDITY, DELTA methods that must compare
    multiple strikes simultaneously. chain_data is the full OpenAlgo chain dict.
    """
    if not chain_data or "chain" not in chain_data or not chain_data["chain"]:
        log.warning("No chain data available for strike selection.")
        return None

    method = selection_cfg.get("method", "ATM").upper()

    chain_list = sorted(chain_data["chain"], key=lambda x: float(x.get("strike", 0)))
    strikes = [float(x["strike"]) for x in chain_list]
    if not strikes:
        return None

    underlying_ltp = float(chain_data.get("underlying_ltp", 0))
    atm_val = chain_data.get("atm_strike")
    atm_val = float(atm_val) if atm_val is not None else min(strikes, key=lambda x: abs(x - underlying_ltp))
    try:
        atm_idx = strikes.index(atm_val)
    except ValueError:
        atm_idx = strikes.index(min(strikes, key=lambda x: abs(x - atm_val)))

    log.debug("Chain ATM Strike: %.0f (idx %d) | Spot: %.2f", strikes[atm_idx], atm_idx, underlying_ltp)

    if method == "PREMIUM":
        p_min = float(selection_cfg.get("premium_min", 50))
        p_max = float(selection_cfg.get("premium_max", 500))
        eligible = [
            (i, float(item.get(option_type.lower(), {}).get("ltp", 0)))
            for i, item in enumerate(chain_list)
            if p_min <= float(item.get(option_type.lower(), {}).get("ltp", 0)) <= p_max
        ]
        if not eligible:
            log.warning("No strikes in premium range [%.0f, %.0f] for %s.", p_min, p_max, option_type)
            return None
        target_idx = min(eligible, key=lambda x: abs(x[0] - atm_idx))[0]

    elif method == "LIQUIDITY":
        best_idx, best_score = atm_idx, -1.0
        for i in range(max(0, atm_idx - 5), min(len(strikes), atm_idx + 6)):
            opt = chain_list[i].get(option_type.lower(), {})
            score = float(opt.get("oi", 0)) * float(opt.get("volume", 0))
            if score > best_score:
                best_score, best_idx = score, i
        target_idx = best_idx

    elif method == "DELTA":
        target_delta = float(selection_cfg.get("target_delta", 0.40))
        best_idx, best_diff = atm_idx, float("inf")
        for i, item in enumerate(chain_list):
            delta = item.get(option_type.lower(), {}).get("delta")
            if delta is not None:
                diff = abs(float(delta) - target_delta)
                if diff < best_diff:
                    best_diff, best_idx = diff, i
        if best_diff == float("inf"):
            log.warning("DELTA method: no delta values in chain. Defaulting to ATM.")
        target_idx = best_idx

    else:
        log.warning("select_strike (chain path) called for method '%s' — use select_strike_fast instead.", method)
        target_idx = atm_idx

    item = chain_list[target_idx]
    contract = dict(item.get(option_type.lower(), {}))
    if not contract:
        return None

    contract["strike"]      = float(item.get("strike"))
    contract["option_type"] = option_type

    min_oi  = float(selection_cfg.get("oi_min_threshold", 0))
    min_vol = float(selection_cfg.get("liquidity_min_volume", 0))
    if float(contract.get("oi", 0)) < min_oi:
        log.warning("%s rejected by OI filter.", contract.get("symbol"))
        return None
    if float(contract.get("volume", 0)) < min_vol:
        log.warning("%s rejected by volume filter.", contract.get("symbol"))
        return None

    return contract
