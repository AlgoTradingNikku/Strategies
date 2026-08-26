"""
tests/test_signal_grader.py
===========================
Sprint-3 unit tests for:

  * ``signal_grader``  — the A/B/C/D five-factor grader and the min-grade gate
  * ``risk_limits.get_grade_multiplier``      — grade → risk multiplier
  * ``risk_limits.compute_quantity_risk_based`` with ``grade=`` — sizing impact
  * ``risk_limits.check_portfolio_exposure``  — rupee-notional portfolio cap

All tests are pure: no disk, no network, no trade_db. Where a function would
normally read ``trade_db.get_open_positions()`` we pass ``open_positions``
explicitly so the DB is never touched.
"""

from __future__ import annotations

import pytest

import risk_limits
import signal_grader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(**overrides) -> dict:
    """A mid-quality BUY signal. Every factor is deliberately mediocre so
    individual tests can move ONE factor and observe a clean score delta."""
    base = {
        "signal": "BUY",
        "adx": 27.5,                      # midway between floor 20 and strong 35
        "rs_ratio": 1.05,                 # halfway to rs_strong 1.10
        "close": 100.0,
        "sr_zones": [[101.75, 98.25]],    # 1.75% away -> midway near 0.5 / far 3.0
        "mtf": {"trend": "neutral"},      # 0.6 credit
        "triggered": ["UT Bot Buy"],      # single engine -> 0.5
    }
    base.update(overrides)
    return base


def _solo(factor: str) -> dict:
    """Config isolating one factor at weight 100 so score == that factor's
    credit x 100. Makes each factor's maths directly assertable."""
    weights = {k: 0 for k in ("adx", "rs", "sr_proximity", "mtf", "engine")}
    weights[factor] = 100
    return {"signal_grading": {"weights": weights}}


# ---------------------------------------------------------------------------
# Per-factor behaviour
# ---------------------------------------------------------------------------

