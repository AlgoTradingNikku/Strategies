"""
===============================================================================
  Signal Engines — UT Bot, S/R Channels, LinReg Candles
===============================================================================

Pure computation logic for three technical analysis signal engines, plus a
composite evaluator that combines them based on configuration.

Each engine takes a pandas DataFrame with OHLCV columns and returns the same
DataFrame with signal columns appended.

Ported from the Pine Script indicator:
    PineScript-UTBot+SR Channels+LinRegCandles
===============================================================================
"""

import numpy as np
import pandas as pd
import logging

log = logging.getLogger("UTBotSRLinRegScanner")


# ============================================================================
# UTILITY
# ============================================================================

def _crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses above s2."""
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def _crossunder(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses below s2."""
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))


# ============================================================================
# 1. UT BOT ENGINE
# ============================================================================

def compute_utbot_signals(
    df: pd.DataFrame,
    key_value: float = 2.0,
    atr_period: int = 1,
    use_heikin_ashi: bool = False,
) -> pd.DataFrame:
    """
    Compute UT Bot ATR Trailing Stop signals.

    Mirrors the Pine Script UT Bot logic exactly:
      - Calculates ATR-based trailing stop (xATRTrailingStop)
      - Detects crossovers for buy/sell entries

    Appends columns
    ----------------
    ut_trail : float   — ATR trailing stop value
    ut_pos   : int     — position state (+1 long, -1 short, 0 neutral)
    ut_buy   : bool    — buy signal (price crosses above trailing stop)
    ut_sell  : bool    — sell signal (price crosses below trailing stop)
    """
    df = df.copy()

    # ---- Source price -------------------------------------------------------
    if use_heikin_ashi:
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        src = ha_close
    else:
        src = df["close"]

    # ---- True Range / ATR (Wilder's RMA) ------------------------------------
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    n_loss = key_value * atr

    # ---- xATRTrailingStop (iterative — matches Pine bar-by-bar logic) -------
    src_vals = src.values
    nl_vals = n_loss.values
    n = len(src_vals)
    stop = np.zeros(n)

    for i in range(1, n):
        prev_stop = stop[i - 1]
        prev_src = src_vals[i - 1]
        cur_src = src_vals[i]
        cur_nl = nl_vals[i]

        if np.isnan(cur_nl):
            stop[i] = prev_stop
            continue

        if cur_src > prev_stop and prev_src > prev_stop:
            stop[i] = max(prev_stop, cur_src - cur_nl)
        elif cur_src < prev_stop and prev_src < prev_stop:
            stop[i] = min(prev_stop, cur_src + cur_nl)
        elif cur_src > prev_stop:
            stop[i] = cur_src - cur_nl
        else:
            stop[i] = cur_src + cur_nl

    xATR = pd.Series(stop, index=df.index)

    # ---- Position state -----------------------------------------------------
    pos = np.zeros(n, dtype=int)
    for i in range(1, n):
        if src_vals[i - 1] < stop[i - 1] and src_vals[i] > stop[i]:
            pos[i] = 1
        elif src_vals[i - 1] > stop[i - 1] and src_vals[i] < stop[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]

    # ---- Crossover signals --------------------------------------------------
    ema = src  # EMA(1) ≡ source
    above = _crossover(ema, xATR)
    below = _crossover(xATR, ema)

    df["ut_trail"] = xATR
    df["ut_pos"] = pd.Series(pos, index=df.index)
    df["ut_buy"] = (src > xATR) & above
    df["ut_sell"] = (src < xATR) & below

    return df


# ============================================================================
# 2. S/R CHANNEL ENGINE
# ============================================================================

def _find_pivots(high_src: pd.Series, low_src: pd.Series, prd: int):
    """
    Find pivot highs and lows — equivalent to Pine's ta.pivothigh / ta.pivotlow.

    A pivot high at bar i requires high_src[i] to be the maximum of the window
    [i-prd, i+prd]. Similarly for pivot lows.

    Returns
    -------
    pivot_highs : list of (bar_index, value)
    pivot_lows  : list of (bar_index, value)
    """
    n = len(high_src)
    h_vals = high_src.values
    l_vals = low_src.values

    pivot_highs = []
    pivot_lows = []

    for i in range(prd, n - prd):
        # Pivot high: bar i is highest in window [i-prd, i+prd]
        h_val = h_vals[i]
        window_h = h_vals[i - prd : i + prd + 1]
        if h_val >= window_h.max():
            pivot_highs.append((i, float(h_val)))

        # Pivot low: bar i is lowest in window [i-prd, i+prd]
        l_val = l_vals[i]
        window_l = l_vals[i - prd : i + prd + 1]
        if l_val <= window_l.min():
            pivot_lows.append((i, float(l_val)))

    return pivot_highs, pivot_lows


