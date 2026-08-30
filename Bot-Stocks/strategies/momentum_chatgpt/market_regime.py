"""
Market Regime Classifier for Momentum-ChatGPT strategy.
Evaluates NIFTY 50 trend dynamics and NIFTY 200 market breadth.
"""

from typing import Dict, Any, List
import pandas as pd
import numpy as np


def compute_nifty_indicators(nifty50_df: pd.DataFrame) -> Dict[str, float]:
    """Compute technical indicators for NIFTY 50 index."""
    if nifty50_df is None or len(nifty50_df) < 50:
        return {}

    close = nifty50_df['close']
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if len(close) >= 200 else ema50

    # Slopes over 5 bars
    ema20_slope = (ema20.iloc[-1] - ema20.iloc[-5]) / ema20.iloc[-5] if len(ema20) >= 5 else 0.0
    ema50_slope = (ema50.iloc[-1] - ema50.iloc[-5]) / ema50.iloc[-5] if len(ema50) >= 5 else 0.0

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = float((100 - (100 / (1 + rs))).iloc[-1])

    return {
        "close": float(close.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "ema20_slope": float(ema20_slope),
        "ema50_slope": float(ema50_slope),
        "rsi14": rsi14 if not np.isnan(rsi14) else 50.0,
    }


def compute_market_breadth(stock_dfs: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """
    Calculate % of universe stocks above EMA20, EMA50, EMA200.
    """
    if not stock_dfs:
        return {"breadth_20": 50.0, "breadth_50": 50.0, "breadth_200": 50.0}

    total = 0
    above_20 = 0
    above_50 = 0
    above_200 = 0

    for symbol, df in stock_dfs.items():
        if df is None or len(df) < 20:
            continue

        c = df['close'].iloc[-1]
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1] if len(df) >= 50 else ema20
        ema200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1] if len(df) >= 200 else ema50

        total += 1
        if c > ema20:
            above_20 += 1
        if c > ema50:
            above_50 += 1
        if c > ema200:
            above_200 += 1

    if total == 0:
        return {"breadth_20": 50.0, "breadth_50": 50.0, "breadth_200": 50.0}

    return {
        "breadth_20": round((above_20 / total) * 100, 2),
        "breadth_50": round((above_50 / total) * 100, 2),
        "breadth_200": round((above_200 / total) * 100, 2),
    }


def classify_market_regime(
    nifty_metrics: Dict[str, float],
    breadth_metrics: Dict[str, float],
    cfg: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Classify current market regime into:
    - STRONG_BULL (Multiplier: 1.00)
    - SELECTIVE_BULL (Multiplier: 0.95)
    - NEUTRAL (Multiplier: 0.85)
    - BEARISH (Multiplier: 0.65)
    """
    if not nifty_metrics:
        return {
            "regime": "NEUTRAL",
            "multiplier": 0.85,
            "reason": "Missing NIFTY benchmark data",
            "nifty": nifty_metrics,
            "breadth": breadth_metrics,
        }

    c = nifty_metrics.get("close", 0)
    ema50 = nifty_metrics.get("ema50", 0)
    ema200 = nifty_metrics.get("ema200", 0)
    b50 = breadth_metrics.get("breadth_50", 50.0)

    is_strong_bull = (c > ema50) and (c > ema200) and (ema50 > ema200) and (b50 >= 60.0)
    is_selective_bull = (c > ema200) and not is_strong_bull and (b50 >= 40.0)
    is_bearish = (c < ema200) and (ema50 < ema200) and (b50 < 40.0)

    if is_strong_bull:
        regime = "STRONG_BULL"
        mult = 1.00
    elif is_selective_bull:
        regime = "SELECTIVE_BULL"
        mult = 0.95
    elif is_bearish:
        regime = "BEARISH"
        mult = 0.65
    else:
        regime = "NEUTRAL"
        mult = 0.85

    return {
        "regime": regime,
        "multiplier": mult,
        "reason": f"NIFTY Close: {c:.1f}, EMA200: {ema200:.1f}, Breadth50: {b50:.1f}%",
        "nifty": nifty_metrics,
        "breadth": breadth_metrics,
    }