class TestGraderFactors:

    def test_all_factors_maxed_gives_grade_a_and_score_100(self):
        out = signal_grader.grade_signal(_signal(
            adx=40.0,                       # >= adx_strong
            rs_ratio=1.25,                  # >= rs_strong
            sr_zones=[[100.3, 99.7]],       # 0.30% -> within sr_near_pct
            mtf={"trend": "bullish"},       # confirms a BUY
            triggered=["UT Bot Buy", "S/R Bounce"],
        ), {})
        assert out["grade"] == "A"
        assert out["score"] == 100.0
        assert out["reason"] == "OK"
        # Every factor should report full credit in the breakdown.
        for factor, info in out["breakdown"].items():
            assert info["credit"] == pytest.approx(1.0), factor

    def test_all_factors_floored_gives_grade_d(self):
        out = signal_grader.grade_signal(_signal(
            adx=12.0,                       # below adx_floor
            rs_ratio=0.80,                  # a BUY that is UNDER-performing
            sr_zones=[[130.0, 70.0]],       # 30% away -> beyond sr_far_pct
            mtf={"trend": "bearish"},       # counter-trend for a BUY
            triggered=["something odd"],    # 0.25 credit
        ), {})
        assert out["grade"] == "D"
        assert out["score"] < 45.0

    def test_adx_scales_linearly_between_floor_and_strong(self):
        cfg = _solo("adx")
        assert signal_grader.grade_signal(_signal(adx=20.0), cfg)["score"] == 0.0
        assert signal_grader.grade_signal(_signal(adx=27.5), cfg)["score"] == 50.0
        assert signal_grader.grade_signal(_signal(adx=35.0), cfg)["score"] == 100.0
        # Clamped beyond the ends -- no >100 or <0 leakage.
        assert signal_grader.grade_signal(_signal(adx=90.0), cfg)["score"] == 100.0

    def test_rs_is_direction_aware(self):
        """RS 0.85 is bad for a BUY but good for a SELL, and vice-versa."""
        cfg = _solo("rs")
        assert signal_grader.grade_signal(_signal(signal="BUY",  rs_ratio=0.85), cfg)["score"] == 0.0
        assert signal_grader.grade_signal(_signal(signal="SELL", rs_ratio=0.85), cfg)["score"] == 100.0
        assert signal_grader.grade_signal(_signal(signal="BUY",  rs_ratio=1.15), cfg)["score"] == 100.0
        assert signal_grader.grade_signal(_signal(signal="SELL", rs_ratio=1.15), cfg)["score"] == 0.0

    def test_sr_proximity_rewards_being_near_a_zone(self):
        cfg = _solo("sr_proximity")
        near = signal_grader.grade_signal(_signal(close=100.0, sr_zones=[[100.2, 99.8]]), cfg)
        far  = signal_grader.grade_signal(_signal(close=100.0, sr_zones=[[110.0, 90.0]]), cfg)
        assert near["score"] == 100.0
        assert far["score"] == 0.0

    def test_sr_proximity_accepts_dict_and_3_tuple_zone_shapes(self):
        """``compute_sr_signals`` has emitted both [hi, lo] and
        [strength, hi, lo]; the frontend uses {high, low}. All must parse."""
        cfg = _solo("sr_proximity")
        for zones in (
            [[100.2, 99.8]],                    # [hi, lo]
            [[5, 100.2, 99.8]],                 # [strength, hi, lo]
            [{"high": 100.2, "low": 99.8}],     # dict form
        ):
            out = signal_grader.grade_signal(_signal(close=100.0, sr_zones=zones), cfg)
            assert out["score"] == 100.0, zones

    def test_mtf_neutral_earns_partial_credit(self):
        cfg = _solo("mtf")
        assert signal_grader.grade_signal(_signal(mtf={"trend": "bullish"}), cfg)["score"] == 100.0
        # mtf_neutral_credit default is 0.6
        assert signal_grader.grade_signal(_signal(mtf={"trend": "neutral"}), cfg)["score"] == 60.0
        assert signal_grader.grade_signal(_signal(mtf={"trend": "bearish"}), cfg)["score"] == 0.0

    def test_mtf_reads_flattened_mtf_trend_key(self):
        """signal_db rows store the flattened ``mtf_trend`` column rather than
        the nested ``mtf`` dict; the grader must accept either shape."""
        cfg = _solo("mtf")
        sig = _signal(mtf=None, mtf_trend="bullish")
        assert signal_grader.grade_signal(sig, cfg)["score"] == 100.0

    def test_engine_agreement_beats_single_engine(self):
        cfg = _solo("engine")
        both = signal_grader.grade_signal(
            _signal(triggered=["UT Bot Buy", "S/R Support Bounce"]), cfg)
        assert both["score"] == 100.0
        assert signal_grader.grade_signal(_signal(triggered=["UT Bot Buy"]), cfg)["score"] == 50.0
        assert signal_grader.grade_signal(_signal(triggered=["S/R Bounce"]), cfg)["score"] == 50.0


# ---------------------------------------------------------------------------
# Fail-open / robustness
# ---------------------------------------------------------------------------

