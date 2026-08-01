"""
===============================================================================
  Bot-Options / data / option_chain.py
  Option chain data layer — fetches live options chain from OpenAlgo,
  processes strike info, and provides greeks/LTP helper operations.
===============================================================================
"""

import logging
from typing import Optional, Dict, List, Any

log = logging.getLogger(__name__)

def fetch_option_chain(
    underlying: str,
    expiry_date: str,  # format '28OCT25'
    oa_client,
    strike_count: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch live option chain from OpenAlgo for the specified underlying and expiry.

    Parameters
    ----------
    underlying  : e.g. "NIFTY"
    expiry_date : e.g. "30DEC25" (OpenAlgo format)
    oa_client   : initialized openalgo api client
    strike_count: optional number of strikes to fetch around ATM

    Returns
    -------
    Dictionary containing option chain, underlying LTP, ATM strike, or None on error.
    """
    try:
        # OpenAlgo option chain API call
        params = {
            "underlying": underlying,
            "exchange": "NSE_INDEX",
            "expiry_date": expiry_date,
        }
        if strike_count is not None:
            params["strike_count"] = strike_count

        chain_data = oa_client.optionchain(**params)

        if not isinstance(chain_data, dict) or chain_data.get("status") != "success":
            log.error("[%s] Failed to fetch option chain: %s", underlying, chain_data)
            return None

        return chain_data
    except Exception as e:
        log.error("[%s] Error fetching option chain for expiry %s: %s", underlying, expiry_date, e)
        return None


def get_atm_strike(chain_data: dict[str, Any]) -> Optional[float]:
    """Helper to extract ATM strike from option chain data."""
    if not chain_data:
        return None
    return chain_data.get("atm_strike") or chain_data.get("underlying_ltp")


def get_option_symbols_for_strike(
    chain_data: dict[str, Any],
    strike: float,
) -> Optional[Tuple[str, str]]:
    """
    Retrieve CE and PE symbols for a given strike from fetched chain data.
    Returns (ce_symbol, pe_symbol) or None if strike not found.
    """
    if not chain_data or "chain" not in chain_data:
        return None

    for item in chain_data["chain"]:
        if float(item.get("strike", 0)) == float(strike):
            ce_sym = item.get("ce", {}).get("symbol")
            pe_sym = item.get("pe", {}).get("symbol")
            return ce_sym, pe_sym

    return None
