"""
trade_db.py
===========
SQLite database module for active and historical option positions / orders.
Database: trades.db
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

log = logging.getLogger("UTBotSRChannelsScanner")

DB_PATH = Path(__file__).resolve().parent / "trades.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                symbol TEXT NOT NULL,
                exchange TEXT DEFAULT 'NFO',
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL DEFAULT 0.0,
                stop_loss REAL,
                target REAL,
                trailing_sl REAL,
                status TEXT DEFAULT 'OPEN',
                product TEXT DEFAULT 'NRML',
                pnl_pts REAL DEFAULT 0.0,
                pnl_amount REAL DEFAULT 0.0,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                exit_reason TEXT
            )
        """)
        conn.commit()


init_db()


def add_trade(trade_data: Dict[str, Any]) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                order_id, symbol, exchange, action, quantity, entry_price,
                current_price, stop_loss, target, trailing_sl, status, product,
                opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data.get("order_id", f"ORD_{int(datetime.now().timestamp()*1000)}"),
            trade_data.get("symbol"),
            trade_data.get("exchange", "NFO"),
            trade_data.get("action", "BUY"),
            trade_data.get("quantity", 65),
            trade_data.get("entry_price", 0.0),
            trade_data.get("entry_price", 0.0),
            trade_data.get("stop_loss"),
            trade_data.get("target"),
            trade_data.get("stop_loss"),
            "OPEN",
            trade_data.get("product", "NRML"),
            datetime.now().isoformat(),
        ))
        conn.commit()
        return cursor.lastrowid


def update_trade_price(trade_id: int, current_price: float, trailing_sl: Optional[float] = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if trailing_sl is not None:
            cursor.execute("""
                UPDATE trades SET current_price = ?, trailing_sl = ? WHERE trade_id = ?
            """, (current_price, trailing_sl, trade_id))
        else:
            cursor.execute("""
                UPDATE trades SET current_price = ? WHERE trade_id = ?
            """, (current_price, trade_id))
        conn.commit()


def close_trade(trade_id: int, exit_price: float, exit_reason: str = "MANUAL"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT entry_price, quantity, action FROM trades WHERE trade_id = ?", (trade_id,))
        row = cursor.fetchone()
        if not row:
            return

        entry_price = row["entry_price"]
        qty = row["quantity"]
        action = row["action"]

        if action == "BUY":
            pnl_pts = exit_price - entry_price
        else:
            pnl_pts = entry_price - exit_price

        pnl_amount = pnl_pts * qty

        cursor.execute("""
            UPDATE trades SET
                status = 'CLOSED',
                exit_price = ?,
                current_price = ?,
                pnl_pts = ?,
                pnl_amount = ?,
                exit_reason = ?,
                closed_at = ?
            WHERE trade_id = ?
        """, (exit_price, exit_price, pnl_pts, pnl_amount, exit_reason, datetime.now().isoformat(), trade_id))
        conn.commit()


def get_active_trades() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY trade_id DESC")
        return [dict(r) for r in cursor.fetchall()]


def get_all_trades(limit: int = 100) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY trade_id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]
