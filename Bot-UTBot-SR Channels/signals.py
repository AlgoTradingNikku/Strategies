"""
===============================================================================
  Signal Engines — UT Bot & S/R Channels
===============================================================================

Pure computation logic for the two technical analysis signal engines ported
from the Pine Script indicator "UTBot+SR Channels":
  1. UT Bot ATR Trailing Stop
  2. Support / Resistance Channels

Each engine takes a pandas DataFrame with OHLCV columns and returns the same
DataFrame with signal columns appended.

Also provides the composite evaluator that combines both engines based on the
configured signal_mode ("UTBot", "SR", or "UTBot+SR").

Pine Script reference: PineScript-UTBot-SR Channels.txt
  © LonesomeTheBlue + UT Bot Alerts — Merged
===============================================================================
"""

import numpy as np
import pandas as pd
import logging

log = logging.getLogger("UTBotSRChannelsScanner")


# ============================================================================
# UTILITY
# ============================================================================

def _crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses above s2 (was <= on the prior bar)."""
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def _crossunder(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses below s2 (was >= on the prior bar)."""
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))


# ============================================================================
# 1. UT BOT ENGINE
# ============================================================================

def compute_utbot_signals(
    df: pd.DataFrame,
    key_value: float = 1.0,
    atr_period: int = 2,
    use_heikin_ashi: bool = False,
) -> pd.DataFrame:
    """
    Compute UT Bot ATR Trailing Stop signals.

    Mirrors the Pine Script UT Bot logic exactly:
      - Calculates ATR-based trailing stop (xATRTrailingStop / ut_trail)
      - Detects EMA-vs-trail crossovers for buy/sell entries

    Pine Script defaults: key_value=1.0, atr_period=2

    Appends columns
    ----------------
    ut_trail : float  — ATR trailing stop value
    ut_pos   : int    — position state (+1 long, -1 short, 0 neutral)
    ut_buy   : bool   — buy signal (EMA crosses above trailing stop)
    ut_sell  : bool   — sell signal (EMA crosses below trailing stop)
    """
    df = df.copy()

    # ---- Source price -------------------------------------------------------
    if use_heikin_ashi:
        # Approximate Heikin Ashi close from standard OHLC
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        src = ha_close
    else:
        src = df["close"]

    # ---- True Range / ATR (Wilder's smoothing = EWM alpha=1/period) --------
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    n_loss = key_value * atr

    # ---- xATRTrailingStop — iterative, matches Pine bar-by-bar logic --------
    # Pine Script logic:
    #   iff1  = src > nz(trail[1]) ? src - nLoss : src + nLoss
    #   iff2  = src < nz(trail[1]) and src[1] < nz(trail[1]) ?
    #             min(nz(trail[1]), src + nLoss) : iff1
    #   trail = src > nz(trail[1]) and src[1] > nz(trail[1]) ?
    #             max(nz(trail[1]), src - nLoss) : iff2
    src_vals = src.values
    nl_vals  = n_loss.values
    n        = len(src_vals)
    stop     = np.zeros(n)

    for i in range(1, n):
        prev_stop = stop[i - 1]
        prev_src  = src_vals[i - 1]
        cur_src   = src_vals[i]
        cur_nl    = nl_vals[i]

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

    # ---- Position state (Pine: ut_pos) -------------------------------------
    # pos = 1  when price crosses above trail (long)
    # pos = -1 when price crosses below trail (short)
    pos = np.zeros(n, dtype=int)
    for i in range(1, n):
        if src_vals[i - 1] < stop[i - 1] and src_vals[i] > stop[i]:
            pos[i] = 1
        elif src_vals[i - 1] > stop[i - 1] and src_vals[i] < stop[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]

    # ---- Crossover signals --------------------------------------------------
    # Pine: ut_ema = ta.ema(src, 1)  →  EMA(1) == src itself
    ema   = src
    above = _crossover(ema, xATR)
    below = _crossover(xATR, ema)

    # Pine: ut_buy  = src > trail and above
    #       ut_sell = src < trail and below
    df["ut_trail"] = xATR
    df["ut_pos"]   = pd.Series(pos, index=df.index)
    df["ut_buy"]   = (src > xATR) & above
    df["ut_sell"]  = (src < xATR) & below

    return df


# ============================================================================
# 2. S/R CHANNEL ENGINE
# ============================================================================

def _find_pivots(
    high_src: pd.Series,
    low_src: pd.Series,
    prd: int,
):
    """
    Find pivot highs and lows — equivalent to Pine's ta.pivothigh / ta.pivotlow.

    A pivot high at bar i requires high_src[i] to be the maximum in the
    symmetric window [i-prd, i+prd]. Similarly for pivot lows.

    Returns
    -------
    pivot_highs : list of (bar_index, value)
    pivot_lows  : list of (bar_index, value)
    """
    n      = len(high_src)
    h_vals = high_src.values
    l_vals = low_src.values

    pivot_highs = []
    pivot_lows  = []

    for i in range(prd, n - prd):
        # Pivot high
        h_val    = h_vals[i]
        window_h = h_vals[i - prd : i + prd + 1]
        if h_val >= window_h.max():
            pivot_highs.append((i, float(h_val)))

        # Pivot low
        l_val    = l_vals[i]
        window_l = l_vals[i - prd : i + prd + 1]
        if l_val <= window_l.min():
            pivot_lows.append((i, float(l_val)))

    return pivot_highs, pivot_lows


def compute_vpvr_poc(df: pd.DataFrame, bins: int = 50) -> float:
    """Calculate the Volume Point of Control (POC) over the DataFrame."""
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return 0.0
    
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    min_p = typical_price.min()
    max_p = typical_price.max()
    if min_p == max_p:
        return min_p
        
    bin_edges = np.linspace(min_p, max_p, bins + 1)
    bin_indices = np.digitize(typical_price, bin_edges) - 1
    bin_indices = np.clip(bin_indices, 0, bins - 1)
    
    vol_profile = np.zeros(bins)
    for i in range(len(df)):
        vol_profile[bin_indices[i]] += df["volume"].iloc[i]
        
    poc_bin_idx = np.argmax(vol_profile)
    return (bin_edges[poc_bin_idx] + bin_edges[poc_bin_idx + 1]) / 2.0


