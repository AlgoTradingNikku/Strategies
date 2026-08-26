"""
tests/test_fetch_history_openalgo.py
====================================
Regression tests for scanner.fetch_history's openalgo data source path.

Uses a mocked openalgo.api client so tests are hermetic (no live server or
network required). Guards the fixes introduced when normalising openalgo
output to match the yfinance-shape contract:

  1. Interval "1d" is mapped to broker-native "D" before calling client.history.
  2. Returned DataFrame is tz-naive (openalgo natively emits Asia/Kolkata tz-aware).
  3. Extra 'oi' / 'open_interest' columns are stripped.
  4. Synthetic post-close zero-volume flat OHLC bars are filtered out.
  5. Structured API-error dict payloads return None (do not silently degrade).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_BOT_ROOT = Path(__file__).resolve().parent.parent
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))


@pytest.fixture
def base_config() -> dict:
    return {
        "data_source": "openalgo",
        "exchange": "NSE",
        "data": {"lookback_days": 5},
        "openalgo": {"apikey": "test-key", "base_url": "http://127.0.0.1:5000"},
    }


def _make_openalgo_df() -> pd.DataFrame:
    """Simulate a raw openalgo history() response for IOC 5m over ~1 session.

    Includes tz-aware Asia/Kolkata index, an extra 'oi' column, and one
    synthetic post-close flat zero-volume bar at 15:20.
    """
    idx = pd.DatetimeIndex(
        [
            "2026-08-21 09:15:00", "2026-08-21 09:20:00", "2026-08-21 09:25:00",
            "2026-08-21 15:10:00", "2026-08-21 15:15:00", "2026-08-21 15:20:00",
        ],
        tz="Asia/Kolkata",
        name="timestamp",
    )
    return pd.DataFrame(
        {
            "close":  [136.5, 137.0, 136.8, 135.7, 135.75, 135.75],
            "high":   [137.0, 137.2, 137.1, 135.9, 135.85, 135.75],
            "low":    [136.4, 136.9, 136.6, 135.5, 135.60, 135.75],
            "oi":     [0, 0, 0, 0, 0, 0],
            "open":   [136.6, 137.0, 137.0, 135.8, 135.65, 135.75],
            "volume": [12500, 8400, 9100, 15200, 6800, 0],  # last = synthetic flat
        },
        index=idx,
    )


def _install_fake_openalgo(monkeypatch, history_return, intervals_return=None):
    """Register a stub ``openalgo`` module so fetch_history picks up the fake."""
    fake_client = MagicMock()
    fake_client.history.return_value = history_return
    if intervals_return is None:
        intervals_return = {
            "status": "success",
            "data": {
                "seconds": [], "minutes": ["1m", "3m", "5m", "15m", "30m"],
                "hours": ["1h"], "days": ["D"], "weeks": [], "months": [],
            },
        }
    fake_client.intervals.return_value = intervals_return

    def _api_ctor(api_key: str, host: str):
        return fake_client

    fake_module = types.ModuleType("openalgo")
    fake_module.api = _api_ctor
    monkeypatch.setitem(sys.modules, "openalgo", fake_module)

    import scanner
    scanner._OPENALGO_SUPPORTED_CACHE.clear()
    return fake_client


# ----- tests ----------------------------------------------------------------

def test_openalgo_normalises_output_shape(monkeypatch, base_config):
    """End-to-end shape assertions on a realistic openalgo response."""
    from scanner import fetch_history

    fake_client = _install_fake_openalgo(monkeypatch, _make_openalgo_df())
    df = fetch_history("IOC", "5m", base_config)

    assert df is not None
    assert df.index.tz is None, f"index still tz-aware: {df.index.tz}"
    assert "oi" not in df.columns
    assert "open_interest" not in df.columns
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)
    # 6 raw rows -> 5 after dropping the synthetic zero-vol flat bar
    assert len(df) == 5
    assert pd.Timestamp("2026-08-21 15:20:00") not in df.index
    assert pd.Timestamp("2026-08-21 15:15:00") in df.index
    assert df.index.is_monotonic_increasing
    assert fake_client.history.call_args.kwargs["interval"] == "5m"


def test_openalgo_maps_1d_to_D(monkeypatch, base_config):
    """`candle_timeframe: 1d` must be translated to broker-native `D`."""
    from scanner import fetch_history

    daily_df = pd.DataFrame(
        {
            "close":  [136.0, 137.0, 138.0],
            "high":   [137.0, 138.0, 139.0],
            "low":    [135.0, 136.0, 137.0],
            "open":   [135.5, 136.5, 137.5],
            "volume": [1_000_000, 1_100_000, 1_200_000],
        },
        index=pd.DatetimeIndex(
            ["2026-08-19", "2026-08-20", "2026-08-21"],
            tz="Asia/Kolkata", name="timestamp",
        ),
    )
    fake_client = _install_fake_openalgo(monkeypatch, daily_df)

    df = fetch_history("IOC", "1d", base_config)

    assert df is not None
    assert len(df) == 3
    assert fake_client.history.call_args.kwargs["interval"] == "D"


def test_openalgo_error_dict_returns_none(monkeypatch, base_config, caplog):
    """A structured `{status: error}` payload yields None + a warning."""
    from scanner import fetch_history

    error_payload = {
        "status": "error",
        "message": 'HTTP 400: {"message":{"interval":["Invalid"]}}',
        "code": 400,
        "error_type": "http_error",
    }
    _install_fake_openalgo(monkeypatch, error_payload)

    with caplog.at_level("WARNING", logger="UTBotSRChannelsScanner"):
        df = fetch_history("IOC", "5m", base_config)

    assert df is None
    assert any("openalgo error" in rec.message for rec in caplog.records)


def test_openalgo_unsupported_interval_short_circuits(monkeypatch, base_config, caplog):
    """When client.intervals() reports the broker cannot serve the timeframe,
    fetch_history returns None *without* calling history()."""
    from scanner import fetch_history

    supported = {
        "status": "success",
        "data": {
            "seconds": [], "minutes": ["1m"], "hours": [],
            "days": ["D"], "weeks": [], "months": [],
        },
    }
    fake_client = _install_fake_openalgo(
        monkeypatch, _make_openalgo_df(), intervals_return=supported,
    )

    with caplog.at_level("WARNING", logger="UTBotSRChannelsScanner"):
        df = fetch_history("IOC", "2h", base_config)

    assert df is None
    fake_client.history.assert_not_called()
    assert any("not supported" in rec.message for rec in caplog.records)


def test_openalgo_dedupes_and_dropna(monkeypatch, base_config):
    """Duplicate timestamps and NaN-OHLC rows are cleaned up."""
    from scanner import fetch_history

    idx = pd.DatetimeIndex(
        [
            "2026-08-21 09:15:00", "2026-08-21 09:20:00",
            "2026-08-21 09:20:00",  # duplicate — keep=last must retain 137.05
            "2026-08-21 09:25:00",  # will have NaN close -> dropped
        ],
        tz="Asia/Kolkata", name="timestamp",
    )
    df_raw = pd.DataFrame(
        {
            "close":  [136.5, 137.0, 137.05, float("nan")],
            "high":   [137.0, 137.2, 137.25, 137.5],
            "low":    [136.4, 136.9, 136.95, 137.0],
            "open":   [136.6, 137.0, 137.0, 137.2],
            "volume": [12500, 8400, 8500, 9000],
        },
        index=idx,
    )
    _install_fake_openalgo(monkeypatch, df_raw)

    out = fetch_history("IOC", "5m", base_config)

    assert out is not None
    assert len(out) == 2
    assert out.loc[pd.Timestamp("2026-08-21 09:20:00"), "close"] == pytest.approx(137.05)
