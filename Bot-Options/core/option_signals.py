"""
===============================================================================
  Bot-Options / core / option_signals.py
  Stage 1 (Underlying Signal Scan) and Stage 3 (Option Chart Confirmation)
  signal evaluation engine.
  Re-uses Bot-Stocks/signals.py calculations via sys.path cross-importing.
===============================================================================
"""

import sys
import logging
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)

# Add Bot-Stocks directory to path to import shared logic
bot_stocks_dir = Path(__file__).resolve().parents[2] / "Bot-Stocks"
if str(bot_stocks_dir) not in sys.path:
    sys.path.insert(0, str(bot_stocks_dir))

# Cross-imports from Bot-Stocks
try:
    from signals import compute_utbot_signals, compute_sr_signals, evaluate_composite_signals
    from scanner import fetch_history as stocks_fetch_history
except ImportError as e:
    log.error("Failed to import shared modules from Bot-Stocks: %s", e)
    raise

def get_mapped_underlying_symbol(underlying: str, source: str) -> str:
    """Map user-friendly underlying name to data source specific ticker."""
    source_lower = source.lower()
    if source_lower == "yfinance":
        if underlying == "NIFTY":
            return "^NSEI"
        elif underlying == "BANKNIFTY":
            return "^NSEBANK"
    return underlying


def fetch_underlying_ohlcv(
    underlying: str,
    timeframe: str,
    config: dict
) -> Optional[pd.DataFrame]:
    """Fetch history for the underlying index."""
    source = config.get("underlying_data_source", "yfinance")
    symbol = get_mapped_underlying_symbol(underlying, source)
    
    # Construct a temp config block for stocks_fetch_history to override data_source and exchange
    temp_cfg = config.copy()
    temp_cfg["data_source"] = source
    temp_cfg["exchange"] = "NSE" if source.lower() == "yfinance" else "NSE_INDEX"
    
    return stocks_fetch_history(symbol, timeframe, temp_cfg)


def evaluate_underlying_signals(
    underlying: str,
    timeframe: str,
    config: dict
) -> List[Dict[str, Any]]:
    """
    Stage 1: Scan underlying index chart using UTBot and SR.
    Returns a list of signal dicts containing underlying signal information.
    """
    df = fetch_underlying_ohlcv(underlying, timeframe, config)
    if df is None or len(df) < 20:
        log.warning("[%s] Insufficient underlying data to scan.", underlying)
        return []

    strat = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    lookback = int(config.get("signal_lookback_candles", 2))

    # Calculate indicators
    if strat.get("ut_enabled", True):
        df = compute_utbot_signals(
            df,
            key_value=float(strat.get("key_value", 1.0)),
            atr_period=int(strat.get("atr_period", 2)),
            use_heikin_ashi=bool(strat.get("use_heikin_ashi", False))
        )

    sr_zones = []
    if sr_cfg.get("enabled", True):
        df, sr_zones = compute_sr_signals(
            df,
            pivot_period=int(sr_cfg.get("pivot_period", 10)),
            source=sr_cfg.get("source", "High/Low"),
            channel_width_pct=float(sr_cfg.get("channel_width_pct", 5.0)),
            min_strength=int(sr_cfg.get("min_strength", 1)),
            max_num_sr=int(sr_cfg.get("max_num_sr", 6)),
            loopback=int(sr_cfg.get("loopback", 290)),
            proximity_pct=float(sr_cfg.get("proximity_pct", 0.2))
        )

    # Evaluate composite signals
    composite = evaluate_composite_signals(df, config, lookback, sr_zones=sr_zones)
    
    results = []
    last_row = df.iloc[-1]
    close_price = float(last_row["close"])

    # We construct a base result structure
    # _df and _sr_zones are passed through so downstream filters (e.g. candle patterns)
    # can use the underlying chart data without re-fetching it.
    base_info = {
        "underlying": underlying,
        "underlying_close": close_price,
        "signal_time": df.index[-1].strftime("%Y-%m-%d %H:%M:%S") if hasattr(df.index[-1], "strftime") else str(df.index[-1]),
        "underlying_score": 0.0,
        "triggered_engines": [],
        "_df": df,
        "_sr_zones": sr_zones,
    }

    # Underlying BUY signal -> Bullish, target calls (CE)
    if composite.get("buy"):
        res = base_info.copy()
        res.update({
            "direction": "BUY",  # translates to CE
            "option_type": "CE",
            "underlying_score": float(composite.get("buy_score", 0.0)),
            "triggered_engines": composite.get("triggered_buy", []),
            "reasons": list(composite.get("buy_reasons", []))
        })
        results.append(res)

    # Underlying SELL signal -> Bearish, target puts (PE)
    if composite.get("sell"):
        res = base_info.copy()
        res.update({
            "direction": "SELL",  # translates to PE
            "option_type": "PE",
            "underlying_score": float(composite.get("sell_score", 0.0)),
            "triggered_engines": composite.get("triggered_sell", []),
            "reasons": list(composite.get("sell_reasons", []))
        })
        results.append(res)

    return results


