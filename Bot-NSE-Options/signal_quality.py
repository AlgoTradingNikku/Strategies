"""
===============================================================================
  signal_quality.py — Sprint 2: Signal-Quality Scoring & Filters
===============================================================================
Advanced pre-trade filters that run BEFORE the Sprint-1 risk gates. Their goal
is to reject low-probability signals early so Sprint-1's min-grade gate has
better material to work with.

Provides:
  1. compute_atr_pct(df)          — option's own ATR as % of LTP
  2. compute_adx(df, period)      — underlying trend strength (0-100)
  3. check_atr_range(cfg, atr_pct) — reject dead / chaotic premiums
  4. check_adx_trend(cfg, adx)     — reject chop-zone trades
  5. check_spread_liquidity(cfg, quote) — reject illiquid strikes
  6. compute_signal_score(...)    — transparent weighted 0-100 score + breakdown
  7. score_to_grade(score)         — A/B/C/D mapping

All checks are toggleable via config.yml `signal_quality:` section.
Fully backwards compatible via `.get(..., default)` everywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("UTBotSRChannelsScanner")


# ============================================================================
# Technical helpers
# ============================================================================

def compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """
    Returns the option's most recent ATR as % of last close.
    Higher % = more volatile premium. Sub-0.5% = dead / no movement.
    """
    if df is None or df.empty or len(df) < period + 1:
        return 0.0
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        last_atr = float(atr.iloc[-1])
        last_close = float(close.iloc[-1])
        if last_close <= 0:
            return 0.0
        return (last_atr / last_close) * 100.0
    except Exception as exc:
        log.debug("[signal_quality] compute_atr_pct error: %s", exc)
        return 0.0


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    Standard Wilder ADX on the given OHLC. Returns latest value 0-100.
    ADX > 25 = strong trend, < 20 = chop, > 40 = very strong.
    """
    if df is None or df.empty or len(df) < 2 * period:
        return 0.0
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        plus_di = 100.0 * (
            pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
        )
        minus_di = 100.0 * (
            pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
        )
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
        val = float(adx.iloc[-1])
        return val if np.isfinite(val) else 0.0
    except Exception as exc:
        log.debug("[signal_quality] compute_adx error: %s", exc)
        return 0.0


# ============================================================================
# Individual gates (each returns (ok, reason))
# ============================================================================

def check_atr_range(cfg: dict, atr_pct: float) -> Tuple[bool, str]:
    sq = cfg.get("signal_quality", {})
    if not sq.get("atr_filter_enabled", True):
        return True, ""
    lo = float(sq.get("atr_pct_min", 0.5))
    hi = float(sq.get("atr_pct_max", 8.0))
    if atr_pct < lo:
        return False, f"ATR_TOO_LOW({atr_pct:.2f}%<{lo}%)"
    if atr_pct > hi:
        return False, f"ATR_TOO_HIGH({atr_pct:.2f}%>{hi}%)"
    return True, ""


def check_adx_trend(cfg: dict, adx: float) -> Tuple[bool, str]:
    sq = cfg.get("signal_quality", {})
    if not sq.get("adx_filter_enabled", True):
        return True, ""
    min_adx = float(sq.get("adx_min", 20.0))
    if adx < min_adx:
        return False, f"ADX_CHOP({adx:.1f}<{min_adx})"
    return True, ""


def check_spread_liquidity(cfg: dict, quote: Optional[dict], ltp: float) -> Tuple[bool, str]:
    """
    quote expected keys: 'bid', 'ask', 'oi' (open interest).
    Any missing key → skip that sub-check (fail-open).
    """
    sq = cfg.get("signal_quality", {})
    if not sq.get("spread_filter_enabled", True):
        return True, ""
    if not quote or ltp <= 0:
        return True, "SPREAD_UNAVAILABLE_ALLOW"

    max_spread_pct = float(sq.get("max_spread_pct", 1.5))
    min_oi = int(sq.get("min_open_interest", 500))

    try:
        bid = float(quote.get("bid") or 0.0)
        ask = float(quote.get("ask") or 0.0)
        if bid > 0 and ask > 0 and ask >= bid:
            spread_pct = ((ask - bid) / ltp) * 100.0
            if spread_pct > max_spread_pct:
                return False, f"WIDE_SPREAD({spread_pct:.2f}%>{max_spread_pct}%)"
    except Exception:
        pass

    try:
        oi = int(quote.get("oi") or quote.get("open_interest") or 0)
        if oi and oi < min_oi:
            return False, f"LOW_OI({oi}<{min_oi})"
    except Exception:
        pass

    return True, ""


# ============================================================================
# Weighted signal scoring — transparent breakdown
# ============================================================================