class TestGraderFailOpen:

    def test_completely_empty_signal_grades_c_not_d(self):
        """With every input missing, all five factors earn ``unknown_credit``
        (0.5) -> score 50 -> grade C. This is the whole point of failing open:
        a data outage must not mass-downgrade the universe to D."""
        out = signal_grader.grade_signal({}, {})
        assert out["score"] == 50.0
        assert out["grade"] == "C"

    def test_missing_single_factor_earns_half_credit(self):
        cfg = _solo("rs")
        out = signal_grader.grade_signal(_signal(rs_ratio=None), cfg)
        assert out["score"] == 50.0
        assert "unavailable" in out["breakdown"]["rs"]["detail"].lower()

    def test_unknown_credit_is_configurable(self):
        cfg = _solo("rs")
        cfg["signal_grading"]["unknown_credit"] = 0.0
        out = signal_grader.grade_signal(_signal(rs_ratio=None), cfg)
        assert out["score"] == 0.0

    @pytest.mark.parametrize("bad", ["abc", float("nan"), float("inf"), [], {}])
    def test_non_numeric_inputs_do_not_raise(self, bad):
        out = signal_grader.grade_signal(_signal(adx=bad, rs_ratio=bad), {})
        assert out["grade"] in signal_grader.GRADE_ORDER
        assert 0.0 <= out["score"] <= 100.0

    def test_none_config_is_accepted(self):
        out = signal_grader.grade_signal(_signal(), None)
        assert out["grade"] in signal_grader.GRADE_ORDER

    def test_disabled_grading_returns_permissive_a(self):
        """Disabling grading must not accidentally block trades, so it returns
        the BEST grade — that way any min-grade gate becomes a no-op."""
        cfg = {"signal_grading": {"enabled": False}}
        out = signal_grader.grade_signal(_signal(adx=1.0), cfg)
        assert out["grade"] == "A"
        assert out["reason"] == "GRADING_DISABLED"

    def test_all_weights_zero_returns_neutral_c(self):
        cfg = {"signal_grading": {"weights": {
            "adx": 0, "rs": 0, "sr_proximity": 0, "mtf": 0, "engine": 0}}}
        out = signal_grader.grade_signal(_signal(), cfg)
        assert out["grade"] == "C"
        assert out["reason"] == "NO_ACTIVE_WEIGHTS"

    def test_weights_not_summing_to_100_are_normalised(self):
        """Two equally-weighted factors at weight 7 each must behave exactly
        like weight 50 each — the composite is divided by the weight total."""
        cfg = {"signal_grading": {"weights": {
            "adx": 7, "mtf": 7, "rs": 0, "sr_proximity": 0, "engine": 0}}}
        out = signal_grader.grade_signal(
            _signal(adx=40.0, mtf={"trend": "bullish"}), cfg)
        assert out["score"] == 100.0

    def test_unknown_weight_keys_are_ignored(self):
        cfg = {"signal_grading": {"weights": {"adx": 100, "rs": 0,
                                              "sr_proximity": 0, "mtf": 0,
                                              "engine": 0, "bogus": 500}}}
        out = signal_grader.grade_signal(_signal(adx=40.0), cfg)
        assert out["score"] == 100.0


# ---------------------------------------------------------------------------
# Grade thresholds
# ---------------------------------------------------------------------------

class TestGradeThresholds:

    @pytest.mark.parametrize("adx_for_score, expected", [
        (35.0, "A"),   # 100 -> A
        (20.0, "D"),   # 0   -> D
    ])
    def test_default_threshold_boundaries(self, adx_for_score, expected):
        cfg = _solo("adx")
        out = signal_grader.grade_signal(_signal(adx=adx_for_score), cfg)
        assert out["grade"] == expected

    def test_custom_thresholds_are_honoured(self):
        cfg = _solo("adx")
        # Make A trivially easy: anything >= 10 is an A.
        cfg["signal_grading"]["thresholds"] = {"A": 10, "B": 5, "C": 1}
        out = signal_grader.grade_signal(_signal(adx=23.0), cfg)   # score 20
        assert out["grade"] == "A"

    def test_score_exactly_on_a_threshold_earns_that_grade(self):
        """A >= 75 is inclusive, so a score of exactly 75.0 must grade A."""
        cfg = _solo("adx")
        # adx credit 0.75 -> score 75.0. floor=20, strong=35, span=15.
        out = signal_grader.grade_signal(_signal(adx=20.0 + 0.75 * 15.0), cfg)
        assert out["score"] == 75.0
        assert out["grade"] == "A"



# ---------------------------------------------------------------------------
# Min-grade gate
# ---------------------------------------------------------------------------

