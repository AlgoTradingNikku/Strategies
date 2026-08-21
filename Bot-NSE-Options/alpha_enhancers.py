"""
===============================================================================
  alpha_enhancers.py — Sprint 4: Alpha Enhancements
===============================================================================
Alpha-generating layers on top of the risk foundation. These are NOT
loss-stoppers — they're edge finders that upgrade signal quality using
market context (regime, greeks, session, volume profile).

Provides:
  1. get_vix_regime(cfg)              — LOW/NORMAL/HIGH from INDIA VIX quote
  2. get_regime_multiplier(cfg, regime)— risk% multiplier per regime
  3. get_session_bucket(cfg, now)     — 'opening'/'prime'/'closing'/'off'
  4. get_session_bonus(cfg, bucket)   — score bonus/malus for the session
  5. compute_poc(df)                  — Point-of-Control price from intraday df
  6. check_poc_distance(cfg, price, poc) — reject signals far from POC
  7. check_greeks(cfg, quote)         — reject deep-OTM (|delta|<min) / high-theta
  8. check_strict_mtf(cfg, mtfs)      — hard 3-timeframe alignment (opt-in)

All checks fail-open on missing data. Fully toggleable via config.yml
`alpha_enhancers:` section.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Dict, Optional, Tuple, List

import numpy as np
import pandas as pd

log = logging.getLogger("UTBotSRChannelsScanner")


# ============================================================================
# VIX regime detection
# ============================================================================

def get_vix_regime(cfg: dict) -> Tuple[str, float]:
    """
    Reads INDIA VIX from OpenAlgo /quote and classifies into LOW/NORMAL/HIGH.
    Returns (regime_str, vix_value). On any failure returns ('UNKNOWN', 0.0)
    which callers treat as NORMAL (fail-open).
    """
    ae = cfg.get("alpha_enhancers", {}).get("vix_regime", {})
    if not ae.get("enabled", True):
        return "DISABLED", 0.0

    low_th = float(ae.get("low_threshold", 15.0))
    high_th = float(ae.get("high_threshold", 22.0))
    vix_symbol = str(ae.get("vix_symbol", "INDIAVIX"))
    vix_exchange = str(ae.get("vix_exchange", "NSE_INDEX"))

    try:
        import trading_adapter
        ltp = trading_adapter.get_ltp(cfg, vix_symbol, exchange=vix_exchange)
        vix = float(ltp or 0.0)
        if vix <= 0:
            return "UNKNOWN", 0.0
        if vix < low_th:
            return "LOW", vix
        if vix > high_th:
            return "HIGH", vix
        return "NORMAL", vix
    except Exception as exc:
        log.debug("[alpha] get_vix_regime error: %s", exc)
        return "UNKNOWN", 0.0


def get_regime_multiplier(cfg: dict, regime: str) -> float:
    """Return risk-per-trade multiplier for the current VIX regime."""
    ae = cfg.get("alpha_enhancers", {}).get("vix_regime", {})
    if not ae.get("enabled", True):
        return 1.0
    table = ae.get("risk_multipliers", {}) or {}
    return float(table.get(str(regime).upper(), 1.0))


# ============================================================================
# Session weighting
# ============================================================================

def _parse_hhmm(s: str, default: time) -> time:
    try:
        h, m = str(s).split(":")
        return time(int(h), int(m))
    except Exception:
        return default


def get_session_bucket(cfg: dict, now: Optional[datetime] = None) -> str:
    """
    Returns which intraday session the current time falls into.
    Buckets: 'opening' (first N mins), 'prime' (mid-session, best),
             'closing' (last N mins, fade-only), 'off' (outside market).
    """
    ae = cfg.get("alpha_enhancers", {}).get("session_weighting", {})
    if not ae.get("enabled", True):
        return "prime"  # disabled → treat everything as prime (no bonus/malus)

    now = now or datetime.now()
    t = now.time()

    mkt_open = _parse_hhmm(cfg.get("bot", {}).get("market_open", "09:15"), time(9, 15))
    mkt_close = _parse_hhmm(cfg.get("bot", {}).get("market_close", "15:30"), time(15, 30))
    open_mins = int(ae.get("opening_minutes", 30))
    close_mins = int(ae.get("closing_minutes", 30))

    if t < mkt_open or t > mkt_close:
        return "off"

    open_end_h = mkt_open.hour + (mkt_open.minute + open_mins) // 60
    open_end_m = (mkt_open.minute + open_mins) % 60
    open_end = time(open_end_h % 24, open_end_m)

    close_start_total = mkt_close.hour * 60 + mkt_close.minute - close_mins
    close_start = time(close_start_total // 60, close_start_total % 60)

    if t < open_end:
        return "opening"
    if t >= close_start:
        return "closing"
    return "prime"


def get_session_bonus(cfg: dict, bucket: str) -> float:
    """Return per-session score modifier (added to weighted score)."""
    ae = cfg.get("alpha_enhancers", {}).get("session_weighting", {})
    if not ae.get("enabled", True):
        return 0.0
    table = ae.get("bonuses", {}) or {}
    return float(table.get(str(bucket).lower(), 0.0))


# ============================================================================
# Volume-Profile POC (Point of Control)
# ============================================================================

def compute_poc(df: pd.DataFrame, price_bins: int = 40) -> float:
    """
    Compute today's Point of Control = the price level with the highest traded
    volume from the intraday bar dataframe. Returns 0.0 on error / empty df.

    Uses hlc3 (typical price) per bar × volume as the vol-at-price distribution.
    """
    try:
        if df is None or df.empty or "volume" not in df.columns:
            return 0.0
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        typical = (high + low + close) / 3.0
        lo, hi = float(low.min()), float(high.max())
        if hi <= lo:
            return float(close.iloc[-1])
        bins = np.linspace(lo, hi, price_bins + 1)
        # Bin each bar's typical price and sum volumes per bin
        typical_arr = typical.to_numpy(dtype=float)
        volume_arr = volume.to_numpy(dtype=float)
        idx = np.clip(np.digitize(typical_arr, bins) - 1, 0, price_bins - 1)
        vol_at_price = np.zeros(price_bins)
        for i, v in zip(idx, volume_arr):
            vol_at_price[int(i)] += float(v)
        poc_bin = int(np.argmax(vol_at_price))
        return float((bins[poc_bin] + bins[poc_bin + 1]) / 2.0)
    except Exception as exc:
        log.debug("[alpha] compute_poc error: %s", exc)
        return 0.0


def check_poc_distance(cfg: dict, price: float, poc: float) -> Tuple[bool, str, float]:
    """Returns (ok, reason, distance_pct). Fail-open when poc == 0."""
    ae = cfg.get("alpha_enhancers", {}).get("volume_profile", {})
    if not ae.get("enabled", True):
        return True, "", 0.0
    if poc <= 0 or price <= 0:
        return True, "", 0.0
    max_pct = float(ae.get("max_poc_distance_pct", 1.5))
    dist_pct = abs(price - poc) / poc * 100.0
    if dist_pct > max_pct:
        return False, f"FAR_FROM_POC({dist_pct:.2f}%>{max_pct:.2f}%)", dist_pct
    return True, "", dist_pct


# ============================================================================
# Options Greeks filter
# ============================================================================

def check_greeks(cfg: dict, quote: Optional[dict]) -> Tuple[bool, str, Dict[str, float]]:
    """Rejects deep-OTM (|delta|<min) / high-theta. Fail-open when greeks missing."""
    ae = cfg.get("alpha_enhancers", {}).get("greeks", {})
    greeks = {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}
    if not ae.get("enabled", True):
        return True, "", greeks
    if not quote:
        return True, "", greeks

    try:
        greeks["delta"] = float(quote.get("delta") or 0.0)
        greeks["theta"] = float(quote.get("theta") or 0.0)
        greeks["gamma"] = float(quote.get("gamma") or 0.0)
        greeks["vega"] = float(quote.get("vega") or 0.0)
    except Exception:
        return True, "", greeks

    if greeks["delta"] == 0.0 and greeks["theta"] == 0.0:
        return True, "", greeks  # no greeks in payload → fail-open

    min_abs_delta = float(ae.get("min_abs_delta", 0.20))
    if abs(greeks["delta"]) > 0 and abs(greeks["delta"]) < min_abs_delta:
        return False, f"LOW_DELTA({abs(greeks['delta']):.2f}<{min_abs_delta:.2f})", greeks

    ltp = float(quote.get("ltp") or 0.0)
    if ltp > 0 and greeks["theta"] != 0.0:
        max_theta_pct = float(ae.get("max_theta_pct", 5.0))
        theta_pct = abs(greeks["theta"]) / ltp * 100.0
        if theta_pct > max_theta_pct:
            return False, f"HIGH_THETA({theta_pct:.2f}%>{max_theta_pct:.2f}%)", greeks

    return True, "", greeks


# ============================================================================
# Strict multi-timeframe alignment (opt-in aggressive filter)
# ============================================================================

def check_strict_mtf(cfg: dict, mtf_results: Dict[str, bool]) -> Tuple[bool, str]:
    """Requires ALL listed timeframes to agree. Off by default."""
    ae = cfg.get("alpha_enhancers", {}).get("strict_mtf", {})
    if not ae.get("enabled", False):
        return True, ""
    required = ae.get("required_timeframes", ["5m", "15m"])
    disagreements = [tf for tf in required if not mtf_results.get(tf, True)]
    if disagreements:
        return False, f"MTF_DISAGREE({','.join(disagreements)})"
    return True, ""


# ============================================================================
# Composite gate for scanner
# ============================================================================

def run_alpha_filters(
    cfg: dict,
    *,
    price: float,
    poc: float,
    quote: Optional[dict],
    mtf_results: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Aggregator for scanner. Returns reject_reason + telemetry dict."""
    ae_cfg = cfg.get("alpha_enhancers", {})
    reason = ""
    poc_ok, r_poc, dist_pct = check_poc_distance(cfg, price, poc)
    if not poc_ok:
        reason = r_poc
    g_ok, r_g, greeks = check_greeks(cfg, quote)
    if not reason and not g_ok:
        reason = r_g
    m_ok, r_m = check_strict_mtf(cfg, mtf_results or {})
    if not reason and not m_ok:
        reason = r_m
    return {
        "reject_reason": reason,
        "poc_distance_pct": round(dist_pct, 3),
        "greeks": greeks,
        "mtf_strict_pass": m_ok,
        "enabled": bool(ae_cfg.get("enabled", True)),
    }
