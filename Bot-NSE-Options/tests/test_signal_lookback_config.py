import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import signals


def _df_with_stub_signals() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "volume": [1000, 1100, 1200, 1300, 1400],
            "ut_trail": [99.0, 100.0, 101.0, 102.0, 103.0],
            "ut_pos": [1, 1, 1, 1, 1],
            "ut_buy": [False, False, False, True, False],
            "ut_sell": [False, False, False, False, False],
            "sr_near_support": [False, False, False, False, False],
            "sr_near_resistance": [False, False, False, False, False],
            "sr_buy": [False, False, False, False, False],
            "sr_sell": [False, False, False, False, False],
        },
        index=pd.date_range("2026-09-07 09:15", periods=5, freq="5min"),
    )
    return df


def _patch_signal_engines(monkeypatch):
    monkeypatch.setattr(signals, "compute_utbot_signals", lambda df, **kwargs: df.copy())
    monkeypatch.setattr(signals, "compute_sr_signals", lambda df, **kwargs: df.copy())
    monkeypatch.setattr(signals, "_is_last_candle_incomplete", lambda df, cfg: False)


def test_strategy_signal_lookback_one_uses_latest_candle_only(monkeypatch):
    _patch_signal_engines(monkeypatch)

    out = signals.evaluate_composite_signals(
        _df_with_stub_signals(),
        signal_mode="UTBot",
        cfg={"strategy": {"signal_lookback_candles": 1, "signal_on_closed_bar": True}},
    )

    assert bool(out["final_buy"].iloc[-1]) is False
    assert bool(out["final_sell"].iloc[-1]) is False


def test_strategy_signal_lookback_overrides_legacy_options_value(monkeypatch):
    _patch_signal_engines(monkeypatch)

    out = signals.evaluate_composite_signals(
        _df_with_stub_signals(),
        signal_mode="UTBot",
        cfg={
            "options": {"signal_lookback_candles": 2},
            "strategy": {"signal_lookback_candles": 1, "signal_on_closed_bar": True},
        },
    )

    assert bool(out["final_buy"].iloc[-1]) is False
    assert bool(out["final_sell"].iloc[-1]) is False


def test_legacy_options_signal_lookback_still_works(monkeypatch):
    _patch_signal_engines(monkeypatch)

    out = signals.evaluate_composite_signals(
        _df_with_stub_signals(),
        signal_mode="UTBot",
        cfg={"options": {"signal_lookback_candles": 2}, "strategy": {"signal_on_closed_bar": True}},
    )

    assert bool(out["final_buy"].iloc[-1]) is True
    assert bool(out["final_sell"].iloc[-1]) is False