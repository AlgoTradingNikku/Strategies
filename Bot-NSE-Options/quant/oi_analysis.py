"""
quant/oi_analysis.py
====================
OI Interpretation Engine — derives all OI signals deterministically.
The LLM receives these pre-computed signals, never raw OI numbers.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import OptionChainSnapshot, OISignals
from ai.schemas.settings import PlatformSettings

log = logging.getLogger("UTBotSRChannelsScanner")

# Minimum OI to consider a strike "significant"
_MIN_SIGNIFICANT_OI = 1000
# Minimum OI change to flag as writing/unwinding
_MIN_OI_CHANGE_THRESHOLD = 500


def analyze_oi(
    snapshot: OptionChainSnapshot,
    settings: Optional[PlatformSettings] = None,
) -> OISignals:
    """
    Derive all OI interpretation signals from the chain snapshot.
    Returns OISignals with all fields populated. Fail-open — never raises.
    """
    if settings and not settings.quant_oi_analysis:
        log.debug("[OI] quant_oi_analysis toggle is OFF")
        return OISignals()

    try:
        chain = snapshot.chain
        if not chain:
            return OISignals()

        total_call_oi = sum(s.ce.oi for s in chain if s.ce)
        total_put_oi = sum(s.pe.oi for s in chain if s.pe)
        total_call_vol = sum(s.ce.volume for s in chain if s.ce)
        total_put_vol = sum(s.pe.volume for s in chain if s.pe)

        pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0
        pcr_volume = (total_put_vol / total_call_vol) if total_call_vol > 0 else 0.0

        # Near-ATM PCR (±2 strikes)
        atm = snapshot.atm_strike
        near_atm = [s for s in chain if abs(s.strike - atm) <= 2 * _guess_strike_gap(chain)]
        near_call_oi = sum(s.ce.oi for s in near_atm if s.ce)
        near_put_oi = sum(s.pe.oi for s in near_atm if s.pe)
        near_atm_pcr = (near_put_oi / near_call_oi) if near_call_oi > 0 else 0.0

        # Max OI strikes (support/resistance)
        max_call_strike = _max_oi_strike(chain, "ce")
        max_put_strike = _max_oi_strike(chain, "pe")

        # Writing zones: OI increasing + price stable/falling (CE) or stable/rising (PE)
        call_writing = _detect_writing_zones(chain, "ce")
        put_writing = _detect_writing_zones(chain, "pe")
        call_unwinding = _detect_unwinding_zones(chain, "ce")
        put_unwinding = _detect_unwinding_zones(chain, "pe")

        # Top 3 support (from put OI concentration) and resistance (from call OI)
        oi_resistance = _top_oi_levels(chain, "ce", n=3)
        oi_support = _top_oi_levels(chain, "pe", n=3)

        # OI trend: if put OI > call OI and growing → BEARISH (put buying)
        # if call OI > put OI and growing → BULLISH (call buying)
        oi_trend = "NEUTRAL"
        if pcr > 1.2:
            oi_trend = "BEARISH"  # heavy put loading
        elif pcr < 0.8:
            oi_trend = "BULLISH"  # heavy call loading

        return OISignals(
            pcr=round(pcr, 3),
            pcr_volume=round(pcr_volume, 3),
            near_atm_pcr=round(near_atm_pcr, 3),
            max_call_oi_strike=max_call_strike,
            max_put_oi_strike=max_put_strike,
            call_writing_zones=call_writing,
            put_writing_zones=put_writing,
            call_unwinding_zones=call_unwinding,
            put_unwinding_zones=put_unwinding,
            oi_resistance_levels=oi_resistance,
            oi_support_levels=oi_support,
            oi_trend=oi_trend,
        )

    except Exception as exc:
        log.warning("[OI] analyze_oi failed: %s", exc)
        return OISignals()


# ── helpers ──────────────────────────────────────────────────────────────────

def _guess_strike_gap(chain: list) -> float:
    strikes = sorted(s.strike for s in chain)
    return abs(strikes[1] - strikes[0]) if len(strikes) >= 2 else 50.0


def _max_oi_strike(chain: list, side: str) -> float:
    best, best_oi = 0.0, 0
    for s in chain:
        leg = getattr(s, side)
        if leg and leg.oi > best_oi:
            best_oi = leg.oi
            best = s.strike
    return best


def _top_oi_levels(chain: list, side: str, n: int = 3) -> list[float]:
    pairs = [(s.strike, getattr(s, side).oi) for s in chain if getattr(s, side)]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:n]]


def _detect_writing_zones(chain: list, side: str) -> list[float]:
    """
    Writing = OI increasing (oi_change > threshold) + price stable/moving against writer.
    CE writing: call OI up + CE ltp not rising significantly.
    PE writing: put OI up + PE ltp not falling significantly.
    """
    zones = []
    for s in chain:
        leg = getattr(s, side)
        if not leg or leg.oi < _MIN_SIGNIFICANT_OI:
            continue
        if leg.oi_change > _MIN_OI_CHANGE_THRESHOLD:
            if side == "ce" and leg.ltp > 0:
                zones.append(s.strike)
            elif side == "pe" and leg.ltp > 0:
                zones.append(s.strike)
    return zones[:5]  # cap at 5 zones


def _detect_unwinding_zones(chain: list, side: str) -> list[float]:
    """Unwinding = OI decreasing significantly (oi_change < -threshold)."""
    zones = []
    for s in chain:
        leg = getattr(s, side)
        if not leg:
            continue
        if leg.oi_change < -_MIN_OI_CHANGE_THRESHOLD:
            zones.append(s.strike)
    return zones[:5]
