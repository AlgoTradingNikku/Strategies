"""
quant/technicals.py
====================
Technical indicator computation for the underlying spot index.
Reuses existing signals.py computations where possible.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import TechnicalData
from ai.schemas.settings import PlatformSettings

log = logging.getLogger("UTBotSRChannelsScanner")


def compute_technicals(
    df: Optional[pd.DataFrame],
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
    timeframe_label: str = "5m",
) -> TechnicalData:
    """
    Compute technical indicators from spot OHLCV DataFrame.
    Returns TechnicalData with all fields. Fail-open — never raises.
    """
    if settings and not settings.quant_technicals:
        log.debug("[Tech] quant_technicals toggle is OFF")
        return TechnicalData()

    if df is None or df.empty or len(df) < 20:
        log.debug("[Tech] Insufficient data for technicals (%s rows)", len(df) if df is not None else 0)
        return TechnicalData()

    try:
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        spot_ltp = float(close.iloc[-1])

        # EMAs
        ema9 = float(_ema(close, 9).iloc[-1])
        ema20 = float(_ema(close, 20).iloc[-1])
        ema50 = float(_ema(close, 50).iloc[-1]) if len(close) >= 50 else ema20
        ema200 = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else ema50

        # RSI 14
        rsi14 = float(_rsi(close, 14).iloc[-1])

        # ATR 14
        atr14 = float(_atr(high, low, close, 14).iloc[-1])

        # VWAP (intraday — typical price × volume / cumulative volume)
        vwap = _compute_vwap(df)

        # Day high/low/prev_close
        day_high = float(high.max())
        day_low = float(low.min())
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else spot_ltp
        gap_pct = round(((close.iloc[0] - prev_close) / prev_close * 100), 3) if prev_close > 0 else 0.0

        # Price vs VWAP / EMAs
        price_vs_vwap = "ABOVE" if spot_ltp > vwap else ("BELOW" if spot_ltp < vwap else "AT")
        price_vs_ema20 = "ABOVE" if spot_ltp > ema20 else "BELOW"
        price_vs_ema50 = "ABOVE" if spot_ltp > ema50 else "BELOW"

        # Trend for this timeframe
        trend = _classify_trend(spot_ltp, ema9, ema20, ema50, rsi14)

        return TechnicalData(
            spot_ltp=round(spot_ltp, 2),
            vwap=round(vwap, 2),
            ema9=round(ema9, 2),
            ema20=round(ema20, 2),
            ema50=round(ema50, 2),
            ema200=round(ema200, 2),
            rsi14=round(rsi14, 2),
            atr14=round(atr14, 2),
            day_high=round(day_high, 2),
            day_low=round(day_low, 2),
            prev_close=round(prev_close, 2),
            gap_pct=gap_pct,
            price_vs_vwap=price_vs_vwap,
            price_vs_ema20=price_vs_ema20,
            price_vs_ema50=price_vs_ema50,
            trend_5m=trend if timeframe_label == "5m" else "NEUTRAL",
            trend_15m=trend if timeframe_label == "15m" else "NEUTRAL",
            trend_1h=trend if timeframe_label == "1h" else "NEUTRAL",
            trend_day=trend if timeframe_label == "1d" else "NEUTRAL",
        )

    except Exception as exc:
        log.warning("[Tech] compute_technicals failed: %s", exc)
        return TechnicalData()


def compute_multi_tf_technicals(
    histories: dict[str, Optional[pd.DataFrame]],
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> TechnicalData:
    """
    Compute technicals across multiple timeframes and merge into one TechnicalData.
    histories: dict of timeframe_label -> DataFrame
    """
    result = TechnicalData()
    tf_map = {"5m": "trend_5m", "15m": "trend_15m", "1h": "trend_1h", "1d": "trend_day"}

    for tf_label, df in histories.items():
        td = compute_technicals(df, cfg, settings, timeframe_label=tf_label)
        attr = tf_map.get(tf_label)
        if attr:
            setattr(result, attr, getattr(td, attr))

    # Use 5m data for primary indicators
    if "5m" in histories and histories["5m"] is not None:
        primary = compute_technicals(histories["5m"], cfg, settings, "5m")
        result.spot_ltp = primary.spot_ltp
        result.vwap = primary.vwap
        result.ema9 = primary.ema9
        result.ema20 = primary.ema20
        result.ema50 = primary.ema50
        result.ema200 = primary.ema200
        result.rsi14 = primary.rsi14
        result.atr14 = primary.atr14
        result.day_high = primary.day_high
        result.day_low = primary.day_low
        result.prev_close = primary.prev_close
        result.gap_pct = primary.gap_pct
        result.price_vs_vwap = primary.price_vs_vwap
        result.price_vs_ema20 = primary.price_vs_ema20
        result.price_vs_ema50 = primary.price_vs_ema50

    return result


# ── indicator helpers ─────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs)).fillna(50)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _compute_vwap(df: pd.DataFrame) -> float:
    try:
        if "volume" not in df.columns or df["volume"].sum() == 0:
            return float(df["close"].iloc[-1])
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (typical * df["volume"]).sum() / df["volume"].sum()
        return float(vwap)
    except Exception:
        return float(df["close"].iloc[-1])


def _classify_trend(ltp: float, ema9: float, ema20: float, ema50: float, rsi: float) -> str:
    """Simple trend classification from EMA positions and RSI."""
    bullish_score = sum([
        ltp > ema9,
        ltp > ema20,
        ltp > ema50,
        ema9 > ema20,
        rsi > 55,
    ])
    bearish_score = sum([
        ltp < ema9,
        ltp < ema20,
        ltp < ema50,
        ema9 < ema20,
        rsi < 45,
    ])
    if bullish_score >= 4:
        return "BULLISH"
    elif bearish_score >= 4:
        return "BEARISH"
    return "NEUTRAL"
