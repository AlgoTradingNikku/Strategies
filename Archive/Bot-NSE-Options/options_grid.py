"""
===============================================================================
  options_grid.py — Option strike grid builder & Auto ATM strike calculation
===============================================================================

Builds -3 to +3 levels strike grid (14 contracts: 7 CE + 7 PE) around ATM strike.
Supports Auto-ATM strike calculation from live underlying spot price (e.g., Nifty 24386.95 -> ATM 24400).
Supports custom strike gaps (e.g., 50, 100, 250).
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger("UTBotSRChannelsScanner")

# Standard NSE option symbol regex: UNDERLYING + DDMMMYY + STRIKE + optional (CE|PE)
_BASE_OPT_RE = re.compile(
    r"^(?P<underlying>[A-Z]+)"
    r"(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yr>\d{2})"
    r"(?P<strike>\d+(?:\.\d+)?)"
    r"(?P<type>CE|PE)?$"
)

# Standard index strike step defaults
_DEFAULT_INDEX_GAPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
    "BANKEX": 100,
}


def parse_base_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parse a base option contract string like 'NIFTY18AUG2624600' or 'NIFTY18AUG2624600CE'.
    Returns dict with underlying, expiry, strike, option_type.
    """
    if not symbol or not isinstance(symbol, str):
        return None

    m = _BASE_OPT_RE.match(symbol.strip().upper())
    if not m:
        return None

    return {
        "underlying": m.group("underlying"),
        "expiry": f"{m.group('day')}{m.group('mon')}{m.group('yr')}",
        "strike": float(m.group("strike")),
        "type": m.group("type"),
    }


def get_strike_step(underlying: str, configured_gap: Any = None) -> float:
    """Return the step gap between strike levels."""
    if configured_gap is not None and str(configured_gap).strip().lower() not in ("auto", ""):
        try:
            return float(configured_gap)
        except (ValueError, TypeError):
            pass
    return float(_DEFAULT_INDEX_GAPS.get(underlying.upper(), 50))


def calculate_auto_atm_strike(spot_price: float, step: float) -> float:
    """Calculate nearest ATM strike from spot price and strike gap step."""
    if spot_price <= 0 or step <= 0:
        return 24400.0
    return round(spot_price / step) * step


def generate_option_strike_grid(
    base_symbol_or_params: Any = None,
    levels_up_down: int = 3,
    configured_gap: Any = None,
    openalgo_client: Any = None,
    exchange: str = "NSE_INDEX",
    spot_price: Optional[float] = None,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Build 3 levels up & down option grid (14 contracts: 7 CE + 7 PE).

    base_symbol_or_params can be:
      - A plain strike number string e.g. '24300' or integer 24300 (recommended)
      - A full legacy symbol string e.g. 'NIFTY18AUG2624300' (still supported)
      - Empty / blank / None  → auto-ATM: calculate strike from live spot price
    Underlying and expiry are always taken from config when not encoded in symbol.
    """
    config = config or {}
    opt_cfg = config.get("options", {})
    underlying = opt_cfg.get("underlying", "NIFTY")
    expiry = opt_cfg.get("expiry_date", "18AUG26")
    atm_strike = 24400.0

    step = get_strike_step(underlying, configured_gap)
    is_auto = False

    # Normalise input: convert to string for uniform handling
    raw = str(base_symbol_or_params).strip() if base_symbol_or_params is not None else ""

    if raw == "" or raw.lower() == "auto" or raw == "none":
        # ── Auto-ATM mode ──────────────────────────────────────────────────────
        is_auto = True
        if spot_price is None or spot_price <= 0:
            try:
                import trading_adapter
                spot_price = trading_adapter.get_ltp(config, underlying, exchange=exchange)
            except Exception as e:
                log.debug("Auto ATM spot fetch exception: %s", e)

        if spot_price and spot_price > 0:
            atm_strike = calculate_auto_atm_strike(spot_price, step)
            log.info("[AutoATM] Spot LTP for %s: %.2f -> Calculated ATM Strike: %.1f (Step: %.0f)",
                     underlying, spot_price, atm_strike, step)
        else:
            atm_strike = 24400.0
            log.info("[AutoATM] Spot LTP unavailable. Fallback ATM Strike: %.1f", atm_strike)

    else:
        # Try to parse as a plain strike number first (e.g. '24300')
        try:
            atm_strike = float(raw)
            # underlying and expiry already loaded from config above
            log.info("[ATM] Using configured strike: %.1f (underlying=%s, expiry=%s)",
                     atm_strike, underlying, expiry)
        except ValueError:
            # Fallback: try legacy full symbol format e.g. 'NIFTY18AUG2624300'
            parsed = parse_base_option_symbol(raw)
            if parsed:
                underlying = parsed["underlying"]
                expiry = parsed["expiry"]
                atm_strike = parsed["strike"]
                log.info("[ATM] Parsed legacy symbol %s -> strike=%.1f expiry=%s", raw, atm_strike, expiry)
            else:
                log.warning("[ATM] Cannot parse '%s' as strike or symbol. Using auto-ATM.", raw)
                is_auto = True
                if spot_price and spot_price > 0:
                    atm_strike = calculate_auto_atm_strike(spot_price, step)
                else:
                    atm_strike = 24400.0

    offsets = list(range(-levels_up_down, levels_up_down + 1))  # [-3, -2, -1, 0, 1, 2, 3]
    strikes = [atm_strike + (o * step) for o in offsets]

    ce_symbols = []
    pe_symbols = []
    all_symbols = []
    contract_details = []

    for idx, s in enumerate(strikes):
        strike_str = f"{int(s)}" if s.is_integer() else f"{s}"
        ce_sym = f"{underlying}{expiry}{strike_str}CE"
        pe_sym = f"{underlying}{expiry}{strike_str}PE"

        offset_label = (
            f"ITM{-offsets[idx]}" if offsets[idx] < 0 else (
                "ATM" if offsets[idx] == 0 else f"OTM{offsets[idx]}"
            )
        )

        ce_symbols.append(ce_sym)
        pe_symbols.append(pe_sym)
        all_symbols.extend([ce_sym, pe_sym])

        contract_details.append({
            "strike": s,
            "ce_symbol": ce_sym,
            "pe_symbol": pe_sym,
            "ce_offset": offset_label,
            "is_atm": offsets[idx] == 0,
        })

    return {
        "underlying": underlying,
        "expiry": expiry,
        "atm_strike": atm_strike,
        "strike_gap": step,
        "is_auto_atm": is_auto,
        "spot_price": spot_price,
        "strikes": strikes,
        "ce_symbols": ce_symbols,
        "pe_symbols": pe_symbols,
        "symbols": all_symbols,
        "contract_details": contract_details,
    }