def _cluster_sr_zones(
    pivot_values: list[float],
    cwidth: float,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    loopback_end_idx: int,
    loopback: int,
    min_strength: int,
    max_num_sr: int,
) -> list[tuple[float, float]]:
    """
    Cluster pivot values into S/R zones, score them, and return the top zones.

    Faithfully replicates the Pine Script's get_sr_vals + greedy selection
    algorithm from the SR Channel indicator.

    Parameters
    ----------
    pivot_values   : Pivot price values, ordered most-recent first.
    cwidth         : Maximum channel width (price units).
    high_arr       : Full high price array.
    low_arr        : Full low price array.
    loopback_end_idx : Bar index of the last bar.
    loopback       : Bars to look back for touch counting.
    min_strength   : Minimum zone strength threshold (multiplied by 20).
    max_num_sr     : Maximum number of zones to return.

    Returns
    -------
    list of (zone_hi, zone_lo) tuples, sorted by strength descending.
    """
    num_pivots = len(pivot_values)
    if num_pivots == 0:
        return []

    # ---- Step 1: For each pivot, expand a zone and count pivots inside ------
    # This mirrors Pine's get_sr_vals(ind) function
    raw_zones = []  # [(strength, hi, lo), ...]

    for seed_idx in range(num_pivots):
        lo = pivot_values[seed_idx]
        hi = lo
        numpp = 0

        for y in range(num_pivots):
            cpp = pivot_values[y]
            wdth = hi - cpp if cpp <= hi else cpp - lo
            if wdth <= cwidth:
                if cpp <= hi:
                    lo = min(lo, cpp)
                else:
                    hi = max(hi, cpp)
                numpp += 20

        raw_zones.append([numpp, hi, lo])

    # ---- Step 2: Add touch count — bars whose high or low falls in zone -----
    start_idx = max(0, loopback_end_idx - loopback)
    end_idx = min(loopback_end_idx + 1, len(high_arr))

    for z in raw_zones:
        hi, lo = z[1], z[2]
        touches = 0
        for bar_i in range(start_idx, end_idx):
            h = high_arr[bar_i]
            l = low_arr[bar_i]
            if (lo <= h <= hi) or (lo <= l <= hi):
                touches += 1
        z[0] += touches

    # ---- Step 3: Greedy selection of top-N non-overlapping zones ------------
    # Pick strongest zone, mask all overlapping zones, repeat.
    selected = []
    strength_threshold = min_strength * 20

    for _ in range(min(max_num_sr, num_pivots)):
        best_idx = -1
        best_strength = -1

        for i, z in enumerate(raw_zones):
            if z[0] > best_strength and z[0] >= strength_threshold:
                best_strength = z[0]
                best_idx = i

        if best_idx < 0:
            break

        sel_hi = raw_zones[best_idx][1]
        sel_lo = raw_zones[best_idx][2]
        selected.append((sel_hi, sel_lo, best_strength))

        # Mask overlapping zones
        for z in raw_zones:
            z_hi, z_lo = z[1], z[2]
            if (sel_lo <= z_hi <= sel_hi) or (sel_lo <= z_lo <= sel_hi):
                z[0] = -1

    # Sort selected by strength descending
    selected.sort(key=lambda x: -x[2])

    return [(hi, lo) for hi, lo, _ in selected]


