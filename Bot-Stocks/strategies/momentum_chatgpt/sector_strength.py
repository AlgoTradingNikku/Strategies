"""
Sector Strength Analytics for Momentum-ChatGPT strategy.
Ranks sectors based on 5D/20D/60D momentum, relative strength vs benchmark, trend, and breadth.
"""

from typing import Dict, Any, List
import pandas as pd
import numpy as np


# Default sector mapping for NSE India
DEFAULT_SECTOR_INDICES = {
    "NIFTY IT": "^CNXIT",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY PSU BANK": "^CNXPSU",
    "NIFTY FIN SERVICE": "NIFTY_FIN_SERVICE.NS",
    "NIFTY MEDIA": "^CNXMEDIA",
}


def compute_sector_score(
    sector_df: pd.DataFrame,
    benchmark_df: pd.DataFrame = None
) -> Dict[str, Any]:
    """
    Calculate sector strength score (0-100) based on momentum & relative strength.
    """
    if sector_df is None or len(sector_df) < 60:
        return {"score": 50.0, "rank": "NEUTRAL", "return_20d": 0.0}

    close = sector_df['close']
    c_curr = close.iloc[-1]

    ret5 = ((c_curr - close.iloc[-5]) / close.iloc[-5]) * 100 if len(close) >= 5 else 0.0
    ret20 = ((c_curr - close.iloc[-20]) / close.iloc[-20]) * 100 if len(close) >= 20 else 0.0
    ret60 = ((c_curr - close.iloc[-60]) / close.iloc[-60]) * 100 if len(close) >= 60 else 0.0

    # Benchmark return
    bm_ret20 = 0.0
    if benchmark_df is not None and len(benchmark_df) >= 20:
        bm_close = benchmark_df['close']
        bm_ret20 = ((bm_close.iloc[-1] - bm_close.iloc[-20]) / bm_close.iloc[-20]) * 100

    rs_spread = ret20 - bm_ret20

    # Trend check
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    trend_score = 100.0 if (c_curr > ema20 > ema50) else (50.0 if c_curr > ema50 else 0.0)

    # Score components (normalized to 0-100 scale)
    score_ret5 = np.clip((ret5 + 5.0) * 10, 0, 100)
    score_ret20 = np.clip((ret20 + 10.0) * 5, 0, 100)
    score_ret60 = np.clip((ret60 + 15.0) * 3.33, 0, 100)
    score_rs = np.clip((rs_spread + 10.0) * 5, 0, 100)

    composite_score = (
        0.20 * score_ret5 +
        0.25 * score_ret20 +
        0.20 * score_ret60 +
        0.25 * score_rs +
        0.10 * trend_score
    )

    return {
        "score": round(float(composite_score), 2),
        "return_5d": round(float(ret5), 2),
        "return_20d": round(float(ret20), 2),
        "return_60d": round(float(ret60), 2),
        "rs_spread_20d": round(float(rs_spread), 2),
        "is_uptrend": bool(c_curr > ema50),
    }


def rank_sectors(
    sector_dfs: Dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame = None
) -> Dict[str, Dict[str, Any]]:
    """Rank all sectors by score."""
    scores = {}
    for sector_name, df in sector_dfs.items():
        scores[sector_name] = compute_sector_score(df, benchmark_df)

    # Sort and add ranking
    sorted_sectors = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    ranked_dict = {}
    for idx, (sec_name, sec_data) in enumerate(sorted_sectors, start=1):
        sec_data["rank"] = idx
        ranked_dict[sec_name] = sec_data

    return ranked_dict