class TestMeetsMinGrade:

    def test_default_min_grade_is_d_and_never_blocks(self):
        """Sprint 3 ships observe-only: the default must pass EVERY grade."""
        assert signal_grader.get_min_grade({}) == "D"
        for g in signal_grader.GRADE_ORDER:
            ok, reason = signal_grader.meets_min_grade(g, {})
            assert ok is True
            assert reason == ""

    @pytest.mark.parametrize("grade, expected_ok", [
        ("A", True),    # better than B -> pass
        ("B", True),    # equal to B    -> pass (inclusive)
        ("C", False),
        ("D", False),
    ])
    def test_min_grade_b_blocks_c_and_d(self, grade, expected_ok):
        cfg = {"signal_grading": {"min_grade_to_trade": "B"}}
        ok, reason = signal_grader.meets_min_grade(grade, cfg)
        assert ok is expected_ok
        if not ok:
            assert grade in reason and "min_grade_to_trade" in reason

    def test_min_grade_a_only_passes_a(self):
        cfg = {"signal_grading": {"min_grade_to_trade": "A"}}
        assert signal_grader.meets_min_grade("A", cfg)[0] is True
        assert signal_grader.meets_min_grade("B", cfg)[0] is False

    def test_grade_is_case_and_whitespace_insensitive(self):
        cfg = {"signal_grading": {"min_grade_to_trade": "b"}}
        assert signal_grader.meets_min_grade(" a ", cfg)[0] is True
        assert signal_grader.meets_min_grade("c", cfg)[0] is False

    def test_invalid_min_grade_config_falls_back_to_no_gating(self):
        cfg = {"signal_grading": {"min_grade_to_trade": "Z"}}
        assert signal_grader.get_min_grade(cfg) == "D"
        assert signal_grader.meets_min_grade("D", cfg)[0] is True

    @pytest.mark.parametrize("grade", [None, "", "X", 42])
    def test_unknown_grade_fails_open(self, grade):
        """A grader hiccup must never halt trading, even under a strict gate."""
        cfg = {"signal_grading": {"min_grade_to_trade": "A"}}
        ok, _ = signal_grader.meets_min_grade(grade, cfg)
        assert ok is True

    def test_gate_is_bypassed_when_grading_disabled(self):
        cfg = {"signal_grading": {"enabled": False, "min_grade_to_trade": "A"}}
        assert signal_grader.meets_min_grade("D", cfg)[0] is True


# ---------------------------------------------------------------------------
# risk_limits.get_grade_multiplier
# ---------------------------------------------------------------------------

class TestGradeMultiplier:

    def test_disabled_by_default_returns_1(self):
        for g in signal_grader.GRADE_ORDER:
            assert risk_limits.get_grade_multiplier(g, {}) == 1.0

    @pytest.mark.parametrize("grade, expected", [
        ("A", 1.5), ("B", 1.0), ("C", 0.75), ("D", 0.5),
    ])
    def test_enabled_uses_default_table(self, grade, expected):
        cfg = {"risk_limits": {"grade_multiplier_enabled": True}}
        assert risk_limits.get_grade_multiplier(grade, cfg) == expected

    def test_user_overrides_are_applied(self):
        cfg = {"risk_limits": {
            "grade_multiplier_enabled": True,
            "grade_multipliers": {"a": 2.0},      # lower-case key must work
        }}
        assert risk_limits.get_grade_multiplier("A", cfg) == 2.0
        # Unspecified grades keep their defaults.
        assert risk_limits.get_grade_multiplier("C", cfg) == 0.75

    def test_multiplier_is_capped_at_3x(self):
        """Guards the ``A: 15`` (meant 1.5) decimal typo."""
        cfg = {"risk_limits": {"grade_multiplier_enabled": True,
                               "grade_multipliers": {"A": 15.0}}}
        assert risk_limits.get_grade_multiplier("A", cfg) == 3.0

    @pytest.mark.parametrize("bad", [0, -1.0, "abc", None])
    def test_bad_multiplier_values_degrade_to_safe_defaults(self, bad):
        cfg = {"risk_limits": {"grade_multiplier_enabled": True,
                               "grade_multipliers": {"A": bad}}}
        out = risk_limits.get_grade_multiplier("A", cfg)
        # Either the sanitised default (1.5) or the neutral 1.0 — never 0 or
        # negative, which would silently zero out position size.
        assert out > 0

    def test_none_or_unknown_grade_returns_1(self):
        cfg = {"risk_limits": {"grade_multiplier_enabled": True}}
        assert risk_limits.get_grade_multiplier(None, cfg) == 1.0
        assert risk_limits.get_grade_multiplier("Z", cfg) == 1.0



# ---------------------------------------------------------------------------
# compute_quantity_risk_based with grade=
# ---------------------------------------------------------------------------