def compute_sr_signals(
    df: pd.DataFrame,
    pivot_period: int = 10,
    source: str = "High/Low",
    channel_width_pct: int = 5,
    min_strength: int = 1,
    max_num_sr: int = 6,
    loopback: int = 290,
    proximity_pct: float = 0.5,
) -> pd.DataFrame:
    """
    Compute Support/Resistance channel signals.

    Port of the Pine Script SR Channel logic:
      1. Detects pivot highs/lows
      2. Clusters nearby pivots into zones
      3. Scores zones by pivot count + bar-touch count
      4. Selects top-N strongest zones
      5. Determines if price is near support (buy) or resistance (sell)

    Zone classification relative to current close:
      - Zone entirely below close → Support zone
      - Zone entirely above close → Resistance zone
      - Close inside zone → zone provides both support (from below) and
        resistance (from above)

    Appends columns
    ----------------
    sr_buy  : bool — price is inside or within proximity of a support zone
    sr_sell : bool — price is inside or within proximity of a resistance zone

    Also stores zones as df.attrs["sr_zones"].
    """
    df = df.copy()
    n = len(df)

    if n < pivot_period * 2 + 1:
        df["sr_buy"] = False
        df["sr_sell"] = False
        df.attrs["sr_zones"] = []
        return df

    # ---- Source for pivot detection -----------------------------------------
    if source == "High/Low":
        high_src = df["high"]
        low_src = df["low"]
    else:
        high_src = df[["close", "open"]].max(axis=1)
        low_src = df[["close", "open"]].min(axis=1)

    # ---- Find all pivots ----------------------------------------------------
    pivot_highs, pivot_lows = _find_pivots(high_src, low_src, pivot_period)

    # ---- Collect pivots within loopback window, most-recent first -----------
    last_bar_idx = n - 1
    cutoff_idx = last_bar_idx - loopback

    all_pivots = []
    for bar_idx, val in pivot_highs:
        if bar_idx > cutoff_idx:
            all_pivots.append((bar_idx, val))
    for bar_idx, val in pivot_lows:
        if bar_idx > cutoff_idx:
            all_pivots.append((bar_idx, val))

    # Sort most-recent first (matches Pine's array.unshift ordering)
    all_pivots.sort(key=lambda x: -x[0])
    pivot_values = [p[1] for p in all_pivots]

    # ---- Channel width (based on 300-bar range) ----------------------------
    window_size = min(300, n)
    high_300 = df["high"].iloc[-window_size:].max()
    low_300 = df["low"].iloc[-window_size:].min()
    cwidth = (high_300 - low_300) * channel_width_pct / 100

    if cwidth <= 0:
        df["sr_buy"] = False
        df["sr_sell"] = False
        df.attrs["sr_zones"] = []
        return df

    # ---- Compute zones ------------------------------------------------------
    zones = _cluster_sr_zones(
        pivot_values=pivot_values,
        cwidth=cwidth,
        high_arr=df["high"].values,
        low_arr=df["low"].values,
        loopback_end_idx=last_bar_idx,
        loopback=loopback,
        min_strength=min_strength,
        max_num_sr=max_num_sr,
    )

    # ---- Evaluate support/resistance per bar (vectorized) -------------------
    sr_buy = pd.Series(False, index=df.index)
    sr_sell = pd.Series(False, index=df.index)
    close_vals = df["close"]

    for zone_hi, zone_lo in zones:
        prox = close_vals * proximity_pct / 100

        # Price is inside the zone
        inside = (close_vals >= zone_lo) & (close_vals <= zone_hi)
        sr_buy = sr_buy | inside
        sr_sell = sr_sell | inside

        # Zone is below price → support; check if within proximity above zone_hi
        below_near = (zone_hi < close_vals) & ((close_vals - zone_hi) <= prox)
        sr_buy = sr_buy | below_near

        # Zone is above price → resistance; check if within proximity below zone_lo
        above_near = (zone_lo > close_vals) & ((zone_lo - close_vals) <= prox)
        sr_sell = sr_sell | above_near

    df["sr_buy"] = sr_buy
    df["sr_sell"] = sr_sell
    df.attrs["sr_zones"] = zones

    return df


# ============================================================================
# 3. LINREG CANDLE ENGINE
# ============================================================================

def _rolling_linreg(series: pd.Series, length: int) -> pd.Series:
    """
    Rolling linear regression value — equivalent to Pine Script's
    ta.linreg(source, length, 0).

    Fits a least-squares line to the last `length` values and returns
    the fitted value at the end of the window (current bar).
    """
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _fit(y):
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / x_var
        intercept = y_mean - slope * x_mean
        return slope * (length - 1) + intercept

    return series.rolling(length, min_periods=length).apply(_fit, raw=True)


