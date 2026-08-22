"""
tests/test_batch_a_to_e_additions.py
====================================
Extra unit tests added alongside the correctness / signal / risk fixes.

Grouped by batch:
  • A: EXIT_EOD action routing + rules_engine ordering
  • B: SR disambiguation, closed-candle helper
  • A₂: scanner._evaluate_trade no-hit sentinel
  • D: risk_limits gate + compute_quantity

Each test uses only in-memory dicts / synthetic DataFrames — no network,
no DB (except the risk-limit test which patches trade_db module-level).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Batch A — EXIT_EOD action
# ---------------------------------------------------------------------------

from trade_management import rules_engine
from trade_management.models import (
    ACTION_EXIT_EOD,
    FULL_EXIT_ACTIONS,
    ACTION_EXIT_TARGET,
    ACTION_EXIT_SL,
)


def _make_pos(**overrides) -> dict:
    pos = {
        "id": 1,
        "symbol": "INFY",
        "exchange": "NSE",
        "direction": "BUY",
        "quantity": 100,
        "entry_price": 1000.0,
        "current_sl": 990.0,
        "initial_sl": 990.0,
        "target_price": 1020.0,
        "high_water_mark": 1000.0,
        "profit_locked": 0,
        "profit_lock_tier": 0,
        "trailing_active": 0,
        "partial_exit_tier": 0,
        "product": "MIS",
    }
    pos.update(overrides)
    return pos


class TestExitEODAction:
    """EOD square-off must emit ACTION_EXIT_EOD, be routed as full-exit."""

    def test_full_exit_actions_frozenset_membership(self):
        assert ACTION_EXIT_EOD in FULL_EXIT_ACTIONS
        assert ACTION_EXIT_TARGET in FULL_EXIT_ACTIONS
        assert ACTION_EXIT_SL in FULL_EXIT_ACTIONS

    def test_eod_cutoff_returns_exit_eod(self):
        """When EOD cutoff is set to a past time-of-day, evaluate() must
        return exactly one ACTION_EXIT_EOD with reason 'EOD_SQUARE_OFF'."""
        pos = _make_pos()
        tm_cfg = {"auto_square_off_enabled": True, "auto_square_off_time": "00:00"}
        actions = rules_engine.evaluate(pos, ltp=1010.0, tm_cfg=tm_cfg)
        assert len(actions) == 1
        assert actions[0].action_type == ACTION_EXIT_EOD
        assert actions[0].reason == "EOD_SQUARE_OFF"

    def test_hwm_updates_before_eod_exit(self):
        """HWM must be updated even on the tick that fires EOD."""
        pos = _make_pos(high_water_mark=1000.0)
        tm_cfg = {"auto_square_off_enabled": True, "auto_square_off_time": "00:00"}
        _ = rules_engine.evaluate(pos, ltp=1012.5, tm_cfg=tm_cfg)
        assert pos["high_water_mark"] == 1012.5


# ---------------------------------------------------------------------------
# Batch A₂ — scanner._evaluate_trade "no hit" sentinel
# ---------------------------------------------------------------------------

from scanner import _evaluate_trade


class TestEvaluateTradeNoHit:
    """When neither TP nor SL is hit the returned index sentinels must be -1
    (not 0) so the outer win-rate loop doesn't misclassify a "no hit" trade
    as a win at bar 0."""

    def test_buy_no_hit_returns_minus_one_sentinels(self):
        # Entry 100, ATR 1 → TP=103, SL=98. Feed prices that stay in [99, 102].
        future_high = np.array([102.0, 101.5, 100.8])
        future_low  = np.array([99.5, 99.8, 100.0])
        tp_ok, sl_ok, tp_hit, sl_hit = _evaluate_trade(
            entry=100.0, atr=1.0,
            future_high=future_high, future_low=future_low, is_buy=True,
        )
        assert tp_ok is False and sl_ok is False
        assert tp_hit == -1 and sl_hit == -1

    def test_buy_tp_only_returns_valid_tp_hit(self):
        # TP=103 will be hit at index 1; SL=98 never hit.
        future_high = np.array([102.0, 103.5, 101.0])
        future_low  = np.array([99.0, 99.5, 99.7])
        tp_ok, sl_ok, tp_hit, sl_hit = _evaluate_trade(
            entry=100.0, atr=1.0,
            future_high=future_high, future_low=future_low, is_buy=True,
        )
        assert tp_ok is True and sl_ok is False
        assert tp_hit == 1
        assert sl_hit == -1

    def test_empty_future_arrays_return_no_hit(self):
        tp_ok, sl_ok, tp_hit, sl_hit = _evaluate_trade(
            entry=100.0, atr=1.0,
            future_high=np.array([]), future_low=np.array([]), is_buy=True,
        )
        assert tp_ok is False and sl_ok is False
        assert tp_hit == -1 and sl_hit == -1


# ---------------------------------------------------------------------------
# Batch B — SR disambiguation
# ---------------------------------------------------------------------------

from signals import compute_sr_signals, _is_last_candle_incomplete


def _make_ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a minimal OHLCV frame from a list of (open, high, low, close)."""
    idx = pd.date_range(start="2024-01-01 09:15", periods=len(rows), freq="5min")
    df = pd.DataFrame(
        rows, columns=["open", "high", "low", "close"], index=idx
    )
    df["volume"] = 10_000.0
    return df


