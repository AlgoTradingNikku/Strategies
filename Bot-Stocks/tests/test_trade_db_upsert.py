"""
tests/test_trade_db_upsert.py
==============================
Tests for open_position_db upsert and position accumulation logic.
"""

import pytest
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import trade_db


@pytest.fixture
def temp_trade_db(tmp_path, monkeypatch):
    """Redirect trade_db._DB_PATH to a temporary SQLite database."""
    db_file = tmp_path / "trades_test.db"
    monkeypatch.setattr(trade_db, "_DB_PATH", db_file)
    monkeypatch.setattr(trade_db, "_db_initialized", False)
    return db_file


def test_open_position_db_initial_insert(temp_trade_db):
    pos = {
        "order_id": "ORD1001",
        "symbol": "YESBANK",
        "exchange": "NSE",
        "direction": "BUY",
        "quantity": 10,
        "entry_price": 20.0,
        "current_sl": 18.0,
        "initial_sl": 18.0,
        "target_price": 25.0,
    }
    pos_id = trade_db.open_position_db(pos)
    assert pos_id > 0

    open_positions = trade_db.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["symbol"] == "YESBANK"
    assert open_positions[0]["quantity"] == 10
    assert open_positions[0]["entry_price"] == 20.0


def test_open_position_db_duplicate_upsert(temp_trade_db):
    pos1 = {
        "order_id": "ORD1001",
        "symbol": "YESBANK",
        "exchange": "NSE",
        "direction": "BUY",
        "quantity": 10,
        "entry_price": 20.0,
        "current_sl": 18.0,
        "initial_sl": 18.0,
        "target_price": 25.0,
    }
    pos_id_1 = trade_db.open_position_db(pos1)

    # Place a duplicate order for YESBANK
    pos2 = {
        "order_id": "ORD1002",
        "symbol": "YESBANK",
        "exchange": "NSE",
        "direction": "BUY",
        "quantity": 10,
        "entry_price": 22.0,
        "current_sl": 19.0,
        "initial_sl": 18.0,
        "target_price": 27.0,
    }
    pos_id_2 = trade_db.open_position_db(pos2)

    # Should reuse existing position ID and accumulate qty / average entry price
    assert pos_id_1 == pos_id_2

    open_positions = trade_db.get_open_positions()
    assert len(open_positions) == 1
    pos_rec = open_positions[0]
    assert pos_rec["quantity"] == 20
    assert pos_rec["entry_price"] == 21.0  # (10*20 + 10*22) / 20

    events = trade_db.get_position_events(pos_id_1)
    event_types = [e["event_type"] for e in events]
    assert "OPEN" in event_types
    assert "POSITION_ADD" in event_types