def compute_linreg_signals(
    df: pd.DataFrame,
    length: int = 11,
    signal_length: int = 7,
    use_sma: bool = True,
    proximity_pct: float = 0.5,
    compare_source: str = "lr_close",
) -> pd.DataFrame:
    """
    Compute Linear Regression Candle signals.

    Port of the Pine Script LinReg Candle logic:
      - Applies linear regression to OHLC prices to smooth noise
      - Computes a signal line (SMA or EMA of LinReg close)
      - Buy/Sell based on compare_source vs the signal line

    Parameters
    ----------
    compare_source : str
        "lr_close" — compare LinReg-smoothed close vs signal line (matches TradingView visually).
        "close"    — compare raw closing price vs signal line (more sensitive / faster signals).

    Appends columns
    ----------------
    lr_close  : float — linear regression smoothed close
    lr_signal : float — signal line (SMA/EMA of lr_close)
    lr_buy    : bool  — source crosses above or is above and nearby signal line
    lr_sell   : bool  — source crosses below or is below and nearby signal line
    """
    df = df.copy()

    # ---- LinReg smoothed close ----------------------------------------------
    lr_close = _rolling_linreg(df["close"], length)

    # ---- Signal line --------------------------------------------------------
    if use_sma:
        lr_signal = lr_close.rolling(signal_length, min_periods=1).mean()
    else:
        lr_signal = lr_close.ewm(span=signal_length, adjust=False).mean()

    # ---- Select comparison source -------------------------------------------
    # "lr_close" matches TradingView's LinReg candle visual (smoothed close vs signal line)
    # "close"    compares raw price vs signal line (faster, more sensitive)
    if compare_source == "lr_close":
        src = lr_close
    else:
        src = df["close"]

    # ---- Signals: src vs signal line ----------------------------------------
    prox = src * proximity_pct / 100

    crossover  = _crossover(src, lr_signal)
    above_near = (src > lr_signal) & ((src - lr_signal) <= prox)

    crossunder = _crossunder(src, lr_signal)
    below_near = (src < lr_signal) & ((lr_signal - src) <= prox)

    df["lr_close"] = lr_close
    df["lr_signal"] = lr_signal
    df["lr_buy"] = crossover | above_near
    df["lr_sell"] = crossunder | below_near

    return df


def safe_eval_boolean(expr: str, variables: dict) -> bool:
    """
    Safely evaluate a boolean expression with allowed variables and operators.
    E.g. "(utbot and sr) or (utbot and linreg)"
    """
    import re
    normalized = expr.lower()

    # Normalize variations using regex boundaries where needed
    normalized = re.sub(r"\blinreg candles\b", "linreg", normalized)
    normalized = re.sub(r"\blinreg\b", "linreg", normalized)
    normalized = re.sub(r"\blinreg\b", "linreg", normalized)
    normalized = re.sub(r"\blr\b", "linreg", normalized)

    normalized = re.sub(r"\bs/r channels\b", "sr", normalized)
    normalized = normalized.replace("s/r", "sr")  # safe since '/' is unique
    normalized = re.sub(r"\bsr_channels\b", "sr", normalized)

    normalized = re.sub(r"\but bot\b", "utbot", normalized)
    normalized = re.sub(r"\but\b", "utbot", normalized)

    # Only allow parentheses, spaces, logical operators (and, or, not, true, false), and mapped keys
    token_pattern = re.compile(r"[a-z0-9_]+|[\(\)]")
    tokens = token_pattern.findall(normalized)

    valid_keys = {"utbot", "sr", "linreg", "and", "or", "not", "true", "false", "(", ")"}
    for token in tokens:
        if token not in valid_keys:
            raise ValueError(f"Invalid token {token!r} in custom logic expression")

    # Map the clean variable names to values
    context = {
        "utbot": bool(variables.get("utbot")),
        "sr": bool(variables.get("sr")),
        "linreg": bool(variables.get("linreg")),
        "__builtins__": {}
    }

    return bool(eval(normalized, context))


# ============================================================================
# 4. COMPOSITE SIGNAL EVALUATOR
# ============================================================================

