"""tests/test_signal_db.py - Sprint 1.5 additions to signal_db.

Covers:
  * regime column tag on logged signals
  * MAE / MFE computation in check_outcomes
  * by_regime / MAE / MFE roll-ups in get_statistics
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pandas as pd
import pytest

import signal_db


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Redirect signal_db._DB_PATH to a tmp file and reset the init flag."""
    db_file = tmp_path / "signals_test.db"
    monkeypatch.setattr(signal_db, "_DB_PATH", db_file)
    monkeypatch.setattr(signal_db, "_db_initialized", False)
    yield db_file


def _sample_signal(symbol="TCS", **overrides):
    base = {
        "symbol": symbol, "signal": "BUY", "close": 100.0,
        "setup_score": 75.0, "score_reasons": ["utbot_buy"],
        "stop_loss": 98.0, "target": 104.0, "risk_reward": 2.0,
        "triggered": ["ut_buy"], "adx": 25.0, "rs_ratio": 1.10,
    }
    base.update(overrides)
    return base


def _fake_fetch_factory(df):
    def _fetch(symbol, timeframe, config):
        return df
    return _fetch


class TestSchemaMigration:

    def test_new_columns_exist_after_init(self, fresh_db):
        conn = signal_db._get_connection({})
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
            assert "regime" in cols
            assert "mae_pct" in cols
            assert "mfe_pct" in cols
        finally:
            conn.close()

    def test_migration_is_idempotent(self, fresh_db):
        signal_db._get_connection({}).close()
        signal_db._db_initialized = False
        signal_db._get_connection({}).close()


class TestLogSignalsBatchRegime:

    def test_regime_persisted_on_every_row(self, fresh_db):
        sigs = [_sample_signal("TCS"), _sample_signal("INFY", signal="SELL")]
        ids = signal_db.log_signals_batch(sigs, timeframe="5m", config={},
                                          regime="trending_up")
        assert len(ids) == 2
        conn = signal_db._get_connection({})
        try:
            rows = conn.execute("SELECT symbol, regime FROM signals ORDER BY id").fetchall()
        finally:
            conn.close()
        assert [r["regime"] for r in rows] == ["trending_up", "trending_up"]

    def test_regime_is_optional_and_defaults_to_null(self, fresh_db):
        signal_db.log_signals_batch([_sample_signal("TCS")], timeframe="5m", config={})
        conn = signal_db._get_connection({})
        try:
            row = conn.execute("SELECT regime FROM signals").fetchone()
        finally:
            conn.close()
        assert row["regime"] is None