class TestGradeAwareSizing:

    # capital 1L, risk 1% = ₹1,000 budget, ₹10/share risk -> 100 shares base.
    BASE = {"risk_limits": {"sizing_mode": "risk_based",
                            "capital": 1_00_000,
                            "risk_per_trade_pct": 1.0,
                            "capital_allow_unlimited": True}}

    def _size(self, cfg, grade=None):
        return risk_limits.compute_quantity_risk_based(
            entry_price=100.0, stop_loss=90.0, config=cfg, grade=grade,
        )

    def test_grade_ignored_when_multiplier_disabled(self):
        """Backwards compatibility: passing a grade must not change sizing
        unless the operator has explicitly opted in."""
        base = self._size(self.BASE)
        for g in signal_grader.GRADE_ORDER:
            assert self._size(self.BASE, g)["quantity"] == base["quantity"]

    def test_a_grade_scales_size_up_and_d_grade_down(self):
        cfg = {"risk_limits": {**self.BASE["risk_limits"],
                               "grade_multiplier_enabled": True}}
        assert self._size(cfg, "B")["quantity"] == 100    # 1.0x
        assert self._size(cfg, "A")["quantity"] == 150    # 1.5x
        assert self._size(cfg, "C")["quantity"] == 75     # 0.75x
        assert self._size(cfg, "D")["quantity"] == 50     # 0.5x

    def test_sizing_result_reports_grade_provenance(self):
        cfg = {"risk_limits": {**self.BASE["risk_limits"],
                               "grade_multiplier_enabled": True}}
        out = self._size(cfg, "A")
        assert out["grade"] == "A"
        assert out["grade_multiplier"] == 1.5
        assert out["base_risk_pct"] == 1.0
        assert out["effective_risk_pct"] == 1.5
        # Actual risk taken should match the scaled budget: 150 x ₹10 = ₹1,500.
        assert out["risk_amount"] == pytest.approx(1500.0)
        assert out["risk_pct"] == pytest.approx(1.5, abs=0.01)

    def test_grade_does_not_affect_capital_pct_mode(self):
        """capital_pct sizing has no risk budget to scale, so the grade is
        irrelevant there — asserted so nobody 'helpfully' wires it in later."""
        cfg = {"risk_limits": {"sizing_mode": "capital_pct",
                               "capital": 1_00_000,
                               "grade_multiplier_enabled": True}}
        a = risk_limits.compute_quantity_risk_based(100.0, 90.0, cfg, grade="A")
        d = risk_limits.compute_quantity_risk_based(100.0, 90.0, cfg, grade="D")
        assert a["quantity"] == d["quantity"] == 1000

    def test_grade_kwarg_is_optional(self):
        """Every pre-Sprint-3 call site omits ``grade`` — must still work."""
        out = risk_limits.compute_quantity_risk_based(100.0, 90.0, self.BASE)
        assert out["quantity"] == 100
        assert out["reason"] == "OK"



# ---------------------------------------------------------------------------
# Portfolio exposure cap
# ---------------------------------------------------------------------------

def _pos(qty: int, entry: float, symbol: str = "TCS") -> dict:
    return {"symbol": symbol, "quantity": qty, "entry_price": entry}