class TestSRDisambiguation:
    """When close is inside a zone, the candle direction must decide BUY vs SELL.
    A single bar can no longer emit both sr_buy=True AND sr_sell=True."""

    def test_no_bar_fires_both_sr_buy_and_sr_sell(self):
        # Build ~300 bars ranging around 100 so the SR engine finds pivots,
        # then evaluate the SR flags. Post-fix invariant: no single bar
        # should have both sr_buy AND sr_sell set to True.
        base = np.linspace(95.0, 105.0, 300)
        highs = base + 1.0
        lows  = base - 1.0
        opens = base
        closes = base + 0.5
        rows = list(zip(opens, highs, lows, closes))
        df = _make_ohlc(rows)

        out, _zones = compute_sr_signals(
            df,
            pivot_period=5,
            source="High/Low",
            channel_width_pct=8,
            min_strength=1,
            max_num_sr=6,
            loopback=250,
            proximity_pct=0.5,
        )

        conflict = (out["sr_buy"] & out["sr_sell"]).any()
        assert not conflict, "SR engine must never fire BUY and SELL on the same bar"


# ---------------------------------------------------------------------------
# Batch B — closed-candle helper
# ---------------------------------------------------------------------------

class TestClosedCandleHelper:
    def test_returns_false_for_empty(self):
        empty = pd.DataFrame(columns=["open", "high", "low", "close"])
        assert _is_last_candle_incomplete(empty, {}) is False

    def test_returns_false_for_non_datetime_index(self):
        df = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
            index=[0],
        )
        assert _is_last_candle_incomplete(df, {"candle_timeframe": "5m"}) is False

    def test_returns_false_when_last_bar_close_time_has_passed(self):
        # Last bar opened 1 hour ago on a 5-minute timeframe → definitely closed.
        past_open = pd.Timestamp.now() - pd.Timedelta(hours=1)
        idx = pd.date_range(end=past_open, periods=2, freq="5min")
        df = pd.DataFrame(
            {"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0], "close": [1.0, 1.0]},
            index=idx,
        )
        assert _is_last_candle_incomplete(df, {"candle_timeframe": "5m", "exchange": "NSE"}) is False

    def test_returns_true_when_last_bar_just_opened(self):
        # Last bar opened 1 second ago on a 5-minute timeframe → still forming.
        recent_open = pd.Timestamp.now() - pd.Timedelta(seconds=1)
        idx = pd.DatetimeIndex([recent_open - pd.Timedelta(minutes=5), recent_open])
        df = pd.DataFrame(
            {"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0], "close": [1.0, 1.0]},
            index=idx,
        )
        assert _is_last_candle_incomplete(df, {"candle_timeframe": "5m", "exchange": "NSE"}) is True



# ---------------------------------------------------------------------------
# Batch D — risk_limits
# ---------------------------------------------------------------------------

import risk_limits


class TestComputeQuantity:
    def test_capital_per_trade_takes_precedence(self):
        cfg = {"openalgo": {"capital_per_trade": 10_000, "order_quantity": 1}}
        assert risk_limits.compute_quantity(500.0, cfg) == 20   # 10000 // 500

    def test_falls_back_to_order_quantity_when_capital_unset(self):
        cfg = {"openalgo": {"order_quantity": 5}}
        assert risk_limits.compute_quantity(500.0, cfg) == 5

    def test_never_returns_less_than_one_when_price_exceeds_capital(self):
        cfg = {"openalgo": {"capital_per_trade": 100}}
        assert risk_limits.compute_quantity(500.0, cfg) == 1

    def test_zero_or_negative_price_falls_back(self):
        cfg = {"openalgo": {"capital_per_trade": 10_000, "order_quantity": 3}}
        assert risk_limits.compute_quantity(0.0, cfg) == 3


class TestRiskGate:
    def test_disabled_gate_always_allows(self):
        cfg = {"risk_limits": {"enabled": False}}
        ok, why = risk_limits.check_can_open_new("INFY", cfg, open_positions=[])
        assert ok and why == ""

    def test_max_concurrent_rejects(self):
        cfg = {"risk_limits": {"enabled": True, "max_concurrent_positions": 2}}
        already_open = [
            {"symbol": "A", "direction": "BUY"},
            {"symbol": "B", "direction": "BUY"},
        ]
        ok, why = risk_limits.check_can_open_new("C", cfg, open_positions=already_open)
        assert not ok
        assert "max_concurrent_positions" in why

    def test_max_per_symbol_rejects_duplicate(self):
        cfg = {"risk_limits": {"enabled": True, "max_positions_per_symbol": 1}}
        already_open = [{"symbol": "INFY", "direction": "BUY"}]
        ok, why = risk_limits.check_can_open_new("INFY", cfg, open_positions=already_open)
        assert not ok
        assert "INFY" in why

    def test_daily_loss_stop_rejects_when_hit(self, monkeypatch):
        """Patch trade_db.get_realized_pnl_pct_since so we don't need a real DB."""
        cfg = {"risk_limits": {"enabled": True, "daily_loss_stop_pct": -3.0}}
        monkeypatch.setattr(
            risk_limits.trade_db,
            "get_realized_pnl_pct_since",
            lambda iso_start: -3.5,   # already worse than -3.0 → block
        )
        ok, why = risk_limits.check_can_open_new("INFY", cfg, open_positions=[])
        assert not ok
        assert "daily_loss_stop_pct" in why

    def test_daily_loss_stop_allows_when_above_floor(self, monkeypatch):
        cfg = {"risk_limits": {"enabled": True, "daily_loss_stop_pct": -3.0}}
        monkeypatch.setattr(
            risk_limits.trade_db,
            "get_realized_pnl_pct_since",
            lambda iso_start: -1.0,   # still above -3.0 → allow
        )
        ok, why = risk_limits.check_can_open_new("INFY", cfg, open_positions=[])
        assert ok and why == ""