class TestCheckOutcomesMAEMFE:

    def _seed_buy_signal(self, entry=100.0):
        """Insert a BUY row and back-date its timestamp past the cutoff."""
        signal_db.log_signals_batch(
            [_sample_signal("TCS", close=entry, stop_loss=95.0, target=110.0)],
            timeframe="5m", config={}, regime="trending_up",
        )
        conn = signal_db._get_connection({})
        try:
            old_ts = (datetime.now() - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE signals SET timestamp = ?", (old_ts,))
            conn.commit()
        finally:
            conn.close()

    def test_mae_mfe_computed_for_buy(self, fresh_db):
        """BUY @100. Bars dip to 97 (MAE=3%) then rally to 108 (MFE=8%),
        never touching 110 target. Expect hit_target=0, mae≈3, mfe≈8."""
        self._seed_buy_signal(entry=100.0)

        future_idx = pd.date_range(datetime.now() + timedelta(hours=1),
                                   periods=3, freq="5min")
        future_df = pd.DataFrame({
            "open":  [100.0, 97.0, 105.0],
            "high":  [101.0, 98.0, 108.0],
            "low":   [ 99.0, 97.0, 104.0],
            "close": [100.0, 97.5, 106.0],
        }, index=future_idx)

        updated = signal_db.check_outcomes(hours=4, config={},
                                           fetch_fn=_fake_fetch_factory(future_df))
        assert updated == 1

        conn = signal_db._get_connection({})
        try:
            row = conn.execute(
                "SELECT outcome_hit_target, outcome_hit_stop, mae_pct, mfe_pct "
                "FROM signals"
            ).fetchone()
        finally:
            conn.close()

        assert row["outcome_hit_target"] == 0
        assert row["outcome_hit_stop"]   == 0
        assert row["mae_pct"] == pytest.approx(3.0, abs=0.001)
        assert row["mfe_pct"] == pytest.approx(8.0, abs=0.001)


class TestGetStatisticsByRegime:

    def test_by_regime_buckets_and_win_rate(self, fresh_db):
        # Two trending_up (1 win, 1 loss) + one chop (win)
        for regime in ("trending_up", "trending_up", "chop"):
            signal_db.log_signals_batch([_sample_signal("TCS")], timeframe="5m",
                                        config={}, regime=regime)
        conn = signal_db._get_connection({})
        try:
            conn.execute("""
                UPDATE signals SET outcome_checked = 1,
                                   outcome_pnl_pct = CASE id
                                       WHEN 1 THEN 2.0
                                       WHEN 2 THEN -1.5
                                       WHEN 3 THEN 3.0
                                   END,
                                   outcome_hit_target = CASE id
                                       WHEN 2 THEN 0 ELSE 1
                                   END,
                                   mae_pct = 1.0,
                                   mfe_pct = 4.0
            """)
            conn.commit()
        finally:
            conn.close()

        stats = signal_db.get_statistics(days=30, config={})
        assert stats["total_signals"]   == 3
        assert stats["checked_signals"] == 3

        by_reg = stats["by_regime"]
        assert set(by_reg.keys()) == {"trending_up", "chop"}
        assert by_reg["trending_up"]["total"] == 2
        assert by_reg["trending_up"]["wins"]  == 1
        assert by_reg["trending_up"]["win_rate"] == 50.0
        assert by_reg["chop"]["total"]    == 1
        assert by_reg["chop"]["win_rate"] == 100.0

        assert stats["avg_mae_winners"] == pytest.approx(1.0)
        assert stats["avg_mfe_winners"] == pytest.approx(4.0)

    def test_null_regime_bucketed_as_unknown(self, fresh_db):
        signal_db.log_signals_batch([_sample_signal("TCS")], timeframe="5m", config={})
        conn = signal_db._get_connection({})
        try:
            conn.execute("UPDATE signals SET outcome_checked = 1, "
                         "outcome_hit_target = 1, outcome_pnl_pct = 1.0")
            conn.commit()
        finally:
            conn.close()

        stats = signal_db.get_statistics(days=30, config={})
        assert "unknown" in stats["by_regime"]
        assert stats["by_regime"]["unknown"]["total"] == 1



# ---------------------------------------------------------------------------
# Sprint 3 — grade persistence + by_grade statistics
# ---------------------------------------------------------------------------

class TestGradePersistence:

    def test_grade_columns_exist_after_init(self, fresh_db):
        conn = signal_db._get_connection({})
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
            assert "grade" in cols
            assert "grade_score" in cols
        finally:
            conn.close()

    def test_grade_persisted_from_signal_dict(self, fresh_db):
        sigs = [
            _sample_signal("TCS",  grade="A", grade_score=88.5),
            _sample_signal("INFY", grade="C", grade_score=47.0),
        ]
        signal_db.log_signals_batch(sigs, timeframe="5m", config={})
        conn = signal_db._get_connection({})
        try:
            rows = conn.execute(
                "SELECT symbol, grade, grade_score FROM signals ORDER BY id"
            ).fetchall()
            assert [r["grade"] for r in rows] == ["A", "C"]
            assert rows[0]["grade_score"] == pytest.approx(88.5)
            assert rows[1]["grade_score"] == pytest.approx(47.0)
        finally:
            conn.close()

    def test_ungraded_signal_stores_null_grade(self, fresh_db):
        """Pre-Sprint-3 callers omit grade entirely — the insert must still
        succeed and leave NULL rather than raising on a missing key."""
        signal_db.log_signals_batch([_sample_signal("WIPRO")],
                                    timeframe="5m", config={})
        conn = signal_db._get_connection({})
        try:
            row = conn.execute("SELECT grade, grade_score FROM signals").fetchone()
            assert row["grade"] is None
            assert row["grade_score"] is None
        finally:
            conn.close()

    def test_grade_exposed_in_signal_history(self, fresh_db):
        signal_db.log_signals_batch(
            [_sample_signal("TCS", grade="B", grade_score=65.0)],
            timeframe="5m", config={},
        )
        hist = signal_db.get_signal_history(limit=10)
        assert hist[0]["grade"] == "B"
        assert hist[0]["grade_score"] == pytest.approx(65.0)


class TestByGradeStatistics:

    def _log_and_close(self, symbol, grade, pnl_pct, hit_target):
        """Insert one signal and mark its outcome directly, so the roll-up can
        be asserted without running the full check_outcomes price walk."""
        ids = signal_db.log_signals_batch(
            [_sample_signal(symbol, grade=grade, grade_score=70.0)],
            timeframe="5m", config={},
        )
        conn = signal_db._get_connection({})
        try:
            conn.execute(
                "UPDATE signals SET outcome_checked=1, outcome_pnl_pct=?, "
                "outcome_hit_target=? WHERE id=?",
                (pnl_pct, 1 if hit_target else 0, ids[0]),
            )
            conn.commit()
        finally:
            conn.close()

    def test_by_grade_bucket_computes_win_rate(self, fresh_db):
        # Grade A: 2 wins / 2. Grade D: 0 wins / 2.
        self._log_and_close("TCS",   "A",  3.0, True)
        self._log_and_close("INFY",  "A",  1.5, True)
        self._log_and_close("WIPRO", "D", -2.0, False)
        self._log_and_close("HCLT",  "D", -1.0, False)

        stats = signal_db.get_statistics(days=30, config={})
        assert stats["by_grade"]["A"] == {"total": 2, "wins": 2, "win_rate": 100.0}
        assert stats["by_grade"]["D"] == {"total": 2, "wins": 0, "win_rate": 0.0}

    def test_null_grades_bucket_under_unknown(self, fresh_db):
        """Legacy rows must not inflate a real grade's win rate."""
        self._log_and_close("TCS", None, 5.0, True)
        stats = signal_db.get_statistics(days=30, config={})
        assert stats["by_grade"]["unknown"]["total"] == 1
        assert "A" not in stats["by_grade"]

    def test_by_grade_present_even_with_no_signals(self, fresh_db):
        stats = signal_db.get_statistics(days=30, config={})
        assert stats["by_grade"] == {}

