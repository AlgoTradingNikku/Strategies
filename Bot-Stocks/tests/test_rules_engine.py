"""
tests/test_rules_engine.py
==========================
Unit tests for trade_management.rules_engine.

The rules engine is deliberately side-effect free (no DB, no network) so it
can be tested with plain dicts as fixtures.  These tests cover:

  - Target / SL exit for both BUY and SELL directions
  - Trailing SL activation & tighter-of-two SL logic vs profit_lock
  - Profit lock (legacy flat mode + multi-tier mode)
  - Partial exit (multi-tier + legacy flat) with the new depletion guard
  - Priority ordering (target > SL > trailing > profit-lock > partial)

Run from the Bot-Stocks/ folder:
    pytest tests/test_rules_engine.py -q
"""

from __future__ import annotations
import pytest

from trade_management import rules_engine
from trade_management.models import TradeAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_buy_position(**overrides) -> dict:
    """Return a canonical BUY position dict; overrides shallow-merged in."""
    pos = {
        "id":                 1,
        "symbol":             "INFY",
        "exchange":           "NSE",
        "direction":          "BUY",
        "quantity":           100,
        "entry_price":        1000.0,
        "current_sl":         990.0,       # 1% below entry
        "initial_sl":         990.0,
        "target_price":       1020.0,      # 2% above entry
        "high_water_mark":    1000.0,
        "profit_locked":      0,
        "profit_lock_tier":   0,
        "trailing_active":    0,
        "partial_exit_tier":  0,
        "product":            "MIS",
    }
    pos.update(overrides)
    return pos


def _make_sell_position(**overrides) -> dict:
    """Return a canonical SELL position dict; overrides shallow-merged in."""
    pos = _make_buy_position()
    pos.update({
        "direction":          "SELL",
        "current_sl":         1010.0,   # 1% above entry
        "initial_sl":         1010.0,
        "target_price":       980.0,    # 2% below entry
    })
    pos.update(overrides)
    return pos


# ---------------------------------------------------------------------------
# Target exit
# ---------------------------------------------------------------------------

class TestTargetExit:
    def test_buy_hits_target_returns_exit(self):
        pos = _make_buy_position()
        actions = rules_engine.evaluate(pos, ltp=1020.5, tm_cfg={})
        assert len(actions) == 1
        assert actions[0].action_type == "EXIT_TARGET"

    def test_buy_below_target_no_exit(self):
        pos = _make_buy_position()
        actions = rules_engine.evaluate(pos, ltp=1019.99, tm_cfg={})
        assert not any(a.action_type == "EXIT_TARGET" for a in actions)

    def test_sell_hits_target_returns_exit(self):
        pos = _make_sell_position()
        actions = rules_engine.evaluate(pos, ltp=979.5, tm_cfg={})
        assert len(actions) == 1
        assert actions[0].action_type == "EXIT_TARGET"


# ---------------------------------------------------------------------------
# Stop-loss exit
# ---------------------------------------------------------------------------

class TestStopLossExit:
    def test_buy_hits_sl_returns_exit(self):
        pos = _make_buy_position()
        actions = rules_engine.evaluate(pos, ltp=989.5, tm_cfg={})
        assert len(actions) == 1
        assert actions[0].action_type == "EXIT_SL"

    def test_sell_hits_sl_returns_exit(self):
        pos = _make_sell_position()
        actions = rules_engine.evaluate(pos, ltp=1010.5, tm_cfg={})
        assert len(actions) == 1
        assert actions[0].action_type == "EXIT_SL"

    def test_target_takes_precedence_over_sl_when_both_edge_cases(self):
        """Target is checked before SL — only relevant when a tick jumps a huge gap."""
        pos = _make_buy_position()
        actions = rules_engine.evaluate(pos, ltp=1050.0, tm_cfg={})
        assert actions[0].action_type == "EXIT_TARGET"


# ---------------------------------------------------------------------------
# Trailing SL
# ---------------------------------------------------------------------------

