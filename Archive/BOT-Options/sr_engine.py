import numpy as np
import pandas as pd

def _find_pivots(
    high_src: pd.Series,
    low_src: pd.Series,
    prd: int,
):
    """
    Find pivot highs and lows — equivalent to Pine's ta.pivothigh / ta.pivotlow.
    """
    win = 2 * prd + 1

    roll_max = high_src.rolling(win, center=True).max()
    roll_min = low_src.rolling(win,  center=True).min()

    ph_mask = high_src == roll_max
    pl_mask = low_src  == roll_min

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
    """
    num_pivots = len(pivot_values)
    if num_pivots == 0:
        return []

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

        if poc_price > 0 and (z_lo <= poc_price <= z_hi):
            z[0] *= 2.0

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

        for z in raw_zones:
            z_hi, z_lo = z[1], z[2]
            if (sel_lo <= z_hi <= sel_hi) or (sel_lo <= z_lo <= sel_hi):
                z[0] = -1

    selected.sort(key=lambda x: -x[2])
    return [(hi, lo, strength) for hi, lo, strength in selected]


def compute_sr_signals(
    df: pd.DataFrame,
    pivot_period: int = 10,
    source: str = "High/Low",
    channel_width_pct: float = 5.0,
    min_strength: int = 1,
    max_num_sr: int = 6,
    loopback: int = 290,
    proximity_pct: float = 0.5,
) -> tuple:
    """
    Compute Support/Resistance channel signals.
    """
    df = df.copy()
    n  = len(df)

    if n < pivot_period * 2 + 1:
        df["sr_buy"]  = False
        df["sr_sell"] = False
        zones: list   = []
        return df, zones

    if source == "High/Low":
        high_src = df["high"]
        low_src  = df["low"]
    else:  # "Close/Open"
        high_src = df[["close", "open"]].max(axis=1)
        low_src  = df[["close", "open"]].min(axis=1)

    pivot_highs, pivot_lows = _find_pivots(high_src, low_src, pivot_period)

    last_bar_idx = n - 1
    cutoff_idx   = last_bar_idx - loopback

    all_pivots = []
    for bar_idx, val in pivot_highs:
        if bar_idx > cutoff_idx:
            all_pivots.append((bar_idx, val))
    for bar_idx, val in pivot_lows:
        if bar_idx > cutoff_idx:
            all_pivots.append((bar_idx, val))

    all_pivots.sort(key=lambda x: -x[0])
    pivot_values = [p[1] for p in all_pivots]

    window_size = min(300, n)
    high_300    = df["high"].iloc[-window_size:].max()
    low_300     = df["low"].iloc[-window_size:].min()
    cwidth      = (high_300 - low_300) * channel_width_pct / 100

    if cwidth <= 0 or not pivot_values:
        df["sr_buy"]  = False
        df["sr_sell"] = False
        zones: list   = []
        return df, zones

    start_idx = max(0, last_bar_idx - loopback)
    loopback_df = df.iloc[start_idx:last_bar_idx + 1]
    poc = compute_vpvr_poc(loopback_df, bins=50)

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

    sr_buy    = pd.Series(False, index=df.index)
    sr_sell   = pd.Series(False, index=df.index)
    close_v   = df["close"]

    for zone_hi, zone_lo, _strength in zones:
        prox = close_v * proximity_pct / 100.0

        inside   = (close_v >= zone_lo) & (close_v <= zone_hi)
        sr_buy   = sr_buy  | inside
        sr_sell  = sr_sell | inside

        below_near = (zone_hi < close_v) & ((close_v - zone_hi) <= prox)
        sr_buy     = sr_buy | below_near

        above_near = (zone_lo > close_v) & ((zone_lo - close_v) <= prox)
        sr_sell    = sr_sell | above_near

    df["sr_buy"]  = sr_buy
    df["sr_sell"] = sr_sell

    return df, zones
