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
# ENGINE REGISTRY — Scalable N-engine configuration
# ============================================================================

ENGINE_REGISTRY = [
    {
        "key": "ut_bot",
        "label": "UT Bot",
        "config_section": "strategy",
        "enabled_key": "ut_enabled",
        "default_enabled": True,
        "buy_col": "ut_buy",
        "sell_col": "ut_sell",
        "eval_mode": "window",  # Checks last N candles with most-recent-wins reducer
        "buy_label": "UT Bot",
        "sell_label": "UT Bot",
    },
    {
        "key": "sr_channels",
        "label": "S/R Channels",
        "config_section": "sr_channels",
        "enabled_key": "enabled",
        "default_enabled": True,
        "buy_col": "sr_buy",
        "sell_col": "sr_sell",
        "eval_mode": "instant",  # Checks only the last candle
        "buy_label": "S/R Support",
        "sell_label": "S/R Resistance",
    },
    {
        "key": "momentum",
        "label": "Momentum Engine",
        "config_section": "momentum",
        "enabled_key": "enabled",
        "default_enabled": False,
        "buy_col": "momentum_buy",
        "sell_col": "momentum_sell",
        "eval_mode": "window",
        "buy_label": "Momentum Long",
        "sell_label": "Momentum Short",
        "components": [
            {"key": "rsi", "label": "RSI", "config_key": "rsi_enabled", "display_label": "RSI (14:40-70)"},
            {"key": "volume", "label": "Volume", "config_key": "volume_enabled", "display_label": "Volume (1.5× SMA)"},
            {"key": "adx", "label": "ADX", "config_key": "adx_enabled", "display_label": "ADX (14 > 20)"},
            {"key": "ema", "label": "EMA", "config_key": "ema_enabled", "display_label": "EMA Trend (200)"},
            {"key": "bb", "label": "BB", "config_key": "bb_enabled", "display_label": "BB (20:2)"},
            {"key": "roc", "label": "ROC", "config_key": "roc_enabled", "display_label": "ROC (10 > 3%)"},
        ],
    },
    {
        "key": "mean_reversion",
        "label": "Mean Reversion Engine",
        "config_section": "mean_reversion",
        "enabled_key": "enabled",
        "default_enabled": False,
        "buy_col": "mr_buy",
        "sell_col": "mr_sell",
        "eval_mode": "instant",
        "buy_label": "Mean Rev Long",
        "sell_label": "Mean Rev Short",
        "components": [
            {"key": "bb", "label": "BB Touch", "config_key": "bb_enabled", "display_label": "BB Touch (20:2)"},
            {"key": "rsi_div", "label": "RSI Divergence", "config_key": "rsi_div_enabled", "display_label": "RSI Divergence"},
            {"key": "rsi_extreme", "label": "RSI Extreme", "config_key": "rsi_extreme_enabled", "display_label": "RSI <30 or >70"},
            {"key": "stochastic", "label": "Stochastic", "config_key": "stochastic_enabled", "display_label": "Stochastic (14)"},
            {"key": "zscore", "label": "Z-Score", "config_key": "zscore_enabled", "display_label": "Z-Score (±2)"},
            {"key": "vol_climax", "label": "Volume Climax", "config_key": "vol_climax_enabled", "display_label": "Vol Climax (2.5×)"},
        ],
    },
]


# ============================================================================
# UTILITY
# ============================================================================