class TestTrailingSL:
    def test_disabled_by_default(self):
        pos = _make_buy_position(high_water_mark=1010.0)
        actions = rules_engine.evaluate(pos, ltp=1010.0, tm_cfg={})
        assert not any(a.action_type == "TRAILING_SL" for a in actions)

    def test_not_activated_below_activation_pct(self):
        pos = _make_buy_position(high_water_mark=1005.0)
        tm_cfg = {"trailing_sl": {"enabled": True, "activation_pct": 1.0, "distance_pct": 0.5}}
        actions = rules_engine.evaluate(pos, ltp=1005.0, tm_cfg=tm_cfg)
        assert not any(a.action_type == "TRAILING_SL" for a in actions)

    def test_activates_and_moves_sl_up_for_buy(self):
        pos = _make_buy_position(high_water_mark=1015.0)
        tm_cfg = {"trailing_sl": {"enabled": True, "activation_pct": 1.0, "distance_pct": 0.5}}
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        tsl = next((a for a in actions if a.action_type == "TRAILING_SL"), None)
        assert tsl is not None
        # 0.5% below HWM 1015 -> 1009.925 -> rounded to 1009.93
        assert tsl.new_sl == pytest.approx(1009.93, abs=0.01)

    def test_never_moves_sl_down_for_buy(self):
        pos = _make_buy_position(high_water_mark=1015.0, current_sl=1012.0)
        tm_cfg = {"trailing_sl": {"enabled": True, "activation_pct": 1.0, "distance_pct": 0.5}}
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        assert not any(a.action_type == "TRAILING_SL" for a in actions)



# ---------------------------------------------------------------------------
# Profit Lock
# ---------------------------------------------------------------------------