def _cluster_sr_zones(
    pivot_values: list,
    cwidth: float,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    loopback_end_idx: int,
    loopback: int,
    min_strength: int,
    max_num_sr: int,
    poc_price: float = 0.0,
) -> list:
    """
    Cluster pivot values into S/R zones, score them, and return the top zones.

    Faithfully replicates the Pine Script's get_sr_vals() + greedy selection
    algorithm from the SR Channel indicator.

    Parameters
    ----------
    pivot_values     : Pivot price values, ordered most-recent first.
    cwidth           : Max channel width in price units.
    high_arr         : Full high price array.
    low_arr          : Full low price array.
    loopback_end_idx : Bar index of the last bar in the DataFrame.
    loopback         : Bars to look back for touch counting.
    min_strength     : Minimum zone strength threshold (multiplied by 20 internally).
    max_num_sr       : Maximum number of zones to return.

    Returns
    -------
    list of (zone_hi, zone_lo) tuples sorted by strength descending.
    """
    num_pivots = len(pivot_values)
    if num_pivots == 0:
        return []

    # ---- Step 1: expand zones around each pivot seed (get_sr_vals) ----------
    # Pine get_sr_vals(ind):
    #   lo = pivotvals[ind]; hi = lo; numpp = 0
    #   for each cpp: wdth = cpp<=hi ? hi-cpp : cpp-lo
    #     if wdth <= cwidth: expand zone, numpp += 20
    raw_zones = []  # [strength, hi, lo]

    for seed_idx in range(num_pivots):
        lo    = pivot_values[seed_idx]
        hi    = lo
        numpp = 0

        for y in range(num_pivots):
            cpp  = pivot_values[y]
            wdth = hi - cpp if cpp <= hi else cpp - lo
            if wdth <= cwidth:
                if cpp <= hi:
                    lo = min(lo, cpp)
                else:
                    hi = max(hi, cpp)
                numpp += 20

        raw_zones.append([numpp, hi, lo])

    # ---- Step 2: add touch count — bars whose high or low falls in zone -----
    # Vectorised: slice the loopback window once, then use NumPy boolean masks
    # per zone instead of a Python loop over every bar (O(Z) not O(Z×B)).
    start_idx = max(0, loopback_end_idx - loopback)
    end_idx   = min(loopback_end_idx + 1, len(high_arr))
    h_slice   = high_arr[start_idx:end_idx]
    l_slice   = low_arr[start_idx:end_idx]

    for z in raw_zones:
        z_hi, z_lo = z[1], z[2]
        touches = int(
            (
                ((h_slice >= z_lo) & (h_slice <= z_hi)) |
                ((l_slice >= z_lo) & (l_slice <= z_hi))
            ).sum()
        )
        z[0] += touches

        # VPVR Hybrid Boost: if Point of Control (POC) is within this zone, multiply its strength
        # because a price pivot with high volume is a much stronger S/R level.
        if poc_price > 0 and (z_lo <= poc_price <= z_hi):
            z[0] *= 2.0  # Massive confluence multiplier

    # ---- Step 3: greedy selection of top-N non-overlapping zones ------------
    selected           = []
    strength_threshold = min_strength * 20

    for _ in range(min(max_num_sr, num_pivots)):
        best_idx      = -1
        best_strength = -1

        for i, z in enumerate(raw_zones):
            if z[0] > best_strength and z[0] >= strength_threshold:
                best_strength = z[0]
                best_idx      = i

        if best_idx < 0:
            break

        sel_hi = raw_zones[best_idx][1]
        sel_lo = raw_zones[best_idx][2]
        selected.append((sel_hi, sel_lo, best_strength))

        # Mask zones that overlap with the selected zone
        for z in raw_zones:
            z_hi, z_lo = z[1], z[2]
            if (sel_lo <= z_hi <= sel_hi) or (sel_lo <= z_lo <= sel_hi):
                z[0] = -1

    # Sort by strength descending
    selected.sort(key=lambda x: -x[2])
    # Return 3-tuples: (zone_hi, zone_lo, strength) so callers can use strength for scoring
    return [(hi, lo, strength) for hi, lo, strength in selected]


def compute_sr_signals(
    df: pd.DataFrame,
    pivot_period: int = 10,
    source: str = "High/Low",
    channel_width_pct: int = 5,
    min_strength: int = 1,
    max_num_sr: int = 6,
    loopback: int = 290,
    proximity_pct: float = 0.5,
) -> tuple:
    """
    Compute Support/Resistance channel signals.

    Port of the Pine Script SR Channel logic:
      1. Detect pivot highs/lows
      2. Cluster nearby pivots into zones
      3. Score zones by pivot-count + bar-touch count
      4. Select top-N strongest non-overlapping zones
      5. Classify price relative to each zone:
           - Inside zone       → both sr_buy and sr_sell (price is within the channel)
           - Near support top  → sr_buy  (price just above zone, within proximity)
           - Near resistance   → sr_sell (price just below zone, within proximity)

    Signal interpretation (aligned with Pine get_color):
      - Zone fully above close → Resistance  (sells)
      - Zone fully below close → Support     (buys)
      - Close inside zone      → In-channel  (both)

    Appends columns
    ----------------
    sr_buy  : bool — price is inside or near a support zone
    sr_sell : bool — price is inside or near a resistance zone

    Returns
    -------
    tuple[pd.DataFrame, list]
        DataFrame with sr_buy/sr_sell columns appended, and the list of
        (zone_hi, zone_lo) tuples for the detected S/R zones.
    """
    df = df.copy()
    n  = len(df)

    if n < pivot_period * 2 + 1:
        df["sr_buy"]  = False
        df["sr_sell"] = False
        zones: list   = []
        return df, zones

    # ---- Source for pivot detection -----------------------------------------
    if source == "High/Low":
        high_src = df["high"]
        low_src  = df["low"]
    else:  # "Close/Open"
        high_src = df[["close", "open"]].max(axis=1)
        low_src  = df[["close", "open"]].min(axis=1)

    # ---- Find all pivots in the dataset ------------------------------------
    pivot_highs, pivot_lows = _find_pivots(high_src, low_src, pivot_period)

    # ---- Collect pivots within loopback window, most-recent first ----------
    last_bar_idx = n - 1
    cutoff_idx   = last_bar_idx - loopback

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
    high_300    = df["high"].iloc[-window_size:].max()
    low_300     = df["low"].iloc[-window_size:].min()
    cwidth      = (high_300 - low_300) * channel_width_pct / 100

    if cwidth <= 0 or not pivot_values:
        df["sr_buy"]  = False
        df["sr_sell"] = False
        zones: list   = []
        return df, zones

    # ---- Compute Volume Point of Control (VPVR) ----------------------------
    # We only compute POC on the loopback window to keep it relevant to current zones.
    start_idx = max(0, last_bar_idx - loopback)
    loopback_df = df.iloc[start_idx:last_bar_idx + 1]
    poc = compute_vpvr_poc(loopback_df, bins=50)

    # ---- Compute zones ------------------------------------------------------
    zones = _cluster_sr_zones(
        pivot_values     = pivot_values,
        cwidth           = cwidth,
        high_arr         = df["high"].values,
        low_arr          = df["low"].values,
        loopback_end_idx = last_bar_idx,
        loopback         = loopback,
        min_strength     = min_strength,
        max_num_sr       = max_num_sr,
        poc_price        = poc,
    )

    # ---- Evaluate support/resistance for every bar (vectorised) ------------
    sr_buy    = pd.Series(False, index=df.index)
    sr_sell   = pd.Series(False, index=df.index)
    close_v   = df["close"]

    for zone_hi, zone_lo, _strength in zones:
        prox = close_v * proximity_pct / 100.0

        # Price is inside the zone → both S (below) and R (above) apply
        inside   = (close_v >= zone_lo) & (close_v <= zone_hi)
        sr_buy   = sr_buy  | inside
        sr_sell  = sr_sell | inside

        # Zone is below price → Support; buy if price is within proximity
        # above the zone top (zone_hi < close and close - zone_hi <= prox)
        below_near = (zone_hi < close_v) & ((close_v - zone_hi) <= prox)
        sr_buy     = sr_buy | below_near

        # Zone is above price → Resistance; sell if price is within proximity
        # below the zone bottom (zone_lo > close and zone_lo - close <= prox)
        above_near = (zone_lo > close_v) & ((zone_lo - close_v) <= prox)
        sr_sell    = sr_sell | above_near

    df["sr_buy"]  = sr_buy
    df["sr_sell"] = sr_sell

    return df, zones


