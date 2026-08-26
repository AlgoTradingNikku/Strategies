"""
tests/test_regime.py
====================
Unit tests for regime.classify_regime and regime.should_enable_engine.

We synthesise deterministic OHLC frames so the tests are hermetic — no
network calls, no historical data files. Each test constructs the exact
market shape needed to exercise one branch of the classifier.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from regime import classify_regime, should_enable_engine, REGIMES


# ---------------------------------------------------------------------------
# Helpers to build synthetic bar frames
# ---------------------------------------------------------------------------

def _make_df(closes: list[float], atr_pct: float = 0.005) -> pd.DataFrame:
    """Build an OHLC frame from a close-price series."""
    closes = np.asarray(closes, dtype=float)
    highs  = closes * (1.0 + atr_pct)
    lows   = closes * (1.0 - atr_pct)
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes},
        index=idx,
    )


def _uptrend(n: int = 250, step: float = 0.003) -> pd.DataFrame:
    base = 100.0
    return _make_df([base * (1 + step) ** i for i in range(n)])


def _downtrend(n: int = 250, step: float = 0.003) -> pd.DataFrame:
    base = 100.0
    return _make_df([base * (1 - step) ** i for i in range(n)])


def _chop(n: int = 250, amplitude: float = 0.002) -> pd.DataFrame:
    """Sine-wave close series → low ADX."""
    x = np.arange(n)
    closes = 100.0 * (1.0 + amplitude * np.sin(x * 0.6))
    return _make_df(closes.tolist(), atr_pct=0.001)


# ---------------------------------------------------------------------------
# classify_regime
# ---------------------------------------------------------------------------

class TestClassifyRegime:

    def test_returns_unknown_for_none(self):
        out = classify_regime(None)
        assert out["regime"] == "unknown"
        assert out["adx"] is None

    def test_returns_unknown_for_short_frame(self):
        df = _uptrend(n=10)  # below default min_bars=50
        out = classify_regime(df)
        assert out["regime"] == "unknown"

    def test_returns_unknown_when_columns_missing(self):
        df = pd.DataFrame({"close": np.arange(100.0)})
        out = classify_regime(df)
        assert out["regime"] == "unknown"

    def test_uptrend_classified_as_trending_up(self):
        df  = _uptrend()
        out = classify_regime(df)
        assert out["regime"] == "trending_up", f"got {out}"
        assert out["adx"] is not None and out["adx"] >= 22.0
        assert out["plus_di"] >= out["minus_di"]

    def test_downtrend_classified_as_trending_down(self):
        df  = _downtrend()
        out = classify_regime(df)
        assert out["regime"] == "trending_down", f"got {out}"
        assert out["minus_di"] > out["plus_di"]

    def test_quiet_chop_classified_as_chop(self):
        df  = _chop()
        out = classify_regime(df)
        assert out["regime"] == "chop", f"got {out}"
        assert out["adx"] < 22.0

    def test_result_has_all_expected_keys(self):
        df  = _uptrend()
        out = classify_regime(df)
        assert set(out.keys()) == {"regime", "adx", "plus_di", "minus_di", "vol_pct"}

    def test_regime_is_from_enum(self):
        df  = _uptrend()
        out = classify_regime(df)
        assert out["regime"] in REGIMES

    def test_custom_adx_threshold_overrides_default(self):
        """Very-high ADX threshold pushes a real trend into 'chop'."""
        df  = _uptrend()
        out = classify_regime(df, config={"regime": {"adx_trend_threshold": 200.0}})
        # ADX can't exceed 100 by construction, so trend becomes chop.
        assert out["regime"] in ("chop", "high_vol_chop")


# ---------------------------------------------------------------------------
# should_enable_engine
# ---------------------------------------------------------------------------

class TestShouldEnableEngine:

    @pytest.mark.parametrize("regime,engine,expected", [
        ("trending_up",   "utbot", True),
        ("trending_up",   "sr",    True),
        ("trending_down", "utbot", True),
        ("chop",          "utbot", False),   # default: no UT Bot in chop
        ("chop",          "sr",    True),
        ("high_vol_chop", "utbot", False),   # default: sit out event days
        ("high_vol_chop", "sr",    False),
        ("unknown",       "utbot", True),    # fail-open on missing regime
        ("unknown",       "sr",    True),
    ])
    def test_default_policy(self, regime, engine, expected):
        assert should_enable_engine(regime, engine) is expected

    def test_config_override_flips_default(self):
        """User can force UT Bot ON even in chop via config."""
        cfg = {"regime": {"policy": {"chop": {"utbot": True}}}}
        assert should_enable_engine("chop", "utbot", cfg) is True
        # Partial override must not wipe the sibling S/R default.
        assert should_enable_engine("chop", "sr", cfg) is True

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            should_enable_engine("chop", "options")

    def test_engine_name_is_case_insensitive(self):
        assert should_enable_engine("trending_up", "UTBOT") is True
        assert should_enable_engine("chop",        "SR")   is True

