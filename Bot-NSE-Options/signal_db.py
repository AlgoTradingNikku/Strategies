"""
signal_db.py
============
SQLite database module for storing option scan signals and performance metrics.
Database: signals.db
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

log = logging.getLogger("UTBotSRChannelsScanner")

DB_PATH = Path(__file__).resolve().parent / "signals.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                underlying TEXT,
                option_type TEXT,
                strike REAL,
                expiry TEXT,
                signal_type TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                price REAL NOT NULL,
                stop_loss REAL,
                target REAL,
                risk_reward TEXT,
                ut_trail REAL,
                sr_near_support INTEGER,
                sr_near_resistance INTEGER,
                outcome TEXT DEFAULT 'PENDING',
                pnl_pct REAL DEFAULT 0.0,
                closed_at TEXT
            )
        """)
        conn.commit()


# Initialize table on module import
init_db()


def log_signal(sig_data: Dict[str, Any]) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO signals (
                timestamp, symbol, underlying, option_type, strike, expiry,
                signal_type, timeframe, price, stop_loss, target, risk_reward,
                ut_trail, sr_near_support, sr_near_resistance, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sig_data.get("timestamp", datetime.now().isoformat()),
            sig_data.get("symbol"),
            sig_data.get("underlying"),
            sig_data.get("option_type"),
            sig_data.get("strike"),
            sig_data.get("expiry"),
            sig_data.get("signal_type"),
            sig_data.get("timeframe"),
            sig_data.get("price"),
            sig_data.get("stop_loss"),
            sig_data.get("target"),
            sig_data.get("risk_reward"),
            sig_data.get("ut_trail"),
            1 if sig_data.get("sr_near_support") else 0,
            1 if sig_data.get("sr_near_resistance") else 0,
            "PENDING",
        ))
        conn.commit()
        return cursor.lastrowid


def log_signals_batch(signals_list: List[Dict[str, Any]]) -> int:
    count = 0
    for s in signals_list:
        try:
            log_signal(s)
            count += 1
        except Exception as e:
            log.error("Failed to log signal %s: %s", s.get("symbol"), e)
    return count


def get_signal_history(limit: int = 100, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        if symbol:
            cursor.execute(
                "SELECT * FROM signals WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            cursor.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_statistics() -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM signals")
        total = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) as buy_count FROM signals WHERE signal_type IN ('BUY', 'CE')")
        buy_cnt = cursor.fetchone()["buy_count"]

        cursor.execute("SELECT COUNT(*) as sell_count FROM signals WHERE signal_type IN ('SELL', 'PE')")
        sell_cnt = cursor.fetchone()["sell_count"]

        return {
            "total_signals": total,
            "buy_signals": buy_cnt,
            "sell_signals": sell_cnt,
            "win_rate_pct": 0.0,
            "total_pnl_pct": 0.0,
        }


def check_outcomes(openalgo_client: Any = None):
    """Stub outcome tracking for signals."""
    pass