# ============================================================================
# 3. CANDLESTICK PATTERN RECOGNITION
# ============================================================================

def detect_candle_patterns(df: pd.DataFrame) -> dict:
    """
    Detect key reversal candlestick patterns on the last 2-3 candles.

    Returns
    -------
    dict with keys:
        bullish_patterns : list of str  — pattern names favouring BUY
        bearish_patterns : list of str  — pattern names favouring SELL
    """
    bullish = []
    bearish = []

    if len(df) < 3:
        return {"bullish_patterns": bullish, "bearish_patterns": bearish}

    c  = df.iloc[-1]   # current candle
    p  = df.iloc[-2]   # prior candle
    pp = df.iloc[-3]   # 2 candles ago

    c_body  = abs(c["close"] - c["open"])
    p_body  = abs(p["close"] - p["open"])
    pp_body = abs(pp["close"] - pp["open"])

    c_bullish = c["close"] > c["open"]
    c_bearish = c["close"] < c["open"]
    p_bullish = p["close"] > p["open"]
    p_bearish = p["close"] < p["open"]

    c_range = c["high"] - c["low"]
    p_range = p["high"] - p["low"]

    # Guard against zero-range candles
    if c_range == 0 or p_range == 0:
        return {"bullish_patterns": bullish, "bearish_patterns": bearish}

    # ---- Bullish Engulfing ----
    # Current bullish body fully engulfs prior bearish body
    if (c_bullish and p_bearish and
            c["open"] <= p["close"] and c["close"] >= p["open"] and
            c_body > p_body):
        bullish.append("Engulfing")

    # ---- Bearish Engulfing ----
    if (c_bearish and p_bullish and
            c["open"] >= p["close"] and c["close"] <= p["open"] and
            c_body > p_body):
        bearish.append("Engulfing")

    # ---- Bullish Pin Bar / Hammer ----
    # Long lower wick >= 2x body, small upper wick
    lower_wick = min(c["open"], c["close"]) - c["low"]
    upper_wick = c["high"] - max(c["open"], c["close"])
    if c_body > 0 and lower_wick >= 2.0 * c_body and upper_wick <= c_body * 0.5:
        bullish.append("Pin Bar")

    # ---- Bearish Pin Bar / Shooting Star ----
    if c_body > 0 and upper_wick >= 2.0 * c_body and lower_wick <= c_body * 0.5:
        bearish.append("Pin Bar")

    # ---- Morning Star (bullish 3-candle reversal) ----
    # candle[-3] bearish, candle[-2] small body (doji/spinning top), candle[-1] bullish
    if (pp["close"] < pp["open"] and
            p_body < pp_body * 0.3 and
            c_bullish and c["close"] > (pp["open"] + pp["close"]) / 2):
        bullish.append("Morning Star")

    # ---- Evening Star (bearish 3-candle reversal) ----
    if (pp["close"] > pp["open"] and
            p_body < pp_body * 0.3 and
            c_bearish and c["close"] < (pp["open"] + pp["close"]) / 2):
        bearish.append("Evening Star")

    return {"bullish_patterns": bullish, "bearish_patterns": bearish}


def _candle_pattern_pts(pattern_name: str, at_sr_zone: bool) -> float:
    """Return score points for a candlestick pattern, boosted if at S/R zone."""
    base_pts = {
        "Engulfing":     8.0 if at_sr_zone else 3.0,
        "Pin Bar":       6.0 if at_sr_zone else 3.0,
        "Morning Star":  5.0 if at_sr_zone else 3.0,
        "Evening Star":  5.0 if at_sr_zone else 3.0,
    }
    return base_pts.get(pattern_name, 3.0)

