"""
===============================================================================
  Bot-Options / data / instrument_resolver.py
  Resolves option contract details, parses symbol strings, and fetches
  instrument metadata from OpenAlgo.
===============================================================================
"""

import re
import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

# Pattern to parse standard NSE options format: NIFTY2581523450CE / NIFTY28OCT2526150PE
# Group 1: Underlying (NIFTY/BANKNIFTY)
# Group 2: Expiry date (e.g. 25815 or 28OCT25)
# Group 3: Strike price (e.g. 23450 or 26150)
# Group 4: Option type (CE/PE)
OPTION_SYMBOL_PATTERN = re.compile(r"^([A-Z]+)(\d{2}[A-Z\d]{3,5}\d{2})(\d+)([CP]E)$")

def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parse a standard NSE option symbol string into constituent parts.
    Example: 'NIFTY28OCT2526150PE' -> {
        'underlying': 'NIFTY',
        'expiry_str': '28OCT25',
        'strike': 26150.0,
        'option_type': 'PE'
    }
    """
    match = OPTION_SYMBOL_PATTERN.match(symbol)
    if not match:
        return None
    
    underlying, expiry_str, strike_str, option_type = match.groups()
    try:
        return {
            "underlying": underlying,
            "expiry_str": expiry_str,
            "strike": float(strike_str),
            "option_type": option_type
        }
    except Exception as e:
        log.error("Failed parsing numeric parts of symbol %s: %s", symbol, e)
        return None


def resolve_instrument_token(
    symbol: str,
    exchange: str,
    oa_client
) -> Optional[str]:
    """
    Query OpenAlgo search endpoint to resolve symbol to broker token.
    Example: symbol 'NIFTY30DEC2526000CE' -> 'NSE_FO|71399'
    """
    try:
        resp = oa_client.search(query=symbol, exchange=exchange)
        if isinstance(resp, dict) and resp.get("status") == "success":
            results = resp.get("data", [])
            for res in results:
                if res.get("symbol") == symbol:
                    return res.get("token")
        return None
    except Exception as e:
        log.error("Error resolving instrument token for %s: %s", symbol, e)
        return None