# Weights sum to 100.
_WEIGHTS: Dict[str, float] = {
    "utbot":  25.0,   # UT-Bot crossover / active trend
    "sr":     15.0,   # near support (buy) / resistance (sell)
    "mtf":    20.0,   # HTF alignment
    "volume": 10.0,   # volume >= vol_sma
    "adx":    15.0,   # underlying trend strength
    "atr":    10.0,   # option ATR% within healthy range
    "spread":  5.0,   # bid-ask spread OK / OI OK
}


def compute_signal_score(
    *,
    ut_fired: bool,
    ut_active_pos: int,
    sr_pass: bool,
    mtf_pass: bool,
    vol_pass: bool,
    adx: float,
    atr_pct: float,
    spread_ok: bool,
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Deterministic 0-100 signal score with per-factor breakdown.

    Returns:
        {
          "score": 78.5,
          "grade": "A",
          "breakdown": {
             "utbot":  {"weight": 25, "earned": 25, "pass": True,  "detail": "crossover"},
             "sr":     {"weight": 15, "earned": 15, "pass": True,  "detail": "near S/R"},
             ...
          }
        }
    """
    cfg = cfg or {}
    sq = cfg.get("signal_quality", {})
    adx_min = float(sq.get("adx_min", 20.0))
    atr_lo = float(sq.get("atr_pct_min", 0.5))
    atr_hi = float(sq.get("atr_pct_max", 8.0))

    breakdown: Dict[str, Dict[str, Any]] = {}

    # UT-Bot: full weight on crossover bar, half if just active trend
    if ut_fired:
        earned, detail = _WEIGHTS["utbot"], "crossover this bar"
        ut_ok = True
    elif ut_active_pos != 0:
        earned, detail = _WEIGHTS["utbot"] * 0.5, "active trend"
        ut_ok = True
    else:
        earned, detail = 0.0, "no signal"
        ut_ok = False
    breakdown["utbot"] = {"weight": _WEIGHTS["utbot"], "earned": earned, "pass": ut_ok, "detail": detail}

    # S/R proximity
    breakdown["sr"] = {
        "weight": _WEIGHTS["sr"],
        "earned": _WEIGHTS["sr"] if sr_pass else 0.0,
        "pass": bool(sr_pass),
        "detail": "near S/R zone" if sr_pass else "no S/R confluence",
    }

    # MTF alignment
    breakdown["mtf"] = {
        "weight": _WEIGHTS["mtf"],
        "earned": _WEIGHTS["mtf"] if mtf_pass else 0.0,
        "pass": bool(mtf_pass),
        "detail": "HTF aligned" if mtf_pass else "HTF conflict",
    }

    # Volume
    breakdown["volume"] = {
        "weight": _WEIGHTS["volume"],
        "earned": _WEIGHTS["volume"] if vol_pass else 0.0,
        "pass": bool(vol_pass),
        "detail": "vol >= sma" if vol_pass else "vol below sma",
    }

    # ADX: linear scale between adx_min and adx_min + 20
    if adx <= 0:
        adx_earned, adx_detail, adx_ok = 0.0, "ADX unavailable", False
    else:
        span = 20.0
        frac = max(0.0, min(1.0, (adx - adx_min) / span)) if adx >= adx_min else 0.0
        adx_earned = _WEIGHTS["adx"] * frac
        adx_ok = adx >= adx_min
        adx_detail = f"ADX {adx:.1f}"
    breakdown["adx"] = {"weight": _WEIGHTS["adx"], "earned": adx_earned, "pass": adx_ok, "detail": adx_detail}

    # ATR%: full weight if inside band, 0 if outside
    atr_ok = atr_lo <= atr_pct <= atr_hi
    breakdown["atr"] = {
        "weight": _WEIGHTS["atr"],
        "earned": _WEIGHTS["atr"] if atr_ok else 0.0,
        "pass": atr_ok,
        "detail": f"ATR {atr_pct:.2f}%",
    }

    # Spread / liquidity
    breakdown["spread"] = {
        "weight": _WEIGHTS["spread"],
        "earned": _WEIGHTS["spread"] if spread_ok else 0.0,
        "pass": bool(spread_ok),
        "detail": "liquid" if spread_ok else "wide spread / low OI",
    }

    total = sum(v["earned"] for v in breakdown.values())
    score = round(min(99.0, max(0.0, total)), 1)
    return {"score": score, "grade": score_to_grade(score), "breakdown": breakdown}


def score_to_grade(score: float) -> str:
    if score >= 75.0:
        return "A"
    if score >= 60.0:
        return "B"
    if score >= 45.0:
        return "C"
    return "D"