def evaluate_option_chart_confirmation(
    option_symbol: str,
    timeframe: str,
    config: dict,
    oa_client
) -> dict[str, Any]:
    """
    Stage 3: Run UTBot on the option symbol's premium chart to confirm the trade.
    Option contract exchange is NFO.
    """
    conf_cfg = config.get("option_chart_confirmation", {})
    if not conf_cfg.get("enabled", True):
        return {
            "status": "skipped",
            "score_adjustment": 0.0,
            "reasons": ["Stage 3 confirmation disabled in config"]
        }

    # Fetch option contract history from OpenAlgo
    temp_cfg = config.copy()
    temp_cfg["data_source"] = "openalgo"
    temp_cfg["exchange"] = "NFO"
    
    df = stocks_fetch_history(option_symbol, timeframe, temp_cfg)
    if df is None or len(df) < 10:
        log.warning("[%s] Insufficient option premium history for Stage 3 scan.", option_symbol)
        return {
            "status": "no_data",
            "score_adjustment": 0.0,
            "reasons": [f"Insufficient option premium history for symbol {option_symbol}"]
        }

    # Run UTBot with Stage 3 specific settings
    df = compute_utbot_signals(
        df,
        key_value=float(conf_cfg.get("key_value", 1.5)),
        atr_period=int(conf_cfg.get("atr_period", 3)),
        use_heikin_ashi=False
    )

    last_row = df.iloc[-1]
    # Check if the option premium is in a bullish regime (ut_pos == 1) or has a recent buy signal
    # Since we are buying options, confirmation is always looking for a long (bullish) premium direction
    is_confirmed = False
    
    # Check current position or any UTBot buy signal in the last 2 candles
    recent_candles = df.tail(2)
    has_buy_signal = bool(recent_candles["ut_buy"].any())
    is_bullish_trend = int(last_row.get("ut_pos", 0)) == 1

    if has_buy_signal or is_bullish_trend:
        is_confirmed = True

    bonus = float(conf_cfg.get("confirmation_bonus_pts", 15.0))
    penalty = float(conf_cfg.get("contradiction_penalty_pts", 15.0))
    mode = conf_cfg.get("mode", "score_only")

    if is_confirmed:
        return {
            "status": "confirmed",
            "score_adjustment": bonus,
            "reasons": [f"Stage 3 option premium chart confirmed bullish trend (+{bonus} pts)"]
        }
    else:
        # Contradicted
        score_adj = -penalty if mode == "score_only" else -100.0  # -100 to fail score check if strict
        return {
            "status": "contradicted",
            "score_adjustment": score_adj,
            "reasons": [f"Stage 3 option premium chart contradicts (trend is bearish/neutral) ({score_adj} pts)"]
        }
