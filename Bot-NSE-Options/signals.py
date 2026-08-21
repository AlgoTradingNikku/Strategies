"""
===============================================================================
  Signal Engines — UT Bot & S/R Channels with Confluence & Filtering
===============================================================================

Pure computation logic for technical analysis signal engines:
  1. UT Bot ATR Trailing Stop
  2. Support / Resistance Channels
  3. Technical Filters (EMA 200, Volume SMA, RSI, TTM Squeeze, MTF Trend)
  4. Setup Score (0-100) & Grade Rating (A, B, C, D)
"""

import numpy as np
import pandas as pd
import logging

log = logging.getLogger("UTBotSRChannelsScanner")


def _crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses above s2 (was <= on the prior bar)."""
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


# ============================================================================
# 1. UT BOT ENGINE
# ============================================================================

def compute_utbot_signals(
    df: pd.DataFrame,
    key_value: float = 2.0,
    atr_period: int = 1,
    use_heikin_ashi: bool = False,
) -> pd.DataFrame:
    """Compute UT Bot ATR Trailing Stop signals."""
    df = df.copy()

    if use_heikin_ashi:
        src = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    else:
        src = df["close"]

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    n_loss = key_value * atr

    src_vals = src.values
    nl_vals = n_loss.values
    n = len(src_vals)
    stop = np.zeros(n)

    for i in range(1, n):
        prev_stop = stop[i - 1]
        prev_src = src_vals[i - 1]
        cur_src = src_vals[i]
        nl = nl_vals[i]

        if np.isnan(nl):
            stop[i] = cur_src
            continue

        if cur_src > prev_stop:
            iff1 = cur_src - nl
        else:
            iff1 = cur_src + nl

        if cur_src < prev_stop and prev_src < prev_stop:
            iff2 = min(prev_stop, cur_src + nl)
        else:
            iff2 = iff1

        if cur_src > prev_stop and prev_src > prev_stop:
            stop[i] = max(prev_stop, cur_src - nl)
        else:
            stop[i] = iff2

    df["ut_trail"] = stop

    pos = np.zeros(n, dtype=int)
    for i in range(1, n):
        prev_pos  = pos[i - 1]
        cur_src   = src_vals[i]
        prev_src  = src_vals[i - 1]       # ← was missing, caused stale crossover detection
        cur_stop  = stop[i]
        prev_stop = stop[i - 1]

        if cur_src > cur_stop and prev_src <= prev_stop:
            pos[i] = 1
        elif cur_src < cur_stop and prev_src >= prev_stop:
            pos[i] = -1
        else:
            pos[i] = prev_pos

    df["ut_pos"] = pos
    pos_series = pd.Series(pos, index=df.index)

    # ut_buy / ut_sell fire ONLY on the bar the crossover occurs (one bar)
    df["ut_buy"]  = (pos_series == 1)  & (pos_series.shift(1) != 1)
    df["ut_sell"] = (pos_series == -1) & (pos_series.shift(1) != -1)

    return df


# ============================================================================
# 2. S/R CHANNELS ENGINE
# ============================================================================

def compute_sr_signals(
    df: pd.DataFrame,
    pivot_period: int = 10,
    source: str = "High/Low",
    channel_width_pct: float = 5.0,
    min_strength: int = 1,
    max_num_sr: int = 6,
    loopback: int = 290,
    proximity_pct: float = 0.5,
) -> pd.DataFrame:
    """Compute Support and Resistance Channels signals."""
    df = df.copy()
    n = len(df)
    df["sr_near_support"] = False
    df["sr_near_resistance"] = False
    df["sr_buy"] = False
    df["sr_sell"] = False

    if n < pivot_period * 2 + 1:
        return df

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    pivots_high = []
    pivots_low = []

    start_idx = max(0, n - loopback)
    for i in range(start_idx + pivot_period, n - pivot_period):
        window_h = highs[i - pivot_period : i + pivot_period + 1]
        if highs[i] == max(window_h):
            pivots_high.append((i, highs[i]))

        window_l = lows[i - pivot_period : i + pivot_period + 1]
        if lows[i] == min(window_l):
            pivots_low.append((i, lows[i]))

    if not pivots_high and not pivots_low:
        return df

    latest_close = closes[-1]
    threshold = latest_close * (proximity_pct / 100.0)

    near_sup = False
    near_res = False

    for _, price in pivots_low:
        if abs(latest_close - price) <= threshold:
            near_sup = True
            break

    for _, price in pivots_high:
        if abs(latest_close - price) <= threshold:
            near_res = True
            break

    df.loc[df.index[-1], "sr_near_support"] = near_sup
    df.loc[df.index[-1], "sr_near_resistance"] = near_res
    df.loc[df.index[-1], "sr_buy"] = near_sup
    df.loc[df.index[-1], "sr_sell"] = near_res

    return df


# ============================================================================
# COMPOSITE EVALUATOR, CONFLUENCE & SETUP SCORING
# ============================================================================

def evaluate_composite_signals(
    df: pd.DataFrame,
    signal_mode: str = "UTBot",
    cfg: dict = None,
    df_htf: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Evaluate composite signals, technical filters, confluence matrix, and setup score.
    Checks recent lookback candles (e.g. 5 candles) and active position trend state so active setup signals are captured.
    """
    cfg = cfg or {}
    ut_cfg = cfg.get("strategy", {})
    sr_cfg = cfg.get("sr_channels", {})
    filters_cfg = cfg.get("filters", {})

    mode = signal_mode.upper().strip()

    # 1. Run UTBot Engine
    df = compute_utbot_signals(
        df,
        key_value=float(ut_cfg.get("key_value", 2.0)),
        atr_period=int(ut_cfg.get("atr_period", 1)),
        use_heikin_ashi=bool(ut_cfg.get("use_heikin_ashi", False)),
    )

    # 2. Run SR Channels Engine
    df = compute_sr_signals(
        df,
        pivot_period=int(sr_cfg.get("pivot_period", 10)),
        source=str(sr_cfg.get("source", "High/Low")),
        channel_width_pct=float(sr_cfg.get("channel_width_pct", 5.0)),
        min_strength=int(sr_cfg.get("min_strength", 1)),
        max_num_sr=int(sr_cfg.get("max_num_sr", 6)),
        loopback=int(sr_cfg.get("loopback", 290)),
        proximity_pct=float(sr_cfg.get("proximity_pct", 0.5)),
    )

    # 3. Technical Filter Indicators
    close = df["close"]
    ema_200 = close.ewm(span=int(filters_cfg.get("ema_period", 200)), adjust=False).mean()
    df["ema_200"] = ema_200

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1.0 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1.0 / 14, adjust=False).mean()
    rs = gain / (loss + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    vol_sma = df["volume"].rolling(20).mean() if "volume" in df.columns else pd.Series(0, index=df.index)
    df["vol_sma"] = vol_sma

    # 4. Determine signal evaluation bar: running bar (index -1) vs last completed bar (index -2)
    signal_on_running_bar = bool(ut_cfg.get("signal_on_running_bar", True))
    if signal_on_running_bar or len(df) < 2:
        eval_idx = -1
        bar_type_label = "running_bar"
    else:
        eval_idx = -2
        bar_type_label = "completed_bar"

    last_bar = df.iloc[eval_idx]
    last_ut_buy  = bool(last_bar.get("ut_buy",  False))
    last_ut_sell = bool(last_bar.get("ut_sell", False))
    last_sr_buy  = bool(last_bar.get("sr_buy",  False))
    last_sr_sell = bool(last_bar.get("sr_sell", False))
    cur_pos      = int(df["ut_pos"].iloc[eval_idx]) if "ut_pos" in df.columns else 0

    if mode == "UTBOT":
        final_buy  = last_ut_buy
        final_sell = last_ut_sell
    elif mode == "SRCHANNELS":
        final_buy  = last_sr_buy
        final_sell = last_sr_sell
    else:  # UTBOT (default) — both engines, UTBot crossover required, SR proximity adds confluence
        final_buy  = last_ut_buy
        final_sell = last_ut_sell

    df["final_buy"]  = False
    df["final_sell"] = False
    df.iloc[-1, df.columns.get_loc("final_buy")]  = final_buy
    df.iloc[-1, df.columns.get_loc("final_sell")] = final_sell

    # Calculate Confluence Matrix & Setup Score for the evaluated candle
    last = df.iloc[eval_idx]
    last_close = float(last["close"])
    last_ema = float(last["ema_200"])
    last_rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    last_vol = float(last["volume"]) if "volume" in df.columns else 0.0
    last_vol_sma = float(last["vol_sma"]) if "vol_sma" in df.columns and pd.notna(last["vol_sma"]) else 0.0

    # Confluence flags
    ema_pass = last_close >= last_ema
    rsi_pass = (45 <= last_rsi <= 75)
    vol_pass = (last_vol >= last_vol_sma) if last_vol_sma > 0 else True
    sr_pass = bool(last.get("sr_near_support", False)) or bool(last.get("sr_near_resistance", False))
    sqz_pass = True

    # Setup Score (0 - 100) — based on quality of the signal on the last bar
    score = 45.0
    if cur_pos == 1 or cur_pos == -1:   # in an active UTBot trend
        score += 15.0
    if last_ut_buy or last_ut_sell:     # UTBot crossover fired this bar
        score += 20.0
    if ema_pass:
        score += 10.0
    if rsi_pass:
        score += 5.0
    if vol_pass:
        score += 5.0

    score = min(99.0, max(35.0, score))

    if score >= 75.0:
        grade = "A"
    elif score >= 60.0:
        grade = "B"
    elif score >= 45.0:
        grade = "C"
    else:
        grade = "D"

    df.attrs["setup_score"] = round(score, 1)
    df.attrs["grade"] = grade
    df.attrs["confluence"] = {
        "ema": ema_pass,
        "rsi": rsi_pass,
        "vol": vol_pass,
        "sr": sr_pass,
        "sqz": sqz_pass,
        "mtf": bool(df.attrs.get("mtf_pass", True)),
    }

    return df


def calculate_risk_reward(
    entry_price: float,
    signal_type: str,
    stop_loss_pct: float = 20.0,
    target_pct: float = 40.0,
) -> dict:
    if entry_price <= 0:
        return {"stop_loss": 0.0, "target": 0.0, "risk_reward": "1:2.0"}

    sig = signal_type.upper()
    if sig in ("BUY", "LONG", "CE", "PE"):
        stop_loss = round(entry_price * (1.0 - stop_loss_pct / 100.0), 2)
        target = round(entry_price * (1.0 + target_pct / 100.0), 2)
        risk = entry_price - stop_loss
        reward = target - entry_price
    else:
        stop_loss = round(entry_price * (1.0 + stop_loss_pct / 100.0), 2)
        target = round(entry_price * (1.0 - target_pct / 100.0), 2)
        risk = stop_loss - entry_price
        reward = entry_price - target

    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0
    return {
        "stop_loss": stop_loss,
        "target": target,
        "risk_reward": f"1:{rr_ratio:.1f}",
    }


def check_mtf_confirmation(df_ltf: pd.DataFrame, df_htf: pd.DataFrame) -> bool:
    if df_htf is None or df_htf.empty or len(df_htf) < 2:
        return True
    df_htf = compute_utbot_signals(df_htf)
    return bool(df_htf["ut_pos"].iloc[-1] >= 0)