def _crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True on bars where s1 crosses above s2 (was <= on the prior bar)."""
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def _parse_timeframe_seconds(timeframe: str) -> int | None:
    """Convert a config timeframe string (e.g. '5m', '1h', '1d') to seconds.

    Returns None if the format is not recognised — callers should treat that
    as "cannot determine bar boundary" and skip closed-candle logic.
    """
    if not isinstance(timeframe, str) or len(timeframe) < 2:
        return None
    try:
        num = int(timeframe[:-1])
    except (ValueError, TypeError):
        return None
    unit = timeframe[-1].lower()
    if unit == "m":
        return num * 60
    if unit == "h":
        return num * 3600
    if unit == "d":
        return num * 86400
    if unit == "w":
        return num * 604800
    return None


def _is_last_candle_incomplete(df: pd.DataFrame, config: dict) -> bool:
    """Return True when the LAST row of ``df`` is still-forming.

    Heuristic: given the configured candle timeframe, compute the expected
    close time of the last bar (bar_open + timeframe) and compare against the
    current wall clock in the exchange timezone. When the current time is
    before the expected close, the bar is still open ("running bar").

    Falls back to False (i.e. "treat as closed") when:
      • the df index isn't a DatetimeIndex,
      • the timeframe string can't be parsed,
      • or any other error occurs — so callers never accidentally strip a
        legitimate closed bar due to a helper malfunction.
    """
    if df is None or len(df) == 0:
        return False
    try:
        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            return False

        timeframe = config.get("candle_timeframe") or config.get("scan_timeframe", "5m")
        bar_secs = _parse_timeframe_seconds(str(timeframe))
        if bar_secs is None:
            return False

        # Use the exchange tz — for NSE/BSE that's Asia/Kolkata; fallback UTC.
        from zoneinfo import ZoneInfo
        tz_name = "Asia/Kolkata" if config.get("exchange", "NSE").upper() in ("NSE", "BSE") else "UTC"
        tz = ZoneInfo(tz_name)

        last_open = idx[-1]
        # Normalise both times to naive-in-tz for comparison.
        if last_open.tzinfo is None:
            last_open_local = last_open
        else:
            last_open_local = last_open.astimezone(tz).replace(tzinfo=None)

        from datetime import datetime, timedelta
        now_local = datetime.now(tz).replace(tzinfo=None)
        expected_close = last_open_local + timedelta(seconds=bar_secs)

        return now_local < expected_close
    except Exception:
        return False


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
    if n > 0:
        init_nl = nl_vals[0] if (len(nl_vals) > 0 and not np.isnan(nl_vals[0])) else 0.0
        stop[0] = src_vals[0] - init_nl

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
    if n > 0:
        pos[0] = 1 if src_vals[0] >= stop[0] else -1

    for i in range(1, n):
        if src_vals[i - 1] <= stop[i - 1] and src_vals[i] > stop[i]:
            pos[i] = 1
        elif src_vals[i - 1] >= stop[i - 1] and src_vals[i] < stop[i]:
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
    pos_s = pd.Series(pos, index=df.index)
    df["ut_trail"] = xATR
    df["ut_pos"]   = pos_s
    df["ut_buy"]   = ((src > xATR) & above) | ((pos_s == 1) & (pos_s.shift(1) != 1))
    df["ut_sell"]  = ((src < xATR) & below) | ((pos_s == -1) & (pos_s.shift(1) != -1))

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
    win = 2 * prd + 1

    # Rolling max/min over the full symmetric window, then shift back by prd
    # so the result at position i reflects the window [i-prd, i+prd].
    roll_max = high_src.rolling(win, center=True).max()
    roll_min = low_src.rolling(win,  center=True).min()

    # A bar is a pivot high/low when it equals the window extreme
    ph_mask = high_src == roll_max
    pl_mask = low_src  == roll_min

    # Convert to lists of (bar_index, value), excluding the edge bars with NaN windows
    pivot_highs = [
        (i, float(high_src.iloc[i]))
        for i in range(prd, len(high_src) - prd)
        if ph_mask.iloc[i]
    ]
    pivot_lows = [
        (i, float(low_src.iloc[i]))
        for i in range(prd, len(low_src) - prd)
        if pl_mask.iloc[i]
    ]

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

    # Fully vectorised: np.histogram accumulates volume per bin in one pass
    vol_profile, bin_edges = np.histogram(
        typical_price, bins=bins, range=(min_p, max_p), weights=df["volume"].values
    )
    poc_bin_idx = int(np.argmax(vol_profile))
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
    open_v    = df["open"]

    # For bars whose close is inside a zone, we can't call it "support" OR
    # "resistance" purely from proximity — the candle direction disambiguates:
    #   • Green candle (close > open) inside the zone  ⇒ likely support bounce ⇒ BUY
    #   • Red candle   (close < open) inside the zone  ⇒ likely resistance rejection ⇒ SELL
    #   • Doji (close == open) inside zone             ⇒ ambiguous; skip both
    # This prevents the SR engine from firing BUY and SELL on the same bar,
    # which downstream turns into a "signal conflict" that the composite
    # scorer treats as noise.
    bar_is_bull = close_v > open_v
    bar_is_bear = close_v < open_v

    for zone_hi, zone_lo, _strength in zones:
        prox = close_v * proximity_pct / 100.0

        # Price is INSIDE the zone — disambiguate by candle direction
        inside      = (close_v >= zone_lo) & (close_v <= zone_hi)
        sr_buy      = sr_buy  | (inside & bar_is_bull)
        sr_sell     = sr_sell | (inside & bar_is_bear)

        # Zone is BELOW price → Support; buy if price is within proximity
        # above the zone top (zone_hi < close and close - zone_hi <= prox)
        below_near = (zone_hi < close_v) & ((close_v - zone_hi) <= prox)
        sr_buy     = sr_buy | below_near

        # Zone is ABOVE price → Resistance; sell if price is within proximity
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
# 6. MOMENTUM ENGINE — Trend Continuation Detection
# ============================================================================

def compute_momentum_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Momentum Engine: Multi-factor trend continuation detection.
    
    Returns DataFrame with momentum_buy, momentum_sell, momentum_score_buy/sell columns.
    """
    df = df.copy()
    n = len(df)
    buy_score = np.zeros(n)
    sell_score = np.zeros(n)
    
    # RSI Component
    if cfg.get("rsi_enabled", True):
        rsi_period = cfg.get("rsi_period", 14)
        rsi_buy_zone = cfg.get("rsi_buy_zone", [40, 70])
        rsi_sell_zone = cfg.get("rsi_sell_zone", [30, 60])
        rsi_weight = cfg.get("rsi_weight", 20)
        
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1.0/rsi_period, adjust=False).mean()
        loss = -delta.where(delta < 0, 0).ewm(alpha=1.0/rsi_period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        df["momentum_rsi"] = rsi
        
        buy_score += np.where((rsi >= rsi_buy_zone[0]) & (rsi <= rsi_buy_zone[1]), rsi_weight, 0)
        sell_score += np.where((rsi >= rsi_sell_zone[0]) & (rsi <= rsi_sell_zone[1]), rsi_weight, 0)
    
    # Volume Component
    if cfg.get("volume_enabled", True) and "volume" in df.columns:
        vol_sma_period = cfg.get("volume_sma_period", 20)
        vol_surge_min = cfg.get("volume_surge_min", 1.5)
        vol_weight = cfg.get("volume_weight", 20)
        
        vol_sma = df["volume"].rolling(vol_sma_period).mean()
        vol_ratio = df["volume"] / (vol_sma + 1e-10)
        df["momentum_vol_ratio"] = vol_ratio
        
        vol_pts = np.where(vol_ratio >= vol_surge_min, vol_weight, 0)
        buy_score += vol_pts
        sell_score += vol_pts
    
    # ADX Component
    if cfg.get("adx_enabled", True):
        adx_period = cfg.get("adx_period", 14)
        adx_min = cfg.get("adx_min_threshold", 20.0)
        adx_strong = cfg.get("adx_strong_threshold", 25.0)
        adx_weight = cfg.get("adx_weight", 15)
        
        adx, plus_di, minus_di = compute_adx(df, period=adx_period)
        df["momentum_adx"] = adx
        df["momentum_plus_di"] = plus_di
        df["momentum_minus_di"] = minus_di
        
        adx_pts = np.where(adx >= adx_strong, adx_weight, np.where(adx >= adx_min, adx_weight * 0.6, 0))
        buy_score += np.where(plus_di > minus_di, adx_pts, 0)
        sell_score += np.where(minus_di > plus_di, adx_pts, 0)
    
    # EMA Component
    if cfg.get("ema_enabled", True):
        ema_period = cfg.get("ema_period", 200)
        ema_weight = cfg.get("ema_weight", 20)
        
        ema = df["close"].ewm(span=ema_period, adjust=False).mean()
        df["momentum_ema"] = ema
        
        buy_score += np.where(df["close"] > ema, ema_weight, 0)
        sell_score += np.where(df["close"] < ema, ema_weight, 0)
    
    # Bollinger Bands Component
    if cfg.get("bb_enabled", True):
        bb_period = cfg.get("bb_period", 20)
        bb_std = cfg.get("bb_std_dev", 2.0)
        bb_weight = cfg.get("bb_weight", 15)
        
        sma = df["close"].rolling(bb_period).mean()
        std = df["close"].rolling(bb_period).std()
        bb_upper = sma + (bb_std * std)
        bb_lower = sma - (bb_std * std)
        bb_width = (bb_upper - bb_lower) / (sma + 1e-10) * 100
        
        df["momentum_bb_upper"] = bb_upper
        df["momentum_bb_lower"] = bb_lower
        df["momentum_bb_width"] = bb_width
        
        bb_expanding = bb_width > bb_width.shift(1)
        buy_score += np.where((df["close"] > bb_upper) | (bb_expanding & (df["close"] > sma)), bb_weight, 0)
        sell_score += np.where((df["close"] < bb_lower) | (bb_expanding & (df["close"] < sma)), bb_weight, 0)
    
    # ROC Component
    if cfg.get("roc_enabled", True):
        roc_period = cfg.get("roc_period", 10)
        roc_buy_thresh = cfg.get("roc_buy_threshold", 3.0)
        roc_sell_thresh = cfg.get("roc_sell_threshold", -3.0)
        roc_weight = cfg.get("roc_weight", 25)
        
        roc = ((df["close"] - df["close"].shift(roc_period)) / (df["close"].shift(roc_period) + 1e-10)) * 100
        df["momentum_roc"] = roc
        
        buy_score += np.where(roc >= roc_buy_thresh * 1.5, roc_weight, np.where(roc >= roc_buy_thresh, roc_weight * 0.7, 0))
        sell_score += np.where(roc <= roc_sell_thresh * 1.5, roc_weight, np.where(roc <= roc_sell_thresh, roc_weight * 0.7, 0))
    
    # Final scoring
    df["momentum_score_buy"] = buy_score
    df["momentum_score_sell"] = sell_score
    min_score = cfg.get("min_momentum_score", 70)
    df["momentum_buy"] = buy_score >= min_score
    df["momentum_sell"] = sell_score >= min_score
    
    return df


# ============================================================================
# 7. MEAN REVERSION ENGINE — Oversold/Overbought Bounce Detection
# ============================================================================

def compute_mean_reversion_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Mean Reversion Engine: Detects stretched price moves likely to snap back.
    
    Returns DataFrame with mr_buy, mr_sell, mr_score_buy/sell columns.
    """
    df = df.copy()
    n = len(df)
    buy_score = np.zeros(n)
    sell_score = np.zeros(n)
    
    # BB Touch Component
    if cfg.get("bb_enabled", True):
        bb_period = cfg.get("bb_period", 20)
        bb_std = cfg.get("bb_std_dev", 2.0)
        bb_weight = cfg.get("bb_weight", 25)
        bb_touch_thresh = cfg.get("bb_touch_threshold", 0.02)
        
        sma = df["close"].rolling(bb_period).mean()
        std = df["close"].rolling(bb_period).std()
        bb_upper = sma + (bb_std * std)
        bb_lower = sma - (bb_std * std)
        
        df["mr_bb_upper"] = bb_upper
        df["mr_bb_lower"] = bb_lower
        
        lower_touch = (df["close"] - bb_lower) / (bb_lower + 1e-10) <= bb_touch_thresh
        upper_touch = (bb_upper - df["close"]) / (bb_upper + 1e-10) <= bb_touch_thresh
        
        buy_score += np.where(lower_touch, bb_weight, 0)
        sell_score += np.where(upper_touch, bb_weight, 0)
    
    # RSI Extremes Component
    if cfg.get("rsi_extreme_enabled", True):
        rsi_extreme_period = cfg.get("rsi_extreme_period", 14)
        rsi_oversold = cfg.get("rsi_oversold", 30)
        rsi_overbought = cfg.get("rsi_overbought", 70)
        rsi_extreme_weight = cfg.get("rsi_extreme_weight", 20)
        
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1.0/rsi_extreme_period, adjust=False).mean()
        loss = -delta.where(delta < 0, 0).ewm(alpha=1.0/rsi_extreme_period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        df["mr_rsi"] = rsi
        
        buy_score += np.where(rsi < rsi_oversold, rsi_extreme_weight, 0)
        sell_score += np.where(rsi > rsi_overbought, rsi_extreme_weight, 0)
    
    # Stochastic Component
    if cfg.get("stochastic_enabled", True):
        stoch_k_period = cfg.get("stoch_k_period", 14)
        stoch_d_period = cfg.get("stoch_d_period", 3)
        stoch_smooth_k = cfg.get("stoch_smooth_k", 3)
        stoch_oversold = cfg.get("stoch_oversold", 20)
        stoch_overbought = cfg.get("stoch_overbought", 80)
        stoch_weight = cfg.get("stoch_weight", 15)
        
        low_min = df["low"].rolling(stoch_k_period).min()
        high_max = df["high"].rolling(stoch_k_period).max()
        stoch_k_raw = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-10)
        stoch_k = stoch_k_raw.rolling(stoch_smooth_k).mean()
        stoch_d = stoch_k.rolling(stoch_d_period).mean()
        
        df["mr_stoch_k"] = stoch_k
        df["mr_stoch_d"] = stoch_d
        
        buy_score += np.where(stoch_k < stoch_oversold, stoch_weight, 0)
        sell_score += np.where(stoch_k > stoch_overbought, stoch_weight, 0)
    
    # Z-Score Component
    if cfg.get("zscore_enabled", True):
        zscore_period = cfg.get("zscore_period", 20)
        zscore_buy_thresh = cfg.get("zscore_buy_threshold", -2.0)
        zscore_sell_thresh = cfg.get("zscore_sell_threshold", 2.0)
        zscore_weight = cfg.get("zscore_weight", 25)
        
        mean = df["close"].rolling(zscore_period).mean()
        std = df["close"].rolling(zscore_period).std()
        zscore = (df["close"] - mean) / (std + 1e-10)
        df["mr_zscore"] = zscore
        
        buy_score += np.where(zscore < zscore_buy_thresh, zscore_weight, 0)
        sell_score += np.where(zscore > zscore_sell_thresh, zscore_weight, 0)
    
    # Volume Climax Component
    if cfg.get("vol_climax_enabled", True) and "volume" in df.columns:
        vol_climax_period = cfg.get("vol_climax_period", 20)
        vol_climax_thresh = cfg.get("vol_climax_threshold", 2.5)
        vol_climax_weight = cfg.get("vol_climax_weight", 15)
        
        vol_sma = df["volume"].rolling(vol_climax_period).mean()
        vol_ratio = df["volume"] / (vol_sma + 1e-10)
        df["mr_vol_ratio"] = vol_ratio
        
        climax = vol_ratio >= vol_climax_thresh
        price_pct = (df["close"] - df["close"].rolling(20).min()) / (df["close"].rolling(20).max() - df["close"].rolling(20).min() + 1e-10)
        
        buy_score += np.where(climax & (price_pct < 0.3), vol_climax_weight, 0)
        sell_score += np.where(climax & (price_pct > 0.7), vol_climax_weight, 0)
    
    # RSI Divergence Component (optimized vectorized version)
    if cfg.get("rsi_div_enabled", True):
        rsi_div_weight = cfg.get("rsi_div_weight", 30)
        rsi_div_lookback = cfg.get("rsi_div_lookback", 15)
        
        if "mr_rsi" not in df.columns:
            rsi_div_period = cfg.get("rsi_div_period", 14)
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).ewm(alpha=1.0/rsi_div_period, adjust=False).mean()
            loss = -delta.where(delta < 0, 0).ewm(alpha=1.0/rsi_div_period, adjust=False).mean()
            rs = gain / (loss + 1e-10)
            df["mr_rsi"] = 100 - (100 / (1 + rs))
        
        # Vectorized divergence detection
        price_shift = df["close"].shift(rsi_div_lookback)
        rsi_shift = df["mr_rsi"].shift(rsi_div_lookback)
        
        # Bullish divergence: price lower, RSI higher
        bullish_div = (df["close"] < price_shift) & (df["mr_rsi"] > rsi_shift)
        buy_score += np.where(bullish_div, rsi_div_weight, 0)
        
        # Bearish divergence: price higher, RSI lower
        bearish_div = (df["close"] > price_shift) & (df["mr_rsi"] < rsi_shift)
        sell_score += np.where(bearish_div, rsi_div_weight, 0)
    
    # Final scoring
    df["mr_score_buy"] = buy_score
    df["mr_score_sell"] = sell_score
    min_score = cfg.get("min_mr_score", 70)
    df["mr_buy"] = buy_score >= min_score
    df["mr_sell"] = sell_score >= min_score
    
    return df


# ============================================================================
# 6. ATR-BASED RISK/REWARD CALCULATOR
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
# 7. MULTI-TIMEFRAME CONFIRMATION
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
# 8. COMPOSITE SIGNAL EVALUATOR
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
    df = df.copy()
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

    # ---- Optional closed-candle-only mode ----------------------------------
    # If ``strategy.signal_on_closed_bar`` is truthy in config, drop the last
    # (possibly still-forming) row before evaluating engine signals. Default
    # is False for backwards compat — the scanner has always looked at the
    # running bar, which lets users see intraday-forming signals but can also
    # cause a signal to disappear later if the bar reverses before it closes.
    eval_df = df
    strat = config.get("strategy", {})
    if bool(strat.get("signal_on_closed_bar", False)) and len(df) >= 2:
        if _is_last_candle_incomplete(df, config):
            eval_df = df.iloc[:-1]

    # ---- Combine based on enabled engines ----------------------------------
    # Generic N-engine AND combination using ENGINE_REGISTRY.
    # Only enabled engines participate; all enabled engines must agree.
    triggered_buy  = []
    triggered_sell = []
    
    # Step 1: Build active engine list
    active_engines = []
    for engine in ENGINE_REGISTRY:
        cfg_section = config.get(engine["config_section"], {})
        default_on = engine.get("default_enabled", True)
        is_enabled = bool(cfg_section.get(engine["enabled_key"], default_on))
        if is_enabled:
            active_engines.append(engine)
    
    # Step 2: Evaluate each active engine
    if not active_engines:
        # No engines enabled → no signals
        composite_buy = False
        composite_sell = False
    else:
        # All active engines must agree (AND logic)
        buy_votes = []
        sell_votes = []
        
        for engine in active_engines:
            buy_col = engine["buy_col"]
            sell_col = engine["sell_col"]
            eval_mode = engine["eval_mode"]
            
            # Evaluate engine signal based on its mode
            if eval_mode == "window":
                # Window mode: check last N candles with most-recent-wins reducer
                # (Currently only UT Bot uses this)
                engine_buy = False
                engine_sell = False
                
                if buy_col in eval_df.columns:
                    tail = eval_df.tail(lookback_candles)
                    engine_buy = bool(tail[buy_col].any())
                    engine_sell = bool(tail[sell_col].any())
                    
                    # Most-recent-wins conflict resolution
                    if engine_buy and engine_sell:
                        buy_positions = np.where(tail[buy_col].values)[0]
                        sell_positions = np.where(tail[sell_col].values)[0]
                        last_buy_idx = int(buy_positions[-1]) if len(buy_positions) else -1
                        last_sell_idx = int(sell_positions[-1]) if len(sell_positions) else -1
                        if last_sell_idx > last_buy_idx:
                            engine_buy = False
                        elif last_buy_idx > last_sell_idx:
                            engine_sell = False
                
                buy_votes.append(engine_buy)
                sell_votes.append(engine_sell)
                
                if engine_buy:
                    triggered_buy.append(engine["buy_label"])
                if engine_sell:
                    triggered_sell.append(engine["sell_label"])
                    
            elif eval_mode == "instant":
                # Instant mode: check only the last candle
                # (Currently only S/R uses this)
                engine_buy = False
                engine_sell = False
                
                if buy_col in eval_df.columns and len(eval_df) > 0:
                    engine_buy = bool(eval_df[buy_col].iloc[-1])
                    engine_sell = bool(eval_df[sell_col].iloc[-1])
                
                buy_votes.append(engine_buy)
                sell_votes.append(engine_sell)
                
                if engine_buy:
                    triggered_buy.append(engine["buy_label"])
                if engine_sell:
                    triggered_sell.append(engine["sell_label"])
            else:
                # Unknown eval_mode — treat as disabled
                log.warning(f"Unknown eval_mode '{eval_mode}' for engine {engine['key']}")
                continue
        
        # Composite signal = ALL active engines agree
        composite_buy = all(buy_votes) if buy_votes else False
        composite_sell = all(sell_votes) if sell_votes else False

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

        # 1. S/R Zone Strength & Proximity — single pass over zones, updating
        # both BUY and SELL best candidates simultaneously.
        if zones:
            prox_cfg = config.get("sr_channels", {}).get("proximity_pct", 0.5)
            max_zone_strength = max(s for _, _, s in zones) if zones else 1

            best_buy_zone_pts    = 0.0
            best_buy_zone_reason = ""
            best_sell_zone_pts   = 0.0
            best_sell_zone_reason = ""

            for zone_hi, zone_lo, zone_strength in zones:
                inside     = (close_price >= zone_lo) and (close_price <= zone_hi)
                above_near = (zone_hi < close_price) and ((close_price - zone_hi) <= (close_price * prox_cfg / 100.0))
                below_near = (zone_lo > close_price) and ((zone_lo - close_price) <= (close_price * prox_cfg / 100.0))
                str_pts    = 10.0 + 20.0 * (zone_strength / max_zone_strength)

                # BUY side (support: price inside or just above zone)
                if inside or above_near:
                    if inside:
                        prox_pts = 15.0
                    elif prox_cfg > 0:
                        dist_pct = ((close_price - zone_hi) / close_price) * 100.0
                        prox_pts = 15.0 * max(0.0, 1.0 - dist_pct / prox_cfg)
                    else:
                        prox_pts = 15.0
                    zone_pts = round(prox_pts + str_pts, 1)
                    if zone_pts > best_buy_zone_pts:
                        best_buy_zone_pts   = zone_pts
                        best_buy_zone_reason = f"Bouncing inside/near Support (strength {zone_strength}) (+{zone_pts:.1f} pts)"

                # SELL side (resistance: price inside or just below zone)
                if inside or below_near:
                    if inside:
                        prox_pts = 15.0
                    elif prox_cfg > 0:
                        dist_pct = ((zone_lo - close_price) / close_price) * 100.0
                        prox_pts = 15.0 * max(0.0, 1.0 - dist_pct / prox_cfg)
                    else:
                        prox_pts = 15.0
                    zone_pts = round(prox_pts + str_pts, 1)
                    if zone_pts > best_sell_zone_pts:
                        best_sell_zone_pts   = zone_pts
                        best_sell_zone_reason = f"Rejecting inside/near Resistance (strength {zone_strength}) (+{zone_pts:.1f} pts)"

            if best_buy_zone_pts > 0:
                buy_score += best_buy_zone_pts
                buy_reasons.append(best_buy_zone_reason)
            if best_sell_zone_pts > 0:
                sell_score += best_sell_zone_pts
                sell_reasons.append(best_sell_zone_reason)

        # 2. Volume Spike Confirmation - DISABLED (use momentum.volume_enabled instead)
        # This scoring was previously used for UT Bot & S/R signals but is now
        # redundant with the Momentum engine's volume component.

        # 3. EMA Trend Confluence - DISABLED (use momentum.ema_enabled instead)
        # This scoring was previously used for UT Bot & S/R signals but is now
        # redundant with the Momentum engine's EMA component.
        # ema_above is left as None for backward compatibility.
        ema_above = None   # Legacy: no longer computed for UT Bot/S/R

        # 4. RSI Momentum Confluence - DISABLED (use momentum.rsi_enabled instead)
        # This scoring was previously used for UT Bot & S/R signals but is now
        # redundant with the Momentum engine's RSI component.
        # rsi_ok is left as None for backward compatibility.
        rsi_ok = None   # Legacy: no longer computed for UT Bot/S/R

        # 4.5 RSI Divergence (up to 15 pts)
        divs = detect_rsi_divergence(df, lookback=15)
        if divs.get("bullish_div"):
            buy_score += 15.0
            buy_reasons.append("Bullish RSI Divergence (+15.0 pts)")
            triggered_buy.append("Bullish Divergence")
        if divs.get("bearish_div"):
            sell_score += 15.0
            sell_reasons.append("Bearish RSI Divergence (+15.0 pts)")
            triggered_sell.append("Bearish Divergence")

        # 5. ADX Trend Strength - DISABLED (use momentum.adx_enabled instead)
        # This scoring was previously used for UT Bot & S/R signals but is now
        # redundant with the Momentum engine's ADX component.
        # adx_ok is left as None for backward compatibility.
        adx_ok = None   # Legacy: no longer computed for UT Bot/S/R

        # 5.5 Volatility Squeeze Release - DISABLED (covered by momentum.bb_enabled)
        # This feature is now covered by the Momentum engine's BB breakout detection.
        # sqz_ok is left as None for backward compatibility.
        sqz_ok = None   # Legacy: no longer computed for UT Bot/S/R

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

    # ---- Hard Filters REMOVED -----------------------------------------------
    # All hard filters (EMA, Volume, ADX, RSI, Squeeze) have been removed.
    # Use engine-specific components instead (momentum.* or mean_reversion.*).
    # Legacy status variables are left as None for backward compatibility.
    vol_ok = None   # Legacy: was volume filter pass/fail

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