def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 15) -> dict:
    """
    Detect RSI divergence on the most recent candle.
    Bullish: Price makes a Lower Low (or equal), but RSI makes a Higher Low.
    Bearish: Price makes a Higher High (or equal), but RSI makes a Lower High.
    """
    if len(df) < lookback + 1 or "rsi" not in df.columns:
        return {"bullish_div": False, "bearish_div": False}
        
    last_idx = len(df) - 1
    cur_low = df["low"].iloc[last_idx]
    cur_high = df["high"].iloc[last_idx]
    cur_rsi = df["rsi"].iloc[last_idx]
    
    # Check last 'lookback' bars (excluding current)
    window = df.iloc[last_idx - lookback : last_idx]
    
    # Bullish Div
    min_idx = window["low"].idxmin()
    min_low = window.loc[min_idx, "low"]
    min_rsi = window.loc[min_idx, "rsi"]
    bullish_div = bool((cur_low <= min_low) and (cur_rsi > min_rsi))
    
    # Bearish Div
    max_idx = window["high"].idxmax()
    max_high = window.loc[max_idx, "high"]
    max_rsi = window.loc[max_idx, "rsi"]
    bearish_div = bool((cur_high >= max_high) and (cur_rsi < max_rsi))
    
    return {"bullish_div": bullish_div, "bearish_div": bearish_div}


# ============================================================================
# 4. VOLATILITY SQUEEZE (TTM SQUEEZE)
# ============================================================================

def compute_squeeze(df: pd.DataFrame, length: int = 20, bb_mult: float = 2.0, kc_mult: float = 1.5) -> pd.DataFrame:
    """
    Detect Volatility Squeeze (Bollinger Bands inside Keltner Channels).
    
    Returns DataFrame with appended columns:
      - squeeze_on : bool (BB entirely inside KC)
      - squeeze_off : bool (BB outside KC)
      - squeeze_release : bool (transition from ON to OFF)
    """
    df = df.copy()
    close = df["close"]
    
    # Bollinger Bands
    sma = close.rolling(length).mean()
    std = close.rolling(length).std()
    bb_upper = sma + (bb_mult * std)
    bb_lower = sma - (bb_mult * std)
    
    # Keltner Channels
    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    
    # Standard Keltner uses SMA of True Range or ATR
    atr = tr.rolling(length).mean()
    # Or EMA, but simple SMA matches standard BB SMA nicely for KC
    kc_upper = sma + (kc_mult * atr)
    kc_lower = sma - (kc_mult * atr)
    
    # Squeeze is ON when BB is inside KC
    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    squeeze_off = (bb_upper > kc_upper) | (bb_lower < kc_lower)
    
    df["squeeze_on"] = squeeze_on
    df["squeeze_off"] = squeeze_off
    # Squeeze release is when it was ON on the previous bar, but OFF on the current bar
    df["squeeze_release"] = squeeze_off & squeeze_on.shift(1)
    
    return df


# ============================================================================
# 5. ADX TREND STRENGTH
# ============================================================================

