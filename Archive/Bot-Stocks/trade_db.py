import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("UTBotSRChannelsScanner")

_DB_PATH = Path(__file__).resolve().parent / "trades.db"
_db_initialized = False

def _get_connection() -> sqlite3.Connection:
    global _db_initialized
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if not _db_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                current_sl REAL NOT NULL,
                initial_sl REAL NOT NULL,
                target_price REAL NOT NULL,
                high_water_mark REAL NOT NULL,
                profit_locked INTEGER DEFAULT 0,
                trailing_active INTEGER DEFAULT 0,
                partial_exit_done INTEGER DEFAULT 0,
                status TEXT DEFAULT 'OPEN',
                close_reason TEXT,
                close_price REAL,
                close_time TEXT,
                pnl_pct REAL,
                timeframe TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS position_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                note TEXT,
                FOREIGN KEY(position_id) REFERENCES positions(id)
            )
        """)
        conn.commit()
        _db_initialized = True
    return conn

def open_position_db(pos: dict) -> int:
    conn = _get_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO positions (
                order_id, symbol, exchange, direction, quantity, entry_price, entry_time,
                current_sl, initial_sl, target_price, high_water_mark, status, timeframe
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            pos.get("order_id"), pos["symbol"], pos["exchange"], pos["direction"],
            pos["quantity"], pos["entry_price"], now, pos["current_sl"],
            pos["initial_sl"], pos["target_price"], pos["entry_price"], pos.get("timeframe")
        ))
        pos_id = cur.lastrowid
        conn.commit()
        log_event(pos_id, "OPEN", None, pos["entry_price"], f"Position opened at {pos['entry_price']}")
        return pos_id
    except Exception as e:
        conn.rollback()
        log.error("DB error opening position: %s", e)
        raise e
    finally:
        conn.close()

def update_position(pos_id: int, **fields) -> bool:
    conn = _get_connection()
    try:
        keys = fields.keys()
        set_str = ", ".join(f"{k} = ?" for k in keys)
        values = list(fields.values())
        values.append(pos_id)
        conn.execute(f"UPDATE positions SET {set_str} WHERE id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        log.error("DB error updating position %d: %s", pos_id, e)
        return False
    finally:
        conn.close()

def log_event(pos_id: int, event_type: str, old_val: float, new_val: float, note: str):
    conn = _get_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute("""
            INSERT INTO position_events (position_id, event_time, event_type, old_value, new_value, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pos_id, now, event_type, old_val, new_val, note))
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("DB error logging event: %s", e)
    finally:
        conn.close()

def get_open_positions() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN'").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_closed_positions(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status != 'OPEN' ORDER BY close_time DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_position_events(pos_id: int) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM position_events WHERE position_id = ? ORDER BY event_time ASC",
            (pos_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
