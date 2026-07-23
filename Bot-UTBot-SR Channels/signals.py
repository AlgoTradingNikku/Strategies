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


def _cluster_sr_zones(
    pivot_values: list,
    cwidth: float,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    loopback_end_idx: int,
    loopback: int,
    min_strength: int,
    max_num_sr: int,
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
    start_idx = max(0, loopback_end_idx - loopback)
    end_idx   = min(loopback_end_idx + 1, len(high_arr))

    for z in raw_zones:
        hi, lo = z[1], z[2]
        touches = 0
        for bar_i in range(start_idx, end_idx):
            h = high_arr[bar_i]
            l = low_arr[bar_i]
            if (lo <= h <= hi) or (lo <= l <= hi):
                touches += 1
        z[0] += touches

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

    Also stores zones as df.attrs["sr_zones"].
    """
    df = df.copy()
    n  = len(df)

    if n < pivot_period * 2 + 1:
        df["sr_buy"]      = False
        df["sr_sell"]     = False
        df.attrs["sr_zones"] = []
        return df

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
        df["sr_buy"]         = False
        df["sr_sell"]        = False
        df.attrs["sr_zones"] = []
        return df

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
    )

    # ---- Evaluate support/resistance for every bar (vectorised) ------------
    sr_buy    = pd.Series(False, index=df.index)
    sr_sell   = pd.Series(False, index=df.index)
    close_v   = df["close"]

    for zone_hi, zone_lo in zones:
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

    df["sr_buy"]         = sr_buy
    df["sr_sell"]        = sr_sell
    df.attrs["sr_zones"] = zones

    return df


# ============================================================================
# 3. COMPOSITE SIGNAL EVALUATOR
# ============================================================================

def evaluate_composite_signals(
    df: pd.DataFrame,
    config: dict,
    lookback_candles: int = 2,
) -> dict:
    """
    Evaluate composite buy/sell signals based on signal_mode and enabled strategies.

    Signal Modes
    ------------
    "UTBot"    — UTBot buy/sell only; checked across last `lookback_candles` candles.
    "SR"       — SR Channel buy/sell only; checked on the most recent (last) candle.
    "UTBot+SR" — BOTH UTBot (last N candles) AND SR (last candle) must trigger.

    Returns
    -------
    dict with keys:
        buy            : bool
        sell           : bool
        triggered_buy  : list[str] — names of conditions that triggered
        triggered_sell : list[str]
        details        : dict      — indicator metadata for display
    """
    strat  = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    mode   = config.get("signal_mode", "UTBot+SR").upper().replace(" ", "")

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

    # ---- Combine based on mode ---------------------------------------------
    triggered_buy  = []
    triggered_sell = []

    if mode == "UTBOT":
        composite_buy  = ut_buy  if ut_enabled else False
        composite_sell = ut_sell if ut_enabled else False
        if ut_buy:
            triggered_buy.append("UT Bot")
        if ut_sell:
            triggered_sell.append("UT Bot")

    elif mode == "SR":
        composite_buy  = sr_buy  if sr_enabled else False
        composite_sell = sr_sell if sr_enabled else False
        if sr_buy:
            triggered_buy.append("S/R Support")
        if sr_sell:
            triggered_sell.append("S/R Resistance")

    else:
        # "UTBot+SR" — both must fire
        ut_ok  = ut_buy  if ut_enabled else True   # if disabled, treat as passed
        sr_ok  = sr_buy  if sr_enabled else True
        ut_ok_s  = ut_sell if ut_enabled else True
        sr_ok_s  = sr_sell if sr_enabled else True

        composite_buy  = ut_ok  and sr_ok
        composite_sell = ut_ok_s and sr_ok_s

        if composite_buy:
            if ut_enabled and ut_buy:
                triggered_buy.append("UT Bot")
            if sr_enabled and sr_buy:
                triggered_buy.append("S/R Support")
        if composite_sell:
            if ut_enabled and ut_sell:
                triggered_sell.append("UT Bot")
            if sr_enabled and sr_sell:
                triggered_sell.append("S/R Resistance")

    # ---- Collect detail metadata for display --------------------------------
    details = {}
    if "ut_trail" in df.columns:
        details["ut_trail"] = float(df["ut_trail"].iloc[-1])
        details["ut_pos"]   = int(df["ut_pos"].iloc[-1])
    if hasattr(df, "attrs") and "sr_zones" in df.attrs:
        details["sr_zones"] = df.attrs["sr_zones"][:3]  # top 3 zones

    return {
        "buy":            composite_buy,
        "sell":           composite_sell,
        "triggered_buy":  triggered_buy,
        "triggered_sell": triggered_sell,
        "details":        details,
    }
