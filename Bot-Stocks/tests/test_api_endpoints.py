"""
tests/test_api_endpoints.py
===========================
Sprint 2.5 API-level tests for the dashboard-facing endpoints:

* /api/scan            — now unpacks the Sprint 1.5 6-tuple and exposes
                          `current_regime` + `regime_gate_enabled` at the
                          top level.
* /api/risk/status     — new endpoint aggregating regime-gate state,
                          sizing mode, capital, and today's realised P&L.
* /api/statistics      — Sprint 1.5 `by_regime` + MAE/MFE surface check
                          (regression against dropped fields).

Uses FastAPI's TestClient (no live network) and monkey-patches the
heavyweight collaborators (`scanner.run_scan`, `trade_db` funcs) so tests
stay hermetic and sub-second.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    return TestClient(app_module.app)


class TestApiScan:

    def test_scan_returns_current_regime_and_gate_flag(self, monkeypatch, client):
        """After Sprint 1.5, run_scan yields a 6-tuple. app.py must unpack all
        six items and expose current_regime + regime_gate_enabled to the
        frontend."""
        fake_return = (
            [{"symbol": "TCS", "close": 3400.0, "engine": "utbot"}],
            [],
            "NIFTY50",
            "5m",
            50,
            "trending_up",
        )
        import app as app_module
        monkeypatch.setattr(app_module, "run_scan", lambda *a, **kw: fake_return)
        monkeypatch.setattr(app_module, "load_config",
                            lambda: {"regime": {"gate_enabled": True}})

        resp = client.post("/api/scan")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["current_regime"] == "trending_up"
        assert body["regime_gate_enabled"] is True
        assert body["buy_signals"][0]["engine"] == "utbot"
        assert body["total_scanned"] == 50

    def test_scan_error_still_returns_500_with_detail(self, monkeypatch, client):
        import app as app_module
        def _boom(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(app_module, "run_scan", _boom)
        monkeypatch.setattr(app_module, "load_config", lambda: {})
        resp = client.post("/api/scan")
        assert resp.status_code == 500
        assert "simulated failure" in resp.json()["detail"]



class TestApiRiskStatus:

    def test_returns_defaults_with_gate_off_and_legacy_sizing(self, monkeypatch, client):
        import app as app_module
        monkeypatch.setattr(app_module, "load_config", lambda: {
            "risk_limits": {"sizing_mode": "legacy", "capital": 100000,
                            "risk_per_trade_pct": 1.0},
            "regime": {"gate_enabled": False},
        })
        monkeypatch.setattr(app_module.trade_db,
                            "get_realized_pnl_rupees_since", lambda _iso: 0.0)
        monkeypatch.setattr(app_module.trade_db, "get_open_positions", lambda: [])

        resp = client.get("/api/risk/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["regime_gate_enabled"] is False
        assert body["sizing_mode"] == "legacy"
        assert body["capital"] == 100000.0
        assert body["realized_pnl_today_rupees"] == 0.0
        assert body["open_positions"] == 0

    def test_reflects_gate_on_and_todays_pnl(self, monkeypatch, client):
        import app as app_module
        monkeypatch.setattr(app_module, "load_config", lambda: {
            "risk_limits": {
                "sizing_mode": "risk_based",
                "capital": 200000,
                "risk_per_trade_pct": 1.5,
                "daily_loss_stop_pct": 3.0,
                "daily_loss_stop_rupees": 5000,
            },
            "regime": {"gate_enabled": True},
        })
        monkeypatch.setattr(app_module.trade_db,
                            "get_realized_pnl_rupees_since", lambda _iso: -2450.75)
        monkeypatch.setattr(app_module.trade_db, "get_open_positions",
                            lambda: [{"id": 1}, {"id": 2}])

        resp = client.get("/api/risk/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["regime_gate_enabled"] is True
        assert body["sizing_mode"] == "risk_based"
        assert body["capital"] == 200000.0
        assert body["risk_per_trade_pct"] == 1.5
        assert body["daily_loss_cap_rupees"] == 5000
        assert body["daily_loss_cap_pct"] == 3.0
        assert body["realized_pnl_today_rupees"] == -2450.75
        assert body["realized_pnl_today_pct"] == pytest.approx(-1.225, abs=0.005)
        assert body["open_positions"] == 2

    def test_never_raises_on_downstream_failure(self, monkeypatch, client):
        """The endpoint must degrade gracefully — dashboard cannot afford a 500
        on the status strip poll."""
        import app as app_module

        def _bad_cfg():
            raise RuntimeError("yaml corrupted")
        monkeypatch.setattr(app_module, "load_config", _bad_cfg)

        resp = client.get("/api/risk/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "partial"
        assert "error" in body

    def test_pnl_pct_zero_when_capital_zero(self, monkeypatch, client):
        """When configured capital is 0, validate_capital falls back to the
        safety default (₹1,00,000) — verified in test_risk_sizing.py. This test
        just guards that the endpoint doesn't divide-by-zero and returns a
        finite pct against whatever capital validate_capital chose."""
        import app as app_module
        monkeypatch.setattr(app_module, "load_config", lambda: {
            "risk_limits": {"capital": 0, "sizing_mode": "legacy"},
            "regime": {"gate_enabled": False},
        })
        monkeypatch.setattr(app_module.trade_db,
                            "get_realized_pnl_rupees_since", lambda _iso: 500.0)
        monkeypatch.setattr(app_module.trade_db, "get_open_positions", lambda: [])
        resp = client.get("/api/risk/status")
        body = resp.json()
        # Finite (no ZeroDivisionError), ₹ value preserved.
        assert body["realized_pnl_today_rupees"] == 500.0
        assert isinstance(body["realized_pnl_today_pct"], (int, float))
        # Capital was defaulted (not 0), so pct is small but finite.
        assert body["capital"] and body["capital"] > 0




class TestApiStatistics:

    def test_statistics_surfaces_by_regime_and_mae_mfe(self, monkeypatch, client):
        import app as app_module
        fake_stats = {
            "total_signals": 4,
            "checked_signals": 4,
            "by_score_tier":  {"70-100": {"total": 2, "wins": 1, "win_rate": 50.0}},
            "by_signal_type": {"BUY": {"total": 3, "wins": 2, "win_rate": 66.7}},
            "by_timeframe":   {"5m": {"total": 4, "wins": 2, "win_rate": 50.0}},
            "by_regime": {
                "trending_up": {"total": 2, "wins": 2, "win_rate": 100.0},
                "chop":        {"total": 2, "wins": 0, "win_rate": 0.0},
            },
            "avg_rr_winners": 2.4, "avg_rr_losers": 1.1,
            "avg_mae_winners": -0.5, "avg_mae_losers": -1.8,
            "avg_mfe_winners":  2.9, "avg_mfe_losers":  0.6,
        }
        monkeypatch.setattr(app_module, "get_statistics", lambda **kw: fake_stats)
        monkeypatch.setattr(app_module, "load_config", lambda: {})

        resp = client.get("/api/statistics?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        stats = body["statistics"]

        # Regression guards — these fields must survive the API round-trip.
        assert "by_regime" in stats
        assert stats["by_regime"]["trending_up"]["win_rate"] == 100.0
        assert stats["by_regime"]["chop"]["total"] == 2
        for k in ("avg_mae_winners", "avg_mae_losers",
                  "avg_mfe_winners", "avg_mfe_losers"):
            assert k in stats



# ---------------------------------------------------------------------------
# Sprint 3 — /api/risk/status grading + exposure fields
# ---------------------------------------------------------------------------

class TestApiRiskStatusSprint3:

    def test_exposes_grading_and_exposure_fields(self, monkeypatch, client):
        """Sprint 3 additions: min_grade_to_trade, grade_multiplier_enabled,
        and a portfolio_exposure snapshot must all surface on the status
        endpoint so the dashboard can render the risk strip."""
        import app as app_module
        monkeypatch.setattr(app_module, "load_config", lambda: {
            "risk_limits": {
                "sizing_mode": "risk_based",
                "capital": 1_00_000,
                "risk_per_trade_pct": 1.0,
                "grade_multiplier_enabled": True,
                "max_portfolio_exposure_pct": 300,
            },
            "signal_grading": {"min_grade_to_trade": "B", "enabled": True},
            "regime": {"gate_enabled": False},
        })
        monkeypatch.setattr(app_module.trade_db,
                            "get_realized_pnl_rupees_since", lambda _iso: 0.0)
        # ₹1,50,000 open notional -> 150% of the ₹1L per-trade capital.
        monkeypatch.setattr(app_module.trade_db, "get_open_positions",
                            lambda: [{"symbol": "TCS", "quantity": 1500,
                                      "entry_price": 100.0}])

        resp = client.get("/api/risk/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["min_grade_to_trade"] == "B"
        assert body["grading_enabled"] is True
        assert body["grade_multiplier_enabled"] is True

        snap = body["portfolio_exposure"]
        assert snap is not None
        assert snap["exposure_rupees"] == pytest.approx(1_50_000.0)
        assert snap["budget_rupees"] == pytest.approx(3_00_000.0)
        assert snap["exposure_pct"] == pytest.approx(150.0)
        assert snap["max_pct"] == 300
        assert snap["positions"] == 1
        assert snap["enabled"] is True

    def test_grading_defaults_when_config_block_absent(self, monkeypatch, client):
        """A config without signal_grading must still yield safe defaults:
        'D' (no gating) and a disabled exposure check."""
        import app as app_module
        monkeypatch.setattr(app_module, "load_config", lambda: {
            "risk_limits": {"sizing_mode": "legacy"},
            "regime": {"gate_enabled": False},
        })
        monkeypatch.setattr(app_module.trade_db,
                            "get_realized_pnl_rupees_since", lambda _iso: 0.0)
        monkeypatch.setattr(app_module.trade_db, "get_open_positions", lambda: [])

        resp = client.get("/api/risk/status")
        body = resp.json()
        assert body["min_grade_to_trade"] == "D"
        assert body["grade_multiplier_enabled"] is False
        # max_portfolio_exposure_pct absent -> snapshot reports enabled=False.
        assert body["portfolio_exposure"]["enabled"] is False



    def test_update_grading_config_persistence(self, monkeypatch, client, tmp_path):
        """POST /api/config/grading updates config.yml and returns new state."""
        import app as app_module
        
        # Mock a temporary config file
        config_file = tmp_path / "config.yml"
        config_file.write_text("""
