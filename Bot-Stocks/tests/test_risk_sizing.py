"""
tests/test_risk_sizing.py
=========================
Sprint-2 unit tests for the risk-based position sizer and capital validator
in ``risk_limits.py``, plus the ``regime_gate`` module.

These tests are pure: they never touch the disk, network, or trade_db.
"""

from __future__ import annotations

import logging

import pytest

import risk_limits
import regime_gate


# ---------------------------------------------------------------------------
# validate_capital
# ---------------------------------------------------------------------------

class TestValidateCapital:

    def test_defaults_to_1_lakh_when_missing(self):
        assert risk_limits.validate_capital({}) == 1_00_000.0

    def test_reads_risk_limits_capital(self):
        cfg = {"risk_limits": {"capital": 50000}}
        assert risk_limits.validate_capital(cfg) == 50_000.0

    def test_falls_back_to_openalgo_capital_per_trade(self):
        cfg = {"openalgo": {"capital_per_trade": 25000}}
        assert risk_limits.validate_capital(cfg) == 25_000.0

    def test_risk_limits_capital_takes_precedence_over_openalgo(self):
        cfg = {
            "risk_limits": {"capital": 75000},
            "openalgo": {"capital_per_trade": 25000},
        }
        assert risk_limits.validate_capital(cfg) == 75_000.0

    def test_clamps_below_minimum(self, caplog):
        cfg = {"risk_limits": {"capital": 5000}}   # below ₹10k
        with caplog.at_level(logging.WARNING):
            assert risk_limits.validate_capital(cfg) == 10_000.0
        assert any("below minimum" in m for m in caplog.messages)

    def test_clamps_above_maximum(self, caplog):
        cfg = {"risk_limits": {"capital": 50_00_000}}   # ₹50L, above ₹10L cap
        with caplog.at_level(logging.WARNING):
            assert risk_limits.validate_capital(cfg) == 10_00_000.0
        assert any("above maximum" in m for m in caplog.messages)

    def test_allow_unlimited_bypasses_upper_bound(self, caplog):
        cfg = {"risk_limits": {"capital": 50_00_000, "capital_allow_unlimited": True}}
        with caplog.at_level(logging.WARNING):
            assert risk_limits.validate_capital(cfg) == 50_00_000.0
        # Above-10L warning should still fire, but value is honoured.
        assert any("exceeds ₹10L" in m for m in caplog.messages)

    def test_allow_unlimited_does_not_break_normal_range(self):
        cfg = {"risk_limits": {"capital": 500000, "capital_allow_unlimited": True}}
        assert risk_limits.validate_capital(cfg) == 500_000.0

    def test_non_numeric_capital_defaults(self, caplog):
        cfg = {"risk_limits": {"capital": "not a number"}}
        with caplog.at_level(logging.WARNING):
            assert risk_limits.validate_capital(cfg) == 1_00_000.0

    def test_zero_or_negative_capital_defaults(self, caplog):
        cfg = {"risk_limits": {"capital": 0}}
        with caplog.at_level(logging.WARNING):
            assert risk_limits.validate_capital(cfg) == 1_00_000.0
        cfg2 = {"risk_limits": {"capital": -100}}
        assert risk_limits.validate_capital(cfg2) == 1_00_000.0


# ---------------------------------------------------------------------------
# compute_quantity_risk_based
# ---------------------------------------------------------------------------