def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate the Average Directional Index (ADX) using Wilder's smoothing.

    Returns a pandas Series of ADX values.
    """
    high = df["high"]
    low  = df["low"]
    close = df["close"]

    # Directional movement
    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's smoothing (EWM with alpha = 1/period)
    alpha = 1.0 / period
    atr     = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / (atr + 1e-10)
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / (atr + 1e-10)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    # Return adx, plus_di, minus_di so callers can determine directional bias
    return adx, plus_di, minus_di


# ============================================================================
# 5. ATR-BASED RISK/REWARD CALCULATOR
# ============================================================================

def calculate_risk_reward(
    df: pd.DataFrame,
    signal_type: str,
    zones: list,
    config: dict,
) -> dict:
    """
    Calculate stop-loss, target, and risk/reward ratio for a signal.

    Parameters
    ----------
    df           : DataFrame with OHLCV + ut_trail columns
    signal_type  : "BUY" or "SELL"
    zones        : list of (zone_hi, zone_lo, strength) S/R zones
    config       : full config dict

    Returns
    -------
    dict with keys: stop_loss, target, risk_reward (all floats or None)
    """
    if len(df) < 14:
        return {"stop_loss": None, "target": None, "risk_reward": None}

    filters_cfg = config.get("filters", {})
    atr_mult    = float(filters_cfg.get("rr_atr_multiplier", 0.5))
    default_rr  = float(filters_cfg.get("rr_default_ratio", 2.0))

    last_row    = df.iloc[-1]
    entry       = float(last_row["close"])

    # ATR(14) for buffer
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1])
    atr_buffer = atr_mult * atr_14

    ut_trail = float(last_row["ut_trail"]) if "ut_trail" in df.columns else None

    if signal_type == "BUY":
        # Stop Loss: tighter of UT Bot trail and nearest support zone bottom
        stop_candidates = []
        if ut_trail is not None:
            stop_candidates.append(ut_trail - atr_buffer)
        for zone_hi, zone_lo, _s in zones:
            if zone_hi < entry:  # zone is below price = support
                stop_candidates.append(zone_lo - atr_buffer)
                break  # take the first (strongest) support
        if not stop_candidates:
            stop_candidates.append(entry - 2.0 * atr_14)  # fallback

        stop_loss = max(stop_candidates)  # tighter = higher stop for BUY

        # Target: next resistance zone above entry, or default R:R
        target = None
        for zone_hi, zone_lo, _s in zones:
            if zone_lo > entry:  # zone is above price = resistance
                target = zone_hi
                break
                
        risk = entry - stop_loss if entry > stop_loss else 1e-5
        if target is None:
            target = entry + default_rr * risk
        else:
            # Bound S/R target dynamically using ATR
            min_target_dist = 1.0 * atr_14
            max_target_dist = max(default_rr * risk, 5.0 * atr_14)
            dist = target - entry
            if dist < min_target_dist:
                target = entry + min_target_dist
            elif dist > max_target_dist:
                target = entry + max_target_dist

    else:  # SELL
        # Stop Loss: tighter of UT Bot trail and nearest resistance zone top
        stop_candidates = []
        if ut_trail is not None:
            stop_candidates.append(ut_trail + atr_buffer)
        for zone_hi, zone_lo, _s in zones:
            if zone_lo > entry:  # zone is above price = resistance
                stop_candidates.append(zone_hi + atr_buffer)
                break
        if not stop_candidates:
            stop_candidates.append(entry + 2.0 * atr_14)  # fallback

        stop_loss = min(stop_candidates)  # tighter = lower stop for SELL

        # Target: next support zone below entry, or default R:R
        target = None
        for zone_hi, zone_lo, _s in zones:
            if zone_hi < entry:  # zone is below price = support
                target = zone_lo
                break
                
        risk = stop_loss - entry if stop_loss > entry else 1e-5
        if target is None:
            target = entry - default_rr * risk
        else:
            # Bound S/R target dynamically using ATR
            min_target_dist = 1.0 * atr_14
            max_target_dist = max(default_rr * risk, 5.0 * atr_14)
            dist = entry - target
            if dist < min_target_dist:
                target = entry - min_target_dist
            elif dist > max_target_dist:
                target = entry - max_target_dist

    # Calculate R:R
    risk = abs(entry - stop_loss)
    reward = abs(target - entry)
    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

    return {
        "stop_loss":    round(stop_loss, 2),
        "target":       round(target, 2),
        "risk_reward":  risk_reward,
    }


# ============================================================================
# 6. MULTI-TIMEFRAME CONFIRMATION
# ============================================================================

def check_mtf_confirmation(
    htf_df: pd.DataFrame,
    config: dict,
) -> dict:
    """
    Check trend direction on a higher-timeframe DataFrame using UTBot trail.

    The caller is responsible for fetching the higher-TF data and passing it in.
    This function runs the UTBot engine on it and checks price vs trail.

    Parameters
    ----------
    htf_df  : Higher-timeframe DataFrame with OHLCV data
    config  : full config dict

    Returns
    -------
    dict with keys:
        trend      : "bullish" | "bearish" | "neutral"
        htf_trail  : float — trailing stop value on the higher TF
        htf_close  : float — last close on the higher TF
    """
    if htf_df is None or len(htf_df) < 20:
        return {"trend": "neutral", "htf_trail": None, "htf_close": None}

    strat = config.get("strategy", {})
    filters_cfg = config.get("filters", {})
    # Use a dedicated MTF ATR period for a smoother trail on the higher timeframe.
    # Falls back to the LTF atr_period if not explicitly set.
    mtf_atr = int(filters_cfg.get("mtf_atr_period", strat.get("atr_period", 10)))
    htf_df = compute_utbot_signals(
        htf_df,
        key_value       = float(strat.get("key_value", 1.0)),
        atr_period      = mtf_atr,
        use_heikin_ashi = bool(strat.get("use_heikin_ashi", False)),
    )

    last = htf_df.iloc[-1]
    htf_close = float(last["close"])
    htf_trail = float(last["ut_trail"])

    # Determine proximity — if price is within N% of trail, call it neutral
    mtf_neutral_pct = float(config.get("filters", {}).get("mtf_neutral_pct", 0.3))
    pct_diff = abs(htf_close - htf_trail) / htf_close * 100 if htf_close > 0 else 0

    if pct_diff < mtf_neutral_pct:
        trend = "neutral"
    elif htf_close > htf_trail:
        trend = "bullish"
    else:
        trend = "bearish"

    return {
        "trend":     trend,
        "htf_trail": round(htf_trail, 2),
        "htf_close": round(htf_close, 2),
    }


# ============================================================================
# 7. COMPOSITE SIGNAL EVALUATOR
# ============================================================================

def evaluate_composite_signals(
    df: pd.DataFrame,
    config: dict,
    lookback_candles: int = 2,
    sr_zones: list = None,
) -> dict:
    """
    Evaluate composite buy/sell signals based on signal_mode and enabled strategies.
    Also calculates Setup Scores (0-100) and applies technical filters (EMA 200, Volume).

    Signal Modes
    ------------
    "UTBot"    — UTBot buy/sell only; checked across last `lookback_candles` candles.
    "SR"       — SR Channel buy/sell only; checked on the most recent (last) candle.
    "UTBot+SR" — BOTH UTBot (last N candles) AND SR (last candle) must trigger.

    Parameters
    ----------
    sr_zones : list, optional
        Pre-computed S/R zones from compute_sr_signals(). When provided these
        are used directly instead of reading from df.attrs (which is fragile
        across pandas operations).
    """
    # ---- 1. Calculate technical indicators for filters and scoring ----
    filters_cfg = config.get("filters", {})
    ema_period = int(filters_cfg.get("ema_period", 200))
    rsi_period = int(filters_cfg.get("rsi_period", 14))
    vol_sma_period = int(filters_cfg.get("volume_sma_period", 20))

    if "close" in df.columns:
        df["ema_trend"] = df["close"].ewm(span=ema_period, adjust=False).mean()
        
        # Calculate RSI using Wilder's smoothing (EWM alpha=1/period) — matches TradingView
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1.0 / rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1.0 / rsi_period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))

        # Calculate ADX 14 — also store plus_di/minus_di for directional scoring
        df["adx_14"], df["plus_di"], df["minus_di"] = compute_adx(df, period=14)

        # Calculate Volatility Squeeze (TTM Squeeze)
        sqz_len = int(filters_cfg.get("squeeze_length", 20))
        sqz_bb  = float(filters_cfg.get("squeeze_bb_mult", 2.0))
        sqz_kc  = float(filters_cfg.get("squeeze_kc_mult", 1.5))
        if len(df) >= sqz_len:
            sqz_df = compute_squeeze(df, length=sqz_len, bb_mult=sqz_bb, kc_mult=sqz_kc)
            df["squeeze_on"]      = sqz_df["squeeze_on"]
            df["squeeze_off"]     = sqz_df["squeeze_off"]
            df["squeeze_release"] = sqz_df["squeeze_release"]

    if "volume" in df.columns and len(df) >= vol_sma_period:
        df["vol_sma"] = df["volume"].rolling(vol_sma_period).mean()

    strat  = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    ut_enabled = strat.get("ut_enabled", True)
    sr_enabled = sr_cfg.get("enabled", True)

    # ---- UT Bot: check last N candles for any buy/sell ----------------------
    ut_buy  = False
    ut_sell = False
    if ut_enabled and "ut_buy" in df.columns:
        tail    = df.tail(lookback_candles)
        ut_buy  = bool(tail["ut_buy"].any())
        ut_sell = bool(tail["ut_sell"].any())

    # ---- S/R Channels: check current (last) candle only --------------------
    sr_buy  = False
    sr_sell = False
    if sr_enabled and "sr_buy" in df.columns and len(df) > 0:
        sr_buy  = bool(df["sr_buy"].iloc[-1])
        sr_sell = bool(df["sr_sell"].iloc[-1])

    # ---- Combine based on enabled engines ----------------------------------
    triggered_buy  = []
    triggered_sell = []

    if ut_enabled and sr_enabled:
        # Both must trigger (UTBot + SR)
        composite_buy  = ut_buy and sr_buy
        composite_sell = ut_sell and sr_sell
        if composite_buy:
            triggered_buy.extend(["UT Bot", "S/R Support"])
        if composite_sell:
            triggered_sell.extend(["UT Bot", "S/R Resistance"])
    elif ut_enabled:
        # UTBot only
        composite_buy  = ut_buy
        composite_sell = ut_sell
        if ut_buy:
            triggered_buy.append("UT Bot")
        if ut_sell:
            triggered_sell.append("UT Bot")
    elif sr_enabled:
        # SR Channels only
        composite_buy  = sr_buy
        composite_sell = sr_sell
        if sr_buy:
            triggered_buy.append("S/R Support")
        if sr_sell:
            triggered_sell.append("S/R Resistance")
    else:
        # Neither enabled -> no signals
        composite_buy  = False
        composite_sell = False

    # ---- Calculate Setup Score & Reasons -----------------------------------
    # NOTE: Scoring always runs on the raw signal state — hard filters only
    # gate whether the signal is *shown* (they run after scoring below).
    # EMA and Volume always contribute points when conditions are met,
    # regardless of whether their mandatory-filter toggles are enabled.
    last_row = df.iloc[-1] if len(df) > 0 else None
    buy_score = 0.0
    buy_reasons = []
    sell_score = 0.0
    sell_reasons = []

    # Use explicitly passed zones; fall back to empty list if not provided.
    zones = sr_zones if sr_zones is not None else []

    if last_row is not None:
        close_price = float(last_row["close"])
        candle_bullish = close_price > float(last_row["open"])   # green candle

        # 1. S/R Zone Strength & Proximity
        # Points are now based on the zone's actual strength score (pivot + touch count),
        # not its positional rank. Strength pts scale 10–30 based on relative zone quality.
        if zones:
            prox_cfg = config.get("sr_channels", {}).get("proximity_pct", 0.5)
            max_zone_strength = max(s for _, _, s in zones) if zones else 1

            # BUY setup score from S/R Support
            best_buy_zone_pts = 0.0
            best_buy_zone_reason = ""
            for idx, (zone_hi, zone_lo, zone_strength) in enumerate(zones):
                inside = (close_price >= zone_lo) and (close_price <= zone_hi)
                above_near = (zone_hi < close_price) and ((close_price - zone_hi) <= (close_price * prox_cfg / 100.0))

                if inside or above_near:
                    # Proximity: full 15 pts if inside, linearly scaled if nearby
                    if inside:
                        prox_pts = 15.0
                    elif prox_cfg > 0:
                        dist_pct = ((close_price - zone_hi) / close_price) * 100.0
                        prox_pts = 15.0 * max(0.0, 1.0 - dist_pct / prox_cfg)
                    else:
                        prox_pts = 15.0
                    # Strength: 10–30 pts scaled proportionally to this zone's actual score
                    str_pts = 10.0 + 20.0 * (zone_strength / max_zone_strength)
                    zone_pts = round(prox_pts + str_pts, 1)
                    if zone_pts > best_buy_zone_pts:
                        best_buy_zone_pts = zone_pts
                        best_buy_zone_reason = f"Bouncing inside/near Support (strength {zone_strength}) (+{zone_pts:.1f} pts)"

            if best_buy_zone_pts > 0:
                buy_score += best_buy_zone_pts
                buy_reasons.append(best_buy_zone_reason)

            # SELL setup score from S/R Resistance
            best_sell_zone_pts = 0.0
            best_sell_zone_reason = ""
            for idx, (zone_hi, zone_lo, zone_strength) in enumerate(zones):
                inside = (close_price >= zone_lo) and (close_price <= zone_hi)
                below_near = (zone_lo > close_price) and ((zone_lo - close_price) <= (close_price * prox_cfg / 100.0))

                if inside or below_near:
                    if inside:
                        prox_pts = 15.0
                    elif prox_cfg > 0:
                        dist_pct = ((zone_lo - close_price) / close_price) * 100.0
                        prox_pts = 15.0 * max(0.0, 1.0 - dist_pct / prox_cfg)
                    else:
                        prox_pts = 15.0
                    str_pts = 10.0 + 20.0 * (zone_strength / max_zone_strength)
                    zone_pts = round(prox_pts + str_pts, 1)
                    if zone_pts > best_sell_zone_pts:
                        best_sell_zone_pts = zone_pts
                        best_sell_zone_reason = f"Rejecting inside/near Resistance (strength {zone_strength}) (+{zone_pts:.1f} pts)"

            if best_sell_zone_pts > 0:
                sell_score += best_sell_zone_pts
                sell_reasons.append(best_sell_zone_reason)

        # 2. Volume Spike Confirmation (up to 15 pts) — DIRECTIONAL
        # Points only awarded to the direction that matches the candle colour.
        # A bullish (green) candle volume spike favours BUY; bearish favours SELL.
        if "volume" in df.columns and "vol_sma" in df.columns:
            vol_sma = float(last_row["vol_sma"])
            last_vol = float(last_row["volume"])
            if vol_sma > 0:
                vol_ratio = last_vol / vol_sma
                vol_pts = min(15.0, 10.0 * vol_ratio)
                if vol_pts > 0:
                    if candle_bullish:
                        buy_score += vol_pts
                        buy_reasons.append(f"Bullish volume surge: {vol_ratio:.2f}x avg (+{vol_pts:.1f} pts)")
                    else:
                        sell_score += vol_pts
                        sell_reasons.append(f"Bearish volume surge: {vol_ratio:.2f}x avg (+{vol_pts:.1f} pts)")

        # 3. EMA Trend Confluence (10–20 pts) — PROPORTIONAL
        # Scoring scales with how far price is from the EMA:
        #   within 1% of EMA → 10 pts; 2%+ away → 20 pts (linearly interpolated).
        # ema_above is always captured so the frontend icon knows the EMA position.
        ema_above = None   # None = no EMA data available
        if "ema_trend" in df.columns and len(df) >= ema_period:
            ema_val = float(last_row["ema_trend"])
            if ema_val > 0:
                ema_above = close_price > ema_val
                pct_from_ema = abs(close_price - ema_val) / ema_val * 100.0
                ema_pts = round(min(20.0, 10.0 + 5.0 * pct_from_ema), 1)
                if close_price > ema_val:
                    buy_score += ema_pts
                    buy_reasons.append(f"Bullish: above EMA{ema_period} by {pct_from_ema:.1f}% (+{ema_pts:.1f} pts)")
                else:
                    sell_score += ema_pts
                    sell_reasons.append(f"Bearish: below EMA{ema_period} by {pct_from_ema:.1f}% (+{ema_pts:.1f} pts)")

        # 4. RSI Momentum Confluence (up to 10 pts) — EXCLUSIVE RANGES
        # Ranges are now enforced as exclusive so a single RSI value cannot
        # simultaneously boost both BUY and SELL scores.
        # rsi_ok is always captured so the frontend icon knows the RSI range pass/fail.
        rsi_buy_min  = float(filters_cfg.get("rsi_buy_min", 40))
        rsi_buy_max  = float(filters_cfg.get("rsi_buy_max", 60))   # tightened from 65
        rsi_sell_min = float(filters_cfg.get("rsi_sell_min", 40))  # tightened from 35
        rsi_sell_max = float(filters_cfg.get("rsi_sell_max", 60))
        rsi_ok = None   # None = no RSI data available
        if "rsi" in df.columns:
            rsi = float(last_row["rsi"])
            # rsi_ok: passes the range relevant to the candle direction
            rsi_ok = (rsi_buy_min <= rsi <= rsi_buy_max) if candle_bullish \
                else (rsi_sell_min <= rsi <= rsi_sell_max)
            # Only award RSI pts to the direction the candle is moving
            if candle_bullish and rsi_ok:
                buy_score += 10.0
                buy_reasons.append(f"Optimal RSI: {rsi:.1f} ({rsi_buy_min:.0f}-{rsi_buy_max:.0f}) (+10.0 pts)")
            elif not candle_bullish and rsi_ok:
                sell_score += 10.0
                sell_reasons.append(f"Optimal RSI: {rsi:.1f} ({rsi_sell_min:.0f}-{rsi_sell_max:.0f}) (+10.0 pts)")

        # 4.5 RSI Divergence (up to 15 pts)
        divs = detect_rsi_divergence(df, lookback=15)
        if divs.get("bullish_div"):
            buy_score += 15.0
            buy_reasons.append(f"Bullish RSI Divergence (+15.0 pts)")
            triggered_buy.append("Bullish Divergence")
        if divs.get("bearish_div"):
            sell_score += 15.0
            sell_reasons.append(f"Bearish RSI Divergence (+15.0 pts)")
            triggered_sell.append("Bearish Divergence")

        # 5. ADX Trend Strength (up to 10 pts) — DIRECTIONAL via +DI / -DI
        # Uses +DI > -DI to confirm bullish momentum; -DI > +DI for bearish.
        # Prevents a strong downtrend from inflating a BUY score.
        # adx_ok is always captured so the frontend icon knows the ADX threshold pass/fail.
        adx_strong          = float(filters_cfg.get("adx_strong_threshold", 25))
        adx_moderate        = float(filters_cfg.get("adx_moderate_threshold", 20))
        adx_threshold_hard  = float(filters_cfg.get("adx_min_threshold", 20))
        adx_ok = None   # None = no ADX data available
        if "adx_14" in df.columns and "plus_di" in df.columns and "minus_di" in df.columns:
            adx_val  = float(last_row["adx_14"])
            plus_di  = float(last_row["plus_di"])
            minus_di = float(last_row["minus_di"])
            adx_ok = adx_val >= adx_threshold_hard
            if adx_val >= adx_strong:
                adx_pts = 10.0
            elif adx_val >= adx_moderate:
                adx_pts = 5.0
            else:
                adx_pts = 0.0
            if adx_pts > 0:
                if plus_di > minus_di:
                    buy_score += adx_pts
                    buy_reasons.append(f"Bullish ADX: {adx_val:.1f} (+DI>{minus_di:.1f}) (+{adx_pts:.1f} pts)")
                else:
                    sell_score += adx_pts
                    sell_reasons.append(f"Bearish ADX: {adx_val:.1f} (-DI>{plus_di:.1f}) (+{adx_pts:.1f} pts)")

        # 5.5 Volatility Squeeze Release (up to 15 pts)
        # Squeeze release happens when Bollinger Bands expand outside Keltner Channels.
        # sqz_ok is always captured so the frontend icon knows whether a release occurred.
        sqz_ok = None   # None = no squeeze data available
        if "squeeze_release" in df.columns:
            sqz_ok = bool(last_row["squeeze_release"])
            if sqz_ok:
                # Squeeze released! Award 15 pts to the direction of the signal
                if candle_bullish:
                    buy_score += 15.0
                    buy_reasons.append(f"Bullish Squeeze Release (+15.0 pts)")
                    triggered_buy.append("Squeeze Release")
                else:
                    sell_score += 15.0
                    sell_reasons.append(f"Bearish Squeeze Release (+15.0 pts)")
                    triggered_sell.append("Squeeze Release")

        # 6. Candlestick Pattern Recognition (up to 8 pts) — BEST ONLY (no stacking)
        # Only the highest-scoring bullish/bearish pattern is counted to avoid
        # inflating scores when multiple patterns coincide on the same candle.
        candle_patterns_on = filters_cfg.get("candle_patterns_enabled", True)
        if candle_patterns_on:
            patterns = detect_candle_patterns(df)
            has_sr_zones = len(zones) > 0

            # Pick best bullish pattern only
            best_bull_pts = 0.0
            best_bull_name = ""
            for pat_name in patterns.get("bullish_patterns", []):
                pts = _candle_pattern_pts(pat_name, has_sr_zones)
                if pts > best_bull_pts:
                    best_bull_pts = pts
                    best_bull_name = pat_name
            if best_bull_name:
                sr_note = " at S/R" if has_sr_zones else ""
                buy_score += best_bull_pts
                buy_reasons.append(f"Bullish {best_bull_name}{sr_note} (+{best_bull_pts:.1f} pts)")
                triggered_buy.append(best_bull_name)

            # Pick best bearish pattern only
            best_bear_pts = 0.0
            best_bear_name = ""
            for pat_name in patterns.get("bearish_patterns", []):
                pts = _candle_pattern_pts(pat_name, has_sr_zones)
                if pts > best_bear_pts:
                    best_bear_pts = pts
                    best_bear_name = pat_name
            if best_bear_name:
                sr_note = " at S/R" if has_sr_zones else ""
                sell_score += best_bear_pts
                sell_reasons.append(f"Bearish {best_bear_name}{sr_note} (+{best_bear_pts:.1f} pts)")
                triggered_sell.append(best_bear_name)

    # Stage 1 scores are left uncapped here intentionally.
    # The single final cap to 100 is applied at the end of scanner._build_result()
    # after MTF (+15) and RS (+10) adjustments are added in Stage 2.
    buy_score  = round(buy_score, 1)
    sell_score = round(sell_score, 1)

    # ---- Apply Hard Filters (gate visibility, NOT scoring) -----------------
    # These run AFTER scoring so scores are always fully computed.
    if last_row is not None:
        close_price = float(last_row["close"])

        # EMA Trend Filter — mandatory gate when enabled
        ema_filter = filters_cfg.get("ema_filter_enabled", False)
        if ema_filter and "ema_trend" in df.columns and len(df) >= ema_period:
            ema_val = float(last_row["ema_trend"])
            if composite_buy and close_price <= ema_val:
                composite_buy = False
                log.info("Filtered out BUY: Close (%.2f) below EMA %d (%.2f)", close_price, ema_period, ema_val)
            if composite_sell and close_price >= ema_val:
                composite_sell = False
                log.info("Filtered out SELL: Close (%.2f) above EMA %d (%.2f)", close_price, ema_period, ema_val)

        # Volume SMA Filter — mandatory gate when enabled.
        # vol_ok is always computed (regardless of filter toggle) so the frontend
        # icon faithfully reflects whether volume cleared the configured threshold.
        vol_filter  = filters_cfg.get("volume_filter_enabled", False)
        vol_min_pct = float(filters_cfg.get("volume_min_pct", 80)) / 100.0
        vol_ok = True   # default: pass (no vol data → show icon as active)
        if "volume" in df.columns and "vol_sma" in df.columns:
            vol_sma  = float(last_row["vol_sma"])
            last_vol = float(last_row["volume"])
            if vol_sma > 0:
                vol_ratio = last_vol / vol_sma
                vol_ok = (last_vol >= vol_min_pct * vol_sma)
                if vol_filter:
                    if composite_buy and not vol_ok:
                        composite_buy = False
                        log.info("Filtered out BUY (Volume): Vol=%.0f is %.0f%% of SMA (min %.0f%%)",
                                 last_vol, vol_ratio * 100, vol_min_pct * 100)
                    elif composite_buy:
                        log.debug("Volume OK for BUY: Vol=%.0f is %.0f%% of SMA", last_vol, vol_ratio * 100)
                    if composite_sell and not vol_ok:
                        composite_sell = False
                        log.info("Filtered out SELL (Volume): Vol=%.0f is %.0f%% of SMA (min %.0f%%)",
                                 last_vol, vol_ratio * 100, vol_min_pct * 100)

        # ADX Hard Filter (opt-in)
        adx_hard_filter = filters_cfg.get("adx_filter_enabled", False)
        adx_threshold   = float(filters_cfg.get("adx_min_threshold", 20))
        if adx_hard_filter and "adx_14" in df.columns:
            adx_val = float(last_row["adx_14"])
            if composite_buy and adx_val < adx_threshold:
                composite_buy = False
                log.info("Filtered out BUY: ADX (%.1f) below threshold (%.0f)", adx_val, adx_threshold)
            if composite_sell and adx_val < adx_threshold:
                composite_sell = False
                log.info("Filtered out SELL: ADX (%.1f) below threshold (%.0f)", adx_val, adx_threshold)

        # RSI Hard Filter (opt-in)
        rsi_hard_filter = filters_cfg.get("rsi_filter_enabled", False)
        if rsi_hard_filter and "rsi" in df.columns:
            rsi_val      = float(last_row["rsi"])
            rsi_buy_min  = float(filters_cfg.get("rsi_buy_min", 40))
            rsi_buy_max  = float(filters_cfg.get("rsi_buy_max", 65))
            rsi_sell_min = float(filters_cfg.get("rsi_sell_min", 35))
            rsi_sell_max = float(filters_cfg.get("rsi_sell_max", 60))
            if composite_buy and not (rsi_buy_min <= rsi_val <= rsi_buy_max):
                composite_buy = False
                log.info("Filtered out BUY: RSI (%.1f) outside range (%.0f-%.0f)", rsi_val, rsi_buy_min, rsi_buy_max)
            if composite_sell and not (rsi_sell_min <= rsi_val <= rsi_sell_max):
                composite_sell = False
                log.info("Filtered out SELL: RSI (%.1f) outside range (%.0f-%.0f)", rsi_val, rsi_sell_min, rsi_sell_max)

        # Volatility Squeeze Hard Filter (opt-in)
        # Only allow signals if they occur exactly when a squeeze releases.
        sqz_hard_filter = filters_cfg.get("squeeze_filter_enabled", False)
        if sqz_hard_filter and "squeeze_release" in df.columns:
            is_release = bool(last_row["squeeze_release"])
            if composite_buy and not is_release:
                composite_buy = False
                log.info("Filtered out BUY: Not in a Squeeze Release")
            if composite_sell and not is_release:
                composite_sell = False
                log.info("Filtered out SELL: Not in a Squeeze Release")

    # ---- Collect detail metadata for display --------------------------------
    details = {}
    if "ut_trail" in df.columns:
        details["ut_trail"] = float(df["ut_trail"].iloc[-1])
        details["ut_pos"]   = int(df["ut_pos"].iloc[-1])
    if zones:
        details["sr_zones"] = zones[:3]  # top 3 zones
    if "adx_14" in df.columns and len(df) > 0:
        details["adx"] = round(float(df["adx_14"].iloc[-1]), 1)

    return {
        "buy":            composite_buy,
        "sell":           composite_sell,
        "triggered_buy":  triggered_buy,
        "triggered_sell": triggered_sell,
        "buy_score":      buy_score,
        "buy_reasons":    buy_reasons,
        "sell_score":     sell_score,
        "sell_reasons":   sell_reasons,
        "details":        details,
        "vol_ok":         vol_ok,
        "ema_above":      ema_above,
        "adx_ok":         adx_ok,
        "rsi_ok":         rsi_ok,
        "sqz_ok":         sqz_ok,
    }
