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

from signals import compute_utbot_signals


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
