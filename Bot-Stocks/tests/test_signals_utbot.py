"""
tests/test_signals_utbot.py
===========================
Sanity tests for the UTBot signal port (signals.compute_utbot_signals).

These are NOT bar-by-bar Pine Script parity tests (which would need golden
data captured from TradingView); they are guardrail tests that assert:

  1. The function returns the required output columns.
  2. Trailing-stop is monotonic within a trend (stays put when price stalls).
  3. A synthetic sharp reversal triggers a signal flip.
  4. Empty / too-short DataFrames don't crash.

Run:
    pytest tests/test_signals_utbot.py -q
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from signals import compute_utbot_signals, evaluate_composite_signals


def _synthetic_ohlc(closes) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    idx = pd.date_range(start="2024-01-01 09:15", periods=len(closes), freq="5min")
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({
        "open":   closes,
        "high":   closes * 1.001,
        "low":    closes * 0.999,
        "close":  closes,
        "volume": np.full_like(closes, 10000, dtype=float),
    }, index=idx)
    return df


def test_output_columns_present():
    df = _synthetic_ohlc(list(range(100, 200)))
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    for col in ("ut_trail", "ut_buy", "ut_sell", "ut_pos"):
        assert col in out.columns, f"Expected column '{col}' missing from UTBot output"


def test_uptrend_generates_buy_position():
    """A sustained uptrend should leave ut_pos at +1 in the final rows."""
    closes = list(range(100, 200))   # monotone up
    df = _synthetic_ohlc(closes)
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    # Once the trailing stop has locked below price, ut_pos should be +1
    assert out["ut_pos"].iloc[-1] == 1

    # And there should be at least one BUY flip somewhere in the series
    assert int(out["ut_buy"].sum()) >= 1


def test_downtrend_generates_sell_position():
    closes = list(range(200, 100, -1))
    df = _synthetic_ohlc(closes)
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    assert out["ut_pos"].iloc[-1] == -1
    assert int(out["ut_sell"].sum()) >= 1


def test_trailing_stop_monotonic_in_uptrend():
    """Within a clean uptrend, the trailing stop must not tick downward."""
    closes = list(range(100, 200))
    df = _synthetic_ohlc(closes)
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    # Skip the warm-up region (first atr_period bars) where trail is initialising
    trail_tail = out["ut_trail"].iloc[15:].dropna().values
    diffs = np.diff(trail_tail)
    # Some non-decrease is OK (flat bars), but no meaningful drops (>1e-6).
    assert (diffs >= -1e-6).all(), "Trailing stop should not decrease during uptrend"


def test_short_dataframe_does_not_crash():
    """Feed fewer bars than the ATR period — must return safely, not raise."""
    df = _synthetic_ohlc([100, 101, 102])
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    # Output should exist and have the same length; values may be NaN.
    assert len(out) == 3
    for col in ("ut_trail", "ut_buy", "ut_sell", "ut_pos"):
        assert col in out.columns


def test_reversal_produces_signal_flip():
    """Up-then-down should produce a SELL flip after the pivot."""
    up   = list(range(100, 150))
    down = list(range(150, 100, -1))
    df = _synthetic_ohlc(up + down)
    out = compute_utbot_signals(df, key_value=1.0, atr_period=10)
    # A sell signal must occur somewhere in the second half (post-pivot)
    second_half_sells = out["ut_sell"].iloc[len(up):].sum()
    assert int(second_half_sells) >= 1


# ---------------------------------------------------------------------------
# Composite-evaluator: "most-recent-wins" reducer inside the lookback window
# ---------------------------------------------------------------------------
# When a rapid BUY-then-SELL flip happens on consecutive bars inside the
# ``signal_lookback_candles`` window, evaluate_composite_signals must report
# only the *latest* direction — not both — so the scanner does not classify
# the same symbol as BUY when TradingView's most recent label is SELL.

def _minimal_ohlc_with_flags(n: int = 30) -> pd.DataFrame:
    """OHLCV frame long enough for ADX(14) / EMA(200-fallback) computations."""
    idx = pd.date_range(start="2024-01-01 09:15", periods=n, freq="5min")
    base = np.linspace(100.0, 110.0, n)
    df = pd.DataFrame({
        "open":   base,
        "high":   base * 1.001,
        "low":    base * 0.999,
        "close":  base,
        "volume": np.full(n, 10_000.0),
    }, index=idx)
    return df


def _base_config() -> dict:
    """Config with UTBot-only mode and no hard filters (isolates the reducer)."""
    return {
        "strategy": {"ut_enabled": True, "key_value": 1.0, "atr_period": 2,
                     "signal_on_closed_bar": False},
        "sr_channels": {"enabled": False},
        "filters": {
            "ema_filter_enabled": False,
            "volume_filter_enabled": False,
            "candle_patterns_enabled": False,
        },
    }


def test_composite_prefers_latest_when_buy_then_sell_in_window():
    """Window contains BUY at N-1 and SELL at N — result must be SELL only."""
    df = _minimal_ohlc_with_flags()
    # Inject pre-computed UT Bot flags so we bypass indicator warm-up quirks
    df["ut_buy"]  = False
    df["ut_sell"] = False
    df["ut_trail"] = df["close"] - 1.0
    df["ut_pos"]   = 1
    df.loc[df.index[-2], "ut_buy"]  = True   # older signal in window
    df.loc[df.index[-1], "ut_sell"] = True   # newer signal in window

    result = evaluate_composite_signals(df, _base_config(), lookback_candles=2)
    assert result["sell"] is True,  "Latest bar is SELL — composite must be SELL"
    assert result["buy"]  is False, "Older BUY in window must be suppressed"


def test_composite_prefers_latest_when_sell_then_buy_in_window():
    """Symmetry check: SELL at N-1, BUY at N → result must be BUY only."""
    df = _minimal_ohlc_with_flags()
    df["ut_buy"]  = False
    df["ut_sell"] = False
    df["ut_trail"] = df["close"] - 1.0
    df["ut_pos"]   = 1
    df.loc[df.index[-2], "ut_sell"] = True
    df.loc[df.index[-1], "ut_buy"]  = True

    result = evaluate_composite_signals(df, _base_config(), lookback_candles=2)
    assert result["buy"]  is True
    assert result["sell"] is False


def test_composite_keeps_single_signal_in_window():
    """When only one side fires, the reducer must not suppress it."""
    df = _minimal_ohlc_with_flags()
    df["ut_buy"]  = False
    df["ut_sell"] = False
    df["ut_trail"] = df["close"] - 1.0
    df["ut_pos"]   = 1
    df.loc[df.index[-1], "ut_buy"] = True

    result = evaluate_composite_signals(df, _base_config(), lookback_candles=2)
    assert result["buy"]  is True
    assert result["sell"] is False

