"""
Stock Momentum & Setup Detection Engine for Momentum-ChatGPT.
Detects Breakouts, Pullback Retests, and Consolidation Continuations with R:R trade planning.
"""

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np


def compute_mansfield_rs(
    stock_close: pd.Series,
    bm_close: pd.Series,
    period: int = 200
) -> pd.Series:
    """Calculate Mansfield Relative Strength vs Benchmark."""
    if len(stock_close) < period or len(bm_close) < period:
        min_len = min(len(stock_close), len(bm_close))
        p = max(20, min_len - 1)
    else:
        p = period

    combined = pd.DataFrame({'stock': stock_close, 'bm': bm_close}).dropna()
    if len(combined) < p:
        return pd.Series(0.0, index=stock_close.index)

    relative_ratio = combined['stock'] / combined['bm']
    ratio_sma = relative_ratio.rolling(p).mean()
    mansfield_rs = ((relative_ratio / ratio_sma) - 1.0) * 100.0
    return mansfield_rs.reindex(stock_close.index, fill_value=0.0)


def evaluate_stock_momentum(
    df: pd.DataFrame,
    benchmark_df: Optional[pd.DataFrame] = None,
    cfg: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Evaluates liquidity, indicators, setup type, score, and trade plan for a single stock."""
    if cfg is None:
        cfg = {}

    min_history = cfg.get("min_history_bars", 300)
    min_turnover = cfg.get("min_turnover_20d_crore", 25.0) * 1e7
    min_price = cfg.get("min_price", 100.0)

    if df is None or len(df) < min_history:
        return {
            "qualified": False,
            "rejection_reason": f"INSUFFICIENT_HISTORY (bars: {len(df) if df is not None else 0} < {min_history})"
        }

    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    curr_close = float(close.iloc[-1])
    curr_high = float(high.iloc[-1])
    curr_low = float(low.iloc[-1])
    curr_vol = float(volume.iloc[-1])

    if curr_close < min_price:
        return {"qualified": False, "rejection_reason": f"PRICE_BELOW_MIN (₹{curr_close:.1f} < ₹{min_price:.1f})"}

    turnover_20d = (close * volume).rolling(20).mean().iloc[-1]
    if turnover_20d < min_turnover:
        return {"qualified": False, "rejection_reason": f"LOW_TURNOVER (₹{turnover_20d/1e7:.2f}Cr < ₹{min_turnover/1e7:.1f}Cr)"}

    # Indicators
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).fillna(50)
    curr_rsi = float(rsi.iloc[-1])

    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    curr_atr = float(atr14.iloc[-1])

    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / atr14)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / atr14)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(14).mean().fillna(20)
    curr_adx = float(adx.iloc[-1])

    vol_sma20 = volume.rolling(20).mean().iloc[-1]
    vol_surge_ratio = curr_vol / vol_sma20 if vol_sma20 > 0 else 1.0

    mansfield_rs_val = 0.0
    if benchmark_df is not None and len(benchmark_df) >= 50:
        m_rs_series = compute_mansfield_rs(close, benchmark_df['close'], period=200)
        mansfield_rs_val = float(m_rs_series.iloc[-1])

    # Setup Detection
    setup_type = None
    high_20d = high.iloc[-21:-1].max() if len(high) >= 21 else high.max()
    high_52w = high.iloc[-250:-1].max() if len(high) >= 250 else high.max()

    is_breakout = (curr_close >= high_20d) or (curr_close >= high_52w * 0.98 and vol_surge_ratio >= 1.3)
    near_ema20 = abs(curr_close - ema20.iloc[-1]) / ema20.iloc[-1] <= 0.02
    is_pullback = (curr_close > ema50.iloc[-1]) and near_ema20 and (curr_close > df['open'].iloc[-1])

    bb_width = (ema20 + 2 * df['close'].rolling(20).std()) - (ema20 - 2 * df['close'].rolling(20).std())
    is_consolidation = (bb_width.iloc[-1] < bb_width.iloc[-10]) and (curr_close > ema20.iloc[-1])

    if is_breakout:
        setup_type = "BREAKOUT"
    elif is_pullback:
        setup_type = "PULLBACK_RETEST"
    elif is_consolidation:
        setup_type = "CONSOLIDATION_CONTINUATION"
    else:
        if curr_close > ema20.iloc[-1] > ema50.iloc[-1]:
            setup_type = "TREND_CONTINUATION"

    if setup_type is None:
        return {"qualified": False, "rejection_reason": "NO_VALID_TECHNICAL_SETUP"}

    # Scoring (0-100)
    trend_pts = 25.0 if (curr_close > ema10.iloc[-1] > ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]) else (18.0 if curr_close > ema20.iloc[-1] > ema50.iloc[-1] else 10.0)
    rsi_pts = 15.0 if (55 <= curr_rsi <= 75) else (10.0 if (45 <= curr_rsi <= 80) else 0.0)
    adx_pts = 10.0 if curr_adx >= 25.0 else (5.0 if curr_adx >= 20.0 else 0.0)
    rs_pts = 20.0 if mansfield_rs_val > 5.0 else (12.0 if mansfield_rs_val > 0.0 else 0.0)
    vol_pts = 15.0 if vol_surge_ratio >= 2.0 else (10.0 if vol_surge_ratio >= 1.5 else 5.0)
    setup_pts = 15.0 if setup_type in ["BREAKOUT", "PULLBACK_RETEST"] else 10.0

    raw_score = trend_pts + (rsi_pts + adx_pts) + rs_pts + vol_pts + setup_pts

    # Trade Plan
    entry_price = curr_close
    swing_low = low.iloc[-10:].min()
    ema_stop = float(ema20.iloc[-1]) - (1.0 * curr_atr)
    stop_loss = max(float(swing_low), ema_stop)

    if stop_loss >= entry_price:
        stop_loss = entry_price - (1.5 * curr_atr)

    risk_per_share = entry_price - stop_loss
    min_rr = cfg.get("min_rr_ratio", 1.5)
    target_price = entry_price + (min_rr * risk_per_share)
    rr_ratio = (target_price - entry_price) / risk_per_share if risk_per_share > 0 else 0.0

    return {
        "qualified": True,
        "raw_score": round(raw_score, 2),
        "setup_type": setup_type,
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "target_price": round(target_price, 2),
        "rr_ratio": round(rr_ratio, 2),
        "rsi": round(curr_rsi, 1),
        "adx": round(curr_adx, 1),
        "vol_surge": round(vol_surge_ratio, 2),
        "mansfield_rs": round(mansfield_rs_val, 2),
        "turnover_cr": round(turnover_20d / 1e7, 2),
    }