class TestRiskBasedSizing:

    def _cfg(self, **overrides) -> dict:
        base = {
            "risk_limits": {
                "sizing_mode": "risk_based",
                "capital": 1_00_000,
                "risk_per_trade_pct": 1.0,
            }
        }
        base["risk_limits"].update(overrides)
        return base

    def test_basic_math_1pct_of_1L_at_5rupee_sl_gives_200_qty(self):
        # risk budget = 1% × ₹1L = ₹1000. per-share risk = ₹5. qty = 200.
        out = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=95.0, config=self._cfg(),
        )
        assert out["quantity"] == 200
        assert out["risk_amount"] == pytest.approx(1000.0, abs=0.01)
        assert out["risk_pct"] == pytest.approx(1.0, abs=0.01)
        assert out["mode"] == "risk_based"
        assert out["reason"] == "OK"

    def test_wider_sl_reduces_qty_keeping_risk_constant(self):
        """The whole point of fixed-fractional: wider SL → fewer shares → same ₹ risk."""
        narrow = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=99.0, config=self._cfg(),
        )
        wide = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=90.0, config=self._cfg(),
        )
        assert narrow["quantity"] > wide["quantity"]
        # Both should risk ~₹1000 (integer rounding may lose a few rupees).
        assert narrow["risk_amount"] == pytest.approx(1000.0, abs=1.0)
        assert wide["risk_amount"] == pytest.approx(1000.0, abs=10.0)

    def test_sl_equals_entry_falls_back_to_capital_pct(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = risk_limits.compute_quantity_risk_based(
                entry_price=100.0, stop_loss=100.0, config=self._cfg(),
            )
        assert out["quantity"] == 1000   # 1L / 100
        assert out["mode"] == "risk_based_fallback_capital_pct"
        assert out["reason"] == "SL_EQUALS_ENTRY"

    def test_invalid_entry_price_returns_zero_qty(self):
        out = risk_limits.compute_quantity_risk_based(
            entry_price=0.0, stop_loss=1.0, config=self._cfg(),
        )
        assert out["quantity"] == 0
        assert out["reason"] == "INVALID_ENTRY_PRICE"

    def test_risk_budget_smaller_than_one_share_returns_zero(self):
        # ₹10k capital, 0.1% risk, ₹100 SL → budget ₹10, per-share risk ₹100 → 0.
        cfg = self._cfg(capital=10_000, risk_per_trade_pct=0.1)
        out = risk_limits.compute_quantity_risk_based(
            entry_price=500.0, stop_loss=400.0, config=cfg,
        )
        assert out["quantity"] == 0
        assert "RISK_BUDGET_TOO_SMALL" in out["reason"]

    def test_notional_capped_at_capital_when_bounded(self):
        # ₹10k capital, but risk budget would fund 100 shares of ₹500 = ₹50k notional.
        # Bounded mode must clamp qty to ₹10k / ₹500 = 20 shares.
        cfg = self._cfg(capital=10_000, risk_per_trade_pct=10.0)
        out = risk_limits.compute_quantity_risk_based(
            entry_price=500.0, stop_loss=490.0, config=cfg,
        )
        # risk budget = ₹1000, per-share risk = ₹10, raw_qty = 100.
        # Notional would be 100 × 500 = ₹50000 > ₹10k capital → clamped to 20.
        assert out["quantity"] == 20

    def test_notional_uncapped_with_allow_unlimited(self):
        cfg = self._cfg(capital=10_000, risk_per_trade_pct=10.0,
                        capital_allow_unlimited=True)
        out = risk_limits.compute_quantity_risk_based(
            entry_price=500.0, stop_loss=490.0, config=cfg,
        )
        assert out["quantity"] == 100

    def test_capital_pct_mode_uses_full_capital(self):
        cfg = {"risk_limits": {"sizing_mode": "capital_pct", "capital": 1_00_000}}
        out = risk_limits.compute_quantity_risk_based(
            entry_price=200.0, stop_loss=195.0, config=cfg,
        )
        assert out["quantity"] == 500   # 1L / 200
        assert out["mode"] == "capital_pct"

    def test_legacy_mode_preserves_compute_quantity_behaviour(self):
        cfg = {
            "risk_limits": {"sizing_mode": "legacy"},
            "openalgo": {"capital_per_trade": 10_000, "order_quantity": 1},
        }
        out = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=95.0, config=cfg,
        )
        assert out["quantity"] == 100
        assert out["mode"] == "legacy"

    def test_at_10k_min_capital_1pct_sizing_works(self):
        """User's lower bound of ₹10k must still produce a valid trade."""
        cfg = self._cfg(capital=10_000, risk_per_trade_pct=1.0)
        # ₹100 stock, ₹1 SL width → ₹100 risk budget, 100 shares.
        # But 100 × ₹100 = ₹10k notional == capital exactly. OK.
        out = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=99.0, config=cfg,
        )
        assert out["quantity"] >= 1

    def test_zero_risk_pct_returns_zero_qty(self):
        cfg = self._cfg(risk_per_trade_pct=0.0)
        out = risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=95.0, config=cfg,
        )
        assert out["quantity"] == 0
        assert out["reason"] == "RISK_PCT_NON_POSITIVE"



# ---------------------------------------------------------------------------
# regime_gate
# ---------------------------------------------------------------------------

class TestRegimeGate:

    def test_disabled_gate_always_allows(self):
        cfg = {"regime": {"gate_enabled": False}}
        assert regime_gate.is_gate_enabled(cfg) is False
        ok, why = regime_gate.check_signal_allowed("utbot", "chop", cfg)
        assert ok and why == ""

    def test_disabled_by_default(self):
        """gate_enabled defaults to False for Sprint-1.5 backwards compat."""
        assert regime_gate.is_gate_enabled({}) is False
        ok, _ = regime_gate.check_signal_allowed("utbot", "high_vol_chop", {})
        assert ok is True

    def test_enabled_gate_blocks_utbot_in_chop(self):
        cfg = {"regime": {"gate_enabled": True}}   # default policy: utbot off in chop
        ok, why = regime_gate.check_signal_allowed("utbot", "chop", cfg)
        assert ok is False
        assert "chop" in why
        assert "utbot" in why

    def test_enabled_gate_allows_sr_in_chop(self):
        """S/R is enabled in chop per default policy."""
        cfg = {"regime": {"gate_enabled": True}}
        ok, _ = regime_gate.check_signal_allowed("sr", "chop", cfg)
        assert ok is True

    def test_enabled_gate_blocks_everything_in_high_vol_chop(self):
        cfg = {"regime": {"gate_enabled": True}}
        for engine in ("utbot", "sr"):
            ok, why = regime_gate.check_signal_allowed(engine, "high_vol_chop", cfg)
            assert ok is False, f"engine={engine} should be blocked in high_vol_chop"

    def test_enabled_gate_allows_trending_up_for_both(self):
        cfg = {"regime": {"gate_enabled": True}}
        for engine in ("utbot", "sr"):
            ok, _ = regime_gate.check_signal_allowed(engine, "trending_up", cfg)
            assert ok is True

    def test_config_override_flips_default(self):
        """User can force UT-Bot ON in chop via config."""
        cfg = {
            "regime": {
                "gate_enabled": True,
                "policy": {"chop": {"utbot": True}},
            }
        }
        ok, _ = regime_gate.check_signal_allowed("utbot", "chop", cfg)
        assert ok is True

    def test_case_insensitive_engine_and_regime(self):
        cfg = {"regime": {"gate_enabled": True}}
        ok, _ = regime_gate.check_signal_allowed("UTBOT", "TRENDING_UP", cfg)
        assert ok is True
        ok, _ = regime_gate.check_signal_allowed("UtBoT", "CHOP", cfg)
        assert ok is False

    def test_unknown_regime_permissive(self):
        """When we can't classify, don't accidentally block everything."""
        cfg = {"regime": {"gate_enabled": True}}
        ok, _ = regime_gate.check_signal_allowed("utbot", "unknown", cfg)
        assert ok is True

