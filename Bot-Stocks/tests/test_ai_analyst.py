"""
test_ai_analyst.py
===================
Unit tests for ai_analyst module and AI signal recommendation integration.
"""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock
import ai_analyst
import signal_db


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Provide an isolated SQLite database file per test."""
    db_file = tmp_path / "test_signals.db"
    monkeypatch.setattr(signal_db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(signal_db, "_db_initialized", False)
    conn = signal_db._get_connection({})
    conn.close()
    return db_file


def test_build_prompt():
    signal = {
        "symbol": "TCS",
        "signal": "BUY",
        "close": 3500.0,
        "setup_score": 85.0,
        "grade": "A",
        "grade_score": 90.0,
        "regime": "trending_up",
        "stop_loss": 3450.0,
        "target": 3600.0,
        "risk_reward": 2.0,
        "score_reasons": ["UTBot Buy", "S/R Support Bounce"],
    }
    prompt = ai_analyst._build_prompt(signal)
    assert "TCS" in prompt
    assert "BUY" in prompt
    assert "Grade A" in prompt
    assert "trending_up" in prompt


def test_ai_analysis_disabled():
    signal = {"symbol": "INFY", "grade": "B"}
    cfg = {"ai_analysis": {"enabled": False}}
    res = ai_analyst.analyze_signal(signal, cfg)
    assert res["ai_recommendation"] == "N/A"
    assert res["ai_score"] is None
    assert res["ai_badge"] is None


def test_ai_analysis_missing_api_key(monkeypatch):
    signal = {"symbol": "RELIANCE", "grade": "A"}
    cfg = {"ai_analysis": {"enabled": True, "provider": "openai", "api_key_env": ""}}  # Empty API key
    res = ai_analyst.analyze_signal(signal, cfg)
    assert res["ai_recommendation"] == "N/A"
    assert "missing" in res["ai_reasoning"].lower()


@patch("ai_analyst._call_openai_compatible_api")
def test_ai_analysis_custom_ibm_ica_endpoint(mock_call, monkeypatch):
    mock_call.return_value = '{"ai_recommendation": "BUY", "ai_score": 88, "ai_badge": "⭐ AI Recommended", "ai_reasoning": "IBM ICA Gemini evaluation clear setup."}'

    signal = {"symbol": "INFY", "grade": "A", "grade_score": 85.0}
    cfg = {
        "ai_analysis": {
            "enabled": True,
            "provider": "openai_compatible",
            "base_url": "https://api.nextgen-beta.ica.ibm.com/ica/v1",
            "model": "gemini-3.6-flash",
            "api_key_env": "test_ica_key_123",  # Direct API key
        }
    }
    res = ai_analyst.analyze_signal(signal, cfg)

    assert res["ai_recommendation"] == "BUY"
    assert res["ai_score"] == 88.0
    assert "IBM ICA Gemini" in res["ai_reasoning"]
    mock_call.assert_called_once()
    assert mock_call.call_args[0][0] == "https://api.nextgen-beta.ica.ibm.com/ica/v1/chat/completions"


@patch("ai_analyst._call_openai_compatible_api")
def test_ai_analysis_openai_success(mock_call, monkeypatch):
    mock_call.return_value = '{"ai_recommendation": "STRONG BUY", "ai_score": 92, "ai_badge": "⭐ High Conviction", "ai_reasoning": "Excellent risk/reward setup."}'

    signal = {"symbol": "TCS", "grade": "A", "grade_score": 90.0}
    cfg = {"ai_analysis": {"enabled": True, "provider": "openai", "api_key_env": "sk-test-key-dummy"}}  # Direct API key
    res = ai_analyst.analyze_signal(signal, cfg)

    assert res["ai_recommendation"] == "STRONG BUY"
    assert res["ai_score"] == 92.0
    assert res["ai_badge"] == "⭐ High Conviction"
    assert "Excellent" in res["ai_reasoning"]


def test_analyze_signals_batch_filtering():
    signals = [
        {"symbol": "SYM_A", "grade": "A", "grade_score": 90.0, "setup_score": 80.0},
        {"symbol": "SYM_B", "grade": "B", "grade_score": 75.0, "setup_score": 70.0},
        {"symbol": "SYM_C", "grade": "C", "grade_score": 55.0, "setup_score": 60.0},
        {"symbol": "SYM_D", "grade": "D", "grade_score": 35.0, "setup_score": 40.0},
    ]

    cfg = {
        "ai_analysis": {
            "enabled": True,
            "eval_min_grade": "B",
            "max_candidates_per_scan": 2,
            "provider": "openai",
        }
    }

    with patch("ai_analyst.analyze_signal") as mock_analyze:
        mock_analyze.return_value = {
            "ai_recommendation": "BUY",
            "ai_score": 85.0,
            "ai_badge": "⭐ AI Recommended",
            "ai_reasoning": "Strong setup.",
        }
        res = ai_analyst.analyze_signals_batch(signals, cfg)

        # Only Grade A and B (2 candidates max) evaluated
        assert mock_analyze.call_count == 2
        assert res[0]["ai_recommendation"] == "BUY"
        assert res[3]["ai_recommendation"] == "N/A"  # Grade D ignored


def test_signal_db_ai_fields_persistence(fresh_db):
    sig = {
        "symbol": "TATAMOTORS",
        "signal": "BUY",
        "close": 950.0,
        "setup_score": 85.0,
        "grade": "A",
        "grade_score": 88.0,
        "ai_recommendation": "STRONG BUY",
        "ai_score": 94.0,
        "ai_badge": "⭐ AI Recommended",
        "ai_reasoning": "Breakout confirmed by volume and RS ratio.",
    }

    ids = signal_db.log_signals_batch([sig], timeframe="5m", config={}, regime="trending_up")
    assert len(ids) == 1

    hist = signal_db.get_signal_history(limit=10)
    assert len(hist) == 1
    row = hist[0]
    assert row["symbol"] == "TATAMOTORS"
    assert row["ai_recommendation"] == "STRONG BUY"
    assert row["ai_score"] == 94.0
    assert row["ai_badge"] == "⭐ AI Recommended"
    assert "Breakout" in row["ai_reasoning"]


def test_update_signal_ai_analysis(fresh_db):
    sig = {
        "symbol": "WIPRO",
        "signal": "BUY",
        "close": 480.0,
        "setup_score": 75.0,
    }
    ids = signal_db.log_signals_batch([sig], timeframe="5m", config={})
    sig_id = ids[0]

    ai_data = {
        "ai_recommendation": "BUY",
        "ai_score": 80.0,
        "ai_badge": "🤖 Evaluated",
        "ai_reasoning": "Solid baseline bounce.",
    }

    ok = signal_db.update_signal_ai_analysis(sig_id, ai_data)
    assert ok is True

    hist = signal_db.get_signal_history(limit=10)
    assert hist[0]["ai_recommendation"] == "BUY"
    assert hist[0]["ai_score"] == 80.0
