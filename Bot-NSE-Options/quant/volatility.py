"""
quant/volatility.py
====================
Volatility and IV metrics engine.
All values computed deterministically from chain data + VIX.
"""
from __future__ import annotations
import logging
import math
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import OptionChainSnapshot, VolatilityData
from ai.schemas.settings import PlatformSettings

log = logging.getLogger("UTBotSRChannelsScanner")


def compute_volatility_metrics(
    snapshot: OptionChainSnapshot,
    vix: float,
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> VolatilityData:
    """
    Compute all volatility metrics from chain snapshot + VIX.
    Returns VolatilityData. Fail-open.
    """
    if settings and not settings.quant_iv_engine:
        log.debug("[Vol] quant_iv_engine toggle is OFF")
        return VolatilityData()

    try:
        regime_cfg = cfg.get("regime", {})

        # VIX regime classification
        vix_low = float(regime_cfg.get("vix_low_threshold", 13))
        vix_high = float(regime_cfg.get("vix_high_threshold", 20))
        if vix <= 0:
            vix_regime = "UNKNOWN"
        elif vix < vix_low:
            vix_regime = "LOW"
        elif vix > vix_high:
            vix_regime = "HIGH"
        else:
            vix_regime = "NORMAL"

        # ATM IV from chain (average of ATM CE + PE IV if available)
        atm_iv = _get_atm_iv(snapshot)

        # Expected move: ATM_IV * Spot * sqrt(DTE / 365)
        dte = _estimate_dte(snapshot.expiry_date)
        spot = snapshot.underlying_ltp
        expected_move_pct = 0.0
        expected_move_abs = 0.0
        if atm_iv > 0 and spot > 0 and dte > 0:
            expected_move_pct = (atm_iv / 100.0) * math.sqrt(dte / 365.0) * 100
            expected_move_abs = spot * (atm_iv / 100.0) * math.sqrt(dte / 365.0)

        # IV rank/percentile: use VIX as proxy when per-strike IV unavailable
        # In MVP 1, IV Rank is estimated from VIX relative to its typical range
        iv_rank = _estimate_iv_rank(vix)
        iv_percentile = iv_rank  # Same as rank without historical data in MVP 1

        # IV regime
        iv_rank_high = float(regime_cfg.get("iv_rank_high_threshold", 50))
        iv_rank_low = float(regime_cfg.get("iv_rank_low_threshold", 30))
        if iv_rank > iv_rank_high:
            iv_regime = "HIGH"
        elif iv_rank < iv_rank_low:
            iv_regime = "LOW"
        else:
            iv_regime = "NORMAL"

        # VIX change (not available without historical VIX — set to 0 in MVP 1)
        vix_change_pct = 0.0

        return VolatilityData(
            india_vix=round(vix, 2),
            vix_change_pct=round(vix_change_pct, 3),
            vix_regime=vix_regime,
            atm_iv=round(atm_iv, 2),
            iv_rank=round(iv_rank, 1),
            iv_percentile=round(iv_percentile, 1),
            expected_move_abs=round(expected_move_abs, 2),
            expected_move_pct=round(expected_move_pct, 3),
            iv_regime=iv_regime,
        )

    except Exception as exc:
        log.warning("[Vol] compute_volatility_metrics failed: %s", exc)
        return VolatilityData()


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_atm_iv(snapshot: OptionChainSnapshot) -> float:
    """Extract ATM IV as average of ATM CE and PE IV (if Greeks were fetched)."""
    atm_strike = snapshot.atm_strike
    for s in snapshot.chain:
        if s.strike == atm_strike:
            ivs = []
            if s.ce and s.ce.iv > 0:
                ivs.append(s.ce.iv)
            if s.pe and s.pe.iv > 0:
                ivs.append(s.pe.iv)
            if ivs:
                return sum(ivs) / len(ivs)
    return 0.0


def _estimate_dte(expiry_date: str) -> int:
    """Estimate Days to Expiry from expiry string like '24JUL25'."""
    try:
        from datetime import datetime
        expiry_date = expiry_date.strip().upper()
        for fmt in ("%d%b%y", "%d%b%Y"):
            try:
                expiry_dt = datetime.strptime(expiry_date, fmt)
                dte = (expiry_dt - datetime.now()).days
                return max(1, dte)
            except ValueError:
                continue
    except Exception:
        pass
    return 7  # default to weekly


def _estimate_iv_rank(vix: float) -> float:
    """
    Estimate IV Rank from VIX using historical NIFTY VIX range (approx 10-35).
    Returns 0-100. In MVP 2 this will use actual 52-week IV history.
    """
    if vix <= 0:
        return 50.0  # neutral assumption
    vix_min, vix_max = 10.0, 35.0
    rank = (vix - vix_min) / (vix_max - vix_min) * 100
    return max(0.0, min(100.0, rank))