risk_limits:
  sizing_mode: risk_based
  grade_multiplier_enabled: false
signal_grading:
  enabled: true
  min_grade_to_trade: D
regime:
  gate_enabled: false
""")
        
        monkeypatch.setattr(app_module, "_bot_dir", tmp_path)
        monkeypatch.setattr(app_module, "load_config", 
                           lambda: __import__("yaml").safe_load(config_file.read_text()))
        
        # Enable multiplier and raise min grade to B
        resp = client.post("/api/config/grading",
                          params={
                              "grade_multiplier_enabled": True,
                              "min_grade_to_trade": "B"
                          })
        
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["grade_multiplier_enabled"] is True
        assert body["min_grade_to_trade"] == "B"
        
        # Verify persistence: re-read config
        import yaml
        persisted = yaml.safe_load(config_file.read_text())
        assert persisted["risk_limits"]["grade_multiplier_enabled"] is True
        assert persisted["signal_grading"]["min_grade_to_trade"] == "B"

    def test_update_grading_config_invalid_grade(self, monkeypatch, client, tmp_path):
        """POST /api/config/grading rejects invalid min_grade_to_trade."""
        import app as app_module
        config_file = tmp_path / "config.yml"
        config_file.write_text("risk_limits: {}\nsignal_grading: {}\n")
        
        monkeypatch.setattr(app_module, "_bot_dir", tmp_path)
        monkeypatch.setattr(app_module, "load_config", 
                           lambda: __import__("yaml").safe_load(config_file.read_text()))
        
        resp = client.post("/api/config/grading",
                          params={"min_grade_to_trade": "Z"})
        
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        assert "Invalid" in body["message"]