class TestProfitLock:
    def test_legacy_flat_mode_fires_once(self):
        pos = _make_buy_position(high_water_mark=1020.0)   # +2% peak
        tm_cfg = {
            "profit_lock": {"enabled": True, "threshold_pct": 1.5, "lock_fraction": 0.5},
        }
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        pl = next((a for a in actions if a.action_type == "PROFIT_LOCK"), None)
        assert pl is not None
        # 0.5 * (1020 - 1000) = 10 -> new_sl = 1010
        assert pl.new_sl == pytest.approx(1010.0, abs=0.01)

    def test_tiered_mode_picks_best_tier(self):
        # LTP=1029 exceeds default target 1020 → would short-circuit with
        # EXIT_TARGET; give this test a target above LTP so the profit-lock
        # branch actually gets a chance to run.
        pos = _make_buy_position(high_water_mark=1030.0, target_price=1100.0)   # +3% peak
        tm_cfg = {
            "profit_lock": {
                "enabled": True,
                "tiers": [
                    {"threshold_pct": 1.0, "lock_fraction": 0.3},
                    {"threshold_pct": 2.0, "lock_fraction": 0.5},
                    {"threshold_pct": 3.0, "lock_fraction": 0.8},
                ],
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1029.0, tm_cfg=tm_cfg)
        pl = next((a for a in actions if a.action_type == "PROFIT_LOCK"), None)
        assert pl is not None
        # Best tier is 0.8 * 30 = 24 -> new_sl = 1024
        assert pl.new_sl == pytest.approx(1024.0, abs=0.01)



# ---------------------------------------------------------------------------
# Partial Exit — including the depletion-guard fix (#5)
# ---------------------------------------------------------------------------

class TestPartialExit:
    def test_tiered_normal_case(self):
        pos = _make_buy_position(high_water_mark=1015.0)
        tm_cfg = {
            "partial_exit": {
                "enabled": True,
                "tiers": [{"trigger_pct": 1.0, "exit_qty_fraction": 0.5}],
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        pe = next((a for a in actions if a.action_type == "PARTIAL_EXIT"), None)
        assert pe is not None
        assert pe.exit_qty == 50   # 50% of 100

    def test_tiered_guard_clamps_full_fraction_to_qty_minus_one(self):
        """A fraction of 1.0 must NOT drain the position — clamp to qty-1."""
        pos = _make_buy_position(high_water_mark=1015.0, quantity=100)
        tm_cfg = {
            "partial_exit": {
                "enabled": True,
                "tiers": [{"trigger_pct": 1.0, "exit_qty_fraction": 1.0}],
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        pe = next((a for a in actions if a.action_type == "PARTIAL_EXIT"), None)
        assert pe is not None
        assert pe.exit_qty == 99   # clamped, one share retained

    def test_tiered_guard_skips_when_only_one_share(self):
        """If only 1 share remains, a partial makes no sense — skip entirely."""
        pos = _make_buy_position(high_water_mark=1015.0, quantity=1)
        tm_cfg = {
            "partial_exit": {
                "enabled": True,
                "tiers": [{"trigger_pct": 1.0, "exit_qty_fraction": 0.5}],
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        assert not any(a.action_type == "PARTIAL_EXIT" for a in actions)

    def test_legacy_flat_guard_clamps_full_fraction(self):
        pos = _make_buy_position(high_water_mark=1015.0, quantity=10)
        tm_cfg = {
            "partial_exit": {
                "enabled": True,
                "target1_pct": 1.0,
                "exit_qty_fraction": 1.0,
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1015.0, tm_cfg=tm_cfg)
        pe = next((a for a in actions if a.action_type == "PARTIAL_EXIT"), None)
        assert pe is not None
        assert pe.exit_qty == 9

    def test_trigger_not_reached_returns_none(self):
        pos = _make_buy_position(high_water_mark=1005.0)
        tm_cfg = {
            "partial_exit": {
                "enabled": True,
                "tiers": [{"trigger_pct": 1.5, "exit_qty_fraction": 0.5}],
            },
        }
        actions = rules_engine.evaluate(pos, ltp=1005.0, tm_cfg=tm_cfg)
        assert not any(a.action_type == "PARTIAL_EXIT" for a in actions)



# ---------------------------------------------------------------------------
# High-water mark maintenance
# ---------------------------------------------------------------------------

class TestHighWaterMark:
    def test_buy_hwm_rises_with_price(self):
        pos = _make_buy_position(high_water_mark=1000.0)
        rules_engine.evaluate(pos, ltp=1005.0, tm_cfg={})
        assert pos["high_water_mark"] == 1005.0
        assert pos.get("_hwm_dirty") is True

    def test_buy_hwm_does_not_fall_below_prior(self):
        pos = _make_buy_position(high_water_mark=1010.0)
        rules_engine.evaluate(pos, ltp=1005.0, tm_cfg={})
        assert pos["high_water_mark"] == 1010.0

    def test_sell_hwm_falls_with_price(self):
        pos = _make_sell_position(high_water_mark=1000.0)
        rules_engine.evaluate(pos, ltp=990.0, tm_cfg={})
        assert pos["high_water_mark"] == 990.0


# ---------------------------------------------------------------------------
# Interaction: trailing SL vs profit lock — the tighter wins on the same tick
# ---------------------------------------------------------------------------

class TestTrailingVsProfitLock:
    def test_profit_lock_replaces_looser_trailing(self):
        # Use a large target so the target-exit rule doesn't short-circuit
        # this test (LTP=1030 would otherwise hit the default 1020 target).
        pos = _make_buy_position(high_water_mark=1030.0, target_price=1100.0)
        tm_cfg = {
            "trailing_sl": {"enabled": True, "activation_pct": 1.0, "distance_pct": 2.0},
            "profit_lock": {"enabled": True, "threshold_pct": 1.5, "lock_fraction": 0.8},
        }
        actions = rules_engine.evaluate(pos, ltp=1030.0, tm_cfg=tm_cfg)
        # Trailing → 1030 * 0.98 = 1009.4
        # Profit lock → 1000 + 0.8 * (1030 - 1000) = 1024.0 (tighter for BUY)
        adjust_actions = [
            a for a in actions if a.action_type in ("TRAILING_SL", "PROFIT_LOCK")
        ]
        assert len(adjust_actions) == 1
        assert adjust_actions[0].action_type == "PROFIT_LOCK"
        assert adjust_actions[0].new_sl == pytest.approx(1024.0, abs=0.01)

