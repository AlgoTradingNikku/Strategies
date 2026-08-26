"""
regime.py
=========
Market-regime classifier for the Bot-Stocks scanner.

Given a DataFrame of NIFTY (or any broad-market proxy) OHLC bars, classify the
current regime into one of four states:

    "trending_up"   — Strong uptrend. Trend-following strategies (UT Bot) shine.
    "trending_down" — Strong downtrend. UT Bot on the short side works.
    "chop"          — Low ADX, price oscillating. Mean-reversion (S/R) works
                      here; trend-following bleeds via whipsaws.
    "high_vol_chop" — Directionless AND volatile (event days, RBI policy, etc.).
                      Safest to trade nothing, or size down aggressively.

The classifier is deliberately simple and stateless — it takes a DataFrame
and returns a string. Higher-level policy (e.g. "disable UT Bot in chop") is
enforced by the caller, not this module.

Signals used
------------
- **ADX(14)** on the input frame — trend strength.
- **+DI / −DI** — trend direction when ADX is strong.
- **Realised volatility percentile** (rolling std of log returns over
  ``vol_lookback`` bars, ranked over ``vol_percentile_window`` bars) — used
  to detect the "high-vol" chop case.

Config
------
Reads ``config['regime']`` block. All keys are optional; the defaults below
are tuned for 5-minute NIFTY bars but work reasonably for 15m/1h too.

    regime:
      adx_trend_threshold: 22       # ADX >= this is treated as a trend
      adx_strong_threshold: 30      # ADX >= this is a strong trend
      vol_lookback: 20              # bars over which realised vol is computed
      vol_percentile_window: 200    # bars over which vol is ranked
      vol_high_percentile: 0.80     # >= this percentile -> "high vol"
      min_bars: 50                  # below this many bars -> "unknown"
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from signals import compute_adx

log = logging.getLogger("UTBotSRChannelsScanner")


REGIMES = ("trending_up", "trending_down", "chop", "high_vol_chop", "unknown")


def classify_regime(
    nifty_df: Optional[pd.DataFrame],
    config: Optional[dict] = None,
) -> dict:
    """Return a dict summarising the current market regime.

    Parameters
    ----------
    nifty_df : pd.DataFrame or None
        OHLCV bars for a broad-market proxy (typically ``^NSEI``). Must contain
        columns 'high', 'low', 'close'. When None or too short, the function
        returns ``regime="unknown"`` — callers must handle this explicitly
        rather than assume any specific regime.
    config : dict, optional
        Bot-Stocks configuration dict. Reads the ``regime`` sub-block.

    Returns
    -------
    dict
        {
            "regime":    str,          # one of REGIMES
            "adx":       float | None,
            "plus_di":   float | None,
            "minus_di":  float | None,
            "vol_pct":   float | None, # realised-vol percentile [0.0, 1.0]
        }
    """
    cfg = (config or {}).get("regime", {}) if config else {}

    adx_trend        = float(cfg.get("adx_trend_threshold", 22.0))
    _adx_strong      = float(cfg.get("adx_strong_threshold", 30.0))  # reserved
    vol_lookback     = int(cfg.get("vol_lookback", 20))
    vol_perc_window  = int(cfg.get("vol_percentile_window", 200))
    vol_high_perc    = float(cfg.get("vol_high_percentile", 0.80))
    min_bars         = int(cfg.get("min_bars", 50))

    result = {
        "regime":   "unknown",
        "adx":      None,
        "plus_di":  None,
        "minus_di": None,
        "vol_pct":  None,
    }

    if nifty_df is None or len(nifty_df) < min_bars:
        return result

    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(nifty_df.columns):
        log.debug("classify_regime: input missing required columns %s", required_cols)
        return result

    # ---- ADX + directional indices --------------------------------------
    try:
        adx_series, plus_di, minus_di = compute_adx(nifty_df, period=14)
        adx_val      = float(adx_series.iloc[-1])
        plus_di_val  = float(plus_di.iloc[-1])
        minus_di_val = float(minus_di.iloc[-1])
    except Exception as exc:
        log.debug("classify_regime: ADX computation failed: %s", exc)
        return result

    if np.isnan(adx_val) or np.isnan(plus_di_val) or np.isnan(minus_di_val):
        return result

    # ---- Realised-volatility percentile ---------------------------------
    # Rank the *current* rolling-std against its own history so the threshold
    # adapts to each timeframe automatically (5m vol scales differently from
    # 15m vol; percentile ranking is scale-free).
    try:
        log_ret = np.log(nifty_df["close"] / nifty_df["close"].shift(1))
        rvol    = log_ret.rolling(vol_lookback).std()
        rvol_current = float(rvol.iloc[-1])

        window_slice = rvol.tail(vol_perc_window).dropna()
        if len(window_slice) < 2 or np.isnan(rvol_current):
            vol_pct = None
        else:
            vol_pct = float((window_slice < rvol_current).sum()) / float(len(window_slice))
    except Exception as exc:
        log.debug("classify_regime: volatility computation failed: %s", exc)
        vol_pct = None

    # ---- Classify --------------------------------------------------------
    if adx_val >= adx_trend:
        regime = "trending_up" if plus_di_val >= minus_di_val else "trending_down"
    else:
        if vol_pct is not None and vol_pct >= vol_high_perc:
            regime = "high_vol_chop"
        else:
            regime = "chop"

    result.update({
        "regime":   regime,
        "adx":      round(adx_val, 2),
        "plus_di":  round(plus_di_val, 2),
        "minus_di": round(minus_di_val, 2),
        "vol_pct":  round(vol_pct, 3) if vol_pct is not None else None,
    })
    return result


# ---------------------------------------------------------------------------
# Convenience: policy helper (Sprint 2 will use this at scan time)
# ---------------------------------------------------------------------------

def should_enable_engine(regime: str, engine: str, config: Optional[dict] = None) -> bool:
    """Decide whether to enable a signal engine given the current regime.

    Default policy (override per-regime in ``config['regime']['policy']``):

        - trending_up / trending_down  -> UT Bot ON,  S/R ON
        - chop                         -> UT Bot OFF, S/R ON
        - high_vol_chop                -> BOTH OFF (sit out event days)
        - unknown                      -> both ON (behave as if no regime info)

    Parameters
    ----------
    regime : str   — one of REGIMES
    engine : str   — 'utbot' or 'sr'
    config : dict  — optional; can override the default policy.

    Returns
    -------
    bool — True if this engine should be enabled in this regime.
    """
    default_policy = {
        "trending_up":    {"utbot": True,  "sr": True},
        "trending_down":  {"utbot": True,  "sr": True},
        "chop":           {"utbot": False, "sr": True},
        "high_vol_chop":  {"utbot": False, "sr": False},
        "unknown":        {"utbot": True,  "sr": True},
    }

    policy = {k: dict(v) for k, v in default_policy.items()}
    override = ((config or {}).get("regime", {}) or {}).get("policy")
    if isinstance(override, dict):
        for k, v in override.items():
            if k in policy and isinstance(v, dict):
                policy[k].update(v)

    engine_key = engine.lower()
    if engine_key not in ("utbot", "sr"):
        raise ValueError(f"Unknown engine: {engine!r} (expected 'utbot' or 'sr')")

    return bool(policy.get(regime, policy["unknown"]).get(engine_key, True))