class TestPortfolioExposure:

    # capital 1L, cap 300% -> ₹3,00,000 budget.
    CFG = {"risk_limits": {"enabled": True, "capital": 1_00_000,
                           "max_portfolio_exposure_pct": 300}}

    def test_disabled_when_risk_limits_disabled(self):
        cfg = {"risk_limits": {"enabled": False, "capital": 1_00_000,
                               "max_portfolio_exposure_pct": 1}}
        ok, reason = risk_limits.check_portfolio_exposure(
            cfg, new_notional=99_00_000, open_positions=[])
        assert ok is True
        assert reason == ""

    def test_disabled_when_cap_not_configured(self):
        cfg = {"risk_limits": {"enabled": True, "capital": 1_00_000}}
        ok, _ = risk_limits.check_portfolio_exposure(
            cfg, new_notional=99_00_000, open_positions=[])
        assert ok is True

    def test_null_cap_is_treated_as_disabled(self):
        cfg = {"risk_limits": {"enabled": True, "capital": 1_00_000,
                               "max_portfolio_exposure_pct": None}}
        ok, _ = risk_limits.check_portfolio_exposure(
            cfg, new_notional=99_00_000, open_positions=[])
        assert ok is True

    def test_allows_trade_within_budget(self):
        # Open ₹2,00,000 + new ₹50,000 = ₹2,50,000 < ₹3,00,000 budget.
        ok, reason = risk_limits.check_portfolio_exposure(
            self.CFG, new_notional=50_000,
            open_positions=[_pos(1000, 100.0), _pos(500, 200.0)],
        )
        assert ok is True
        assert reason == ""

    def test_blocks_trade_that_breaches_budget(self):
        # Open ₹2,50,000 + new ₹1,00,000 = ₹3,50,000 > ₹3,00,000 budget.
        ok, reason = risk_limits.check_portfolio_exposure(
            self.CFG, new_notional=1_00_000,
            open_positions=[_pos(2500, 100.0)],
        )
        assert ok is False
        assert "portfolio exposure cap" in reason
        assert "3,50,000" in reason or "350,000" in reason

    def test_boundary_exactly_at_budget_is_allowed(self):
        """The cap is a ceiling, not an exclusive bound — landing exactly on
        ₹3,00,000 must pass, otherwise float dust makes the limit unreachable."""
        ok, _ = risk_limits.check_portfolio_exposure(
            self.CFG, new_notional=1_00_000,
            open_positions=[_pos(2000, 100.0)],   # ₹2,00,000
        )
        assert ok is True

    def test_existing_exposure_alone_can_block_a_zero_notional_check(self):
        ok, _ = risk_limits.check_portfolio_exposure(
            self.CFG, new_notional=0.0,
            open_positions=[_pos(4000, 100.0)],   # ₹4,00,000 already open
        )
        assert ok is False

    def test_malformed_position_rows_are_skipped_not_fatal(self):
        snap = risk_limits.compute_portfolio_exposure(
            self.CFG,
            open_positions=[
                _pos(1000, 100.0),                        # ₹1,00,000 — counts
                {"symbol": "X"},                          # no qty/price
                {"quantity": "abc", "entry_price": "xyz"},  # non-numeric
                {"quantity": None, "entry_price": None},
                {"quantity": -5, "entry_price": 100.0},    # negative qty ignored
            ],
        )
        assert snap["exposure_rupees"] == pytest.approx(1_00_000.0)
        assert snap["positions"] == 5

    def test_compute_portfolio_exposure_snapshot_fields(self):
        snap = risk_limits.compute_portfolio_exposure(
            self.CFG, open_positions=[_pos(1500, 100.0)])
        assert snap["exposure_rupees"] == pytest.approx(1_50_000.0)
        assert snap["budget_rupees"] == pytest.approx(3_00_000.0)
        assert snap["exposure_pct"] == pytest.approx(150.0)
        assert snap["max_pct"] == 300
        assert snap["positions"] == 1
        assert snap["enabled"] is True

    def test_empty_portfolio_reports_zero_exposure(self):
        snap = risk_limits.compute_portfolio_exposure(self.CFG, open_positions=[])
        assert snap["exposure_rupees"] == 0.0
        assert snap["exposure_pct"] == 0.0
        assert snap["positions"] == 0

    def test_non_numeric_cap_disables_the_check(self):
        cfg = {"risk_limits": {"enabled": True, "capital": 1_00_000,
                               "max_portfolio_exposure_pct": "lots"}}
        snap = risk_limits.compute_portfolio_exposure(cfg, open_positions=[])
        assert snap["enabled"] is False
        ok, _ = risk_limits.check_portfolio_exposure(
            cfg, new_notional=99_00_000, open_positions=[])
        assert ok is True

    def test_negative_new_notional_is_floored_at_zero(self):
        ok, _ = risk_limits.check_portfolio_exposure(
            self.CFG, new_notional=-50_000,
            open_positions=[_pos(2000, 100.0)],
        )
        assert ok is True