def evaluate_composite_signals(
    df: pd.DataFrame,
    config: dict,
    lookback_candles: int = 3,
) -> dict:
    """
    Evaluate composite buy/sell signals based on enabled conditions and mode.

    Logic
    -----
    - UT Bot:  checked across the last `lookback_candles` candles
    - S/R:     checked on the last (current) candle only
    - LinReg:  checked on the last (current) candle only

    Mode
    ----
    - AND: all enabled conditions must be true
    - OR:  any one enabled condition triggers

    Returns
    -------
    dict with keys:
        buy            : bool
        sell           : bool
        triggered_buy  : list of condition name strings
        triggered_sell : list of condition name strings
        details        : dict with indicator metadata
    """
    strat = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    lr_cfg = config.get("linreg", {})
    mode = config.get("signal_mode", "AND").upper()

    ut_enabled = strat.get("ut_enabled", True)
    sr_enabled = sr_cfg.get("enabled", True)
    lr_enabled = lr_cfg.get("enabled", True)

    # ---- UT Bot: check last N candles for any buy/sell ----------------------
    ut_buy = False
    ut_sell = False
    if ut_enabled and "ut_buy" in df.columns:
        tail = df.tail(lookback_candles)
        ut_buy = bool(tail["ut_buy"].any())
        ut_sell = bool(tail["ut_sell"].any())

    # ---- S/R Channels: check current (last) candle only --------------------
    sr_buy = False
    sr_sell = False
    if sr_enabled and "sr_buy" in df.columns and len(df) > 0:
        sr_buy = bool(df["sr_buy"].iloc[-1])
        sr_sell = bool(df["sr_sell"].iloc[-1])

    # ---- LinReg: check current (last) candle only --------------------------
    lr_buy = False
    lr_sell = False
    if lr_enabled and "lr_buy" in df.columns and len(df) > 0:
        lr_buy = bool(df["lr_buy"].iloc[-1])
        lr_sell = bool(df["lr_sell"].iloc[-1])

    # ---- Collect enabled conditions ----------------------------------------
    buy_conditions = []
    sell_conditions = []
    triggered_buy = []
    triggered_sell = []

    if ut_enabled:
        buy_conditions.append(ut_buy)
        sell_conditions.append(ut_sell)
        if ut_buy:
            triggered_buy.append("UT Bot")
        if ut_sell:
            triggered_sell.append("UT Bot")

    if sr_enabled:
        buy_conditions.append(sr_buy)
        sell_conditions.append(sr_sell)
        if sr_buy:
            triggered_buy.append("S/R Support")
        if sr_sell:
            triggered_sell.append("S/R Resistance")

    if lr_enabled:
        buy_conditions.append(lr_buy)
        sell_conditions.append(lr_sell)
        if lr_buy:
            triggered_buy.append("LinReg ↑")
        if lr_sell:
            triggered_sell.append("LinReg ↓")

    # ---- No conditions enabled → no signal ---------------------------------
    if not buy_conditions:
        return {
            "buy": False,
            "sell": False,
            "triggered_buy": [],
            "triggered_sell": [],
            "details": {},
        }

    # ---- Combine based on mode ---------------------------------------------
    if mode == "AND":
        composite_buy = all(buy_conditions) if buy_conditions else False
        composite_sell = all(sell_conditions) if sell_conditions else False
    elif mode == "OR":
        composite_buy = any(buy_conditions) if buy_conditions else False
        composite_sell = any(sell_conditions) if sell_conditions else False
    else:
        # Custom logic expression (e.g. "(utbot and sr) or (utbot and linreg)")
        buy_vars = {
            "utbot": ut_buy,
            "sr": sr_buy,
            "linreg": lr_buy,
        }
        sell_vars = {
            "utbot": ut_sell,
            "sr": sr_sell,
            "linreg": lr_sell,
        }
        try:
            composite_buy = safe_eval_boolean(mode, buy_vars)
            composite_sell = safe_eval_boolean(mode, sell_vars)
        except Exception as e:
            log.error("Failed to evaluate custom signal_mode %r: %s", mode, e)
            composite_buy = False
            composite_sell = False

    # ---- Collect detail metadata -------------------------------------------
    details = {}
    if "ut_trail" in df.columns:
        details["ut_trail"] = float(df["ut_trail"].iloc[-1])
        details["ut_pos"] = int(df["ut_pos"].iloc[-1])
    if "lr_signal" in df.columns:
        lr_sig_val = df["lr_signal"].iloc[-1]
        lr_cls_val = df["lr_close"].iloc[-1]
        details["lr_signal"] = float(lr_sig_val) if not pd.isna(lr_sig_val) else None
        details["lr_close"] = float(lr_cls_val) if not pd.isna(lr_cls_val) else None
    if hasattr(df, "attrs") and "sr_zones" in df.attrs:
        details["sr_zones"] = df.attrs["sr_zones"][:3]  # top 3 zones

    return {
        "buy": composite_buy,
        "sell": composite_sell,
        "triggered_buy": triggered_buy,
        "triggered_sell": triggered_sell,
        "details": details,
    }
