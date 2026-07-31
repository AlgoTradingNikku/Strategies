"""
===============================================================================
  Bot-Options / db / option_trade_db.py
  Database layer for options trades, positions, and operational audit events.
===============================================================================
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import list, dict, Any, Optional

log = logging.getLogger(__name__)

DB_PATH = Path("c:/Rahul/Trade/Strategies/Bot-Options/option_trades.db")

def get_db_connection() -> sqlite3.Connection:
    """Get connection to SQLite options trades database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize option positions and events tables."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Option Positions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS option_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                underlying TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT DEFAULT 'NFO',
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                option_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                lot_size INTEGER NOT NULL,
                num_lots INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                entry_premium REAL NOT NULL,
                entry_time TEXT NOT NULL,
                underlying_price_at_entry REAL,
                current_premium REAL,
                current_sl_premium REAL NOT NULL,
                initial_sl_premium REAL NOT NULL,
                target_premium REAL NOT NULL,
                peak_premium REAL NOT NULL,
                profit_locked INTEGER DEFAULT 0,
                trailing_active INTEGER DEFAULT 0,
                partial_exit_done INTEGER DEFAULT 0,
                expiry_exit_triggered INTEGER DEFAULT 0,
                status TEXT DEFAULT 'OPEN',
                close_reason TEXT,
                close_premium REAL,
                close_time TEXT,
                pnl_premium REAL,
                pnl_pct REAL,
                pnl_amount REAL,
                timeframe TEXT
            )
        """)
        
        # 2. Operational Audit Events Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS option_position_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                note TEXT,
                FOREIGN KEY(position_id) REFERENCES option_positions(id)
            )
        """)
        
        conn.commit()
    except Exception as e:
        log.error("Failed to initialize option_trades database: %s", e)
    finally:
        conn.close()


def open_position_db(pos: dict[str, Any]) -> int:
    """Insert a new open options position into database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO option_positions (
                order_id, underlying, symbol, exchange, expiry, strike, option_type,
                direction, lot_size, num_lots, quantity, entry_premium, entry_time,
                underlying_price_at_entry, current_premium, current_sl_premium,
                initial_sl_premium, target_premium, peak_premium, status, timeframe
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            pos.get("order_id"),
            pos.get("underlying"),
            pos.get("symbol"),
            pos.get("exchange", "NFO"),
            pos.get("expiry"),
            pos.get("strike"),
            pos.get("option_type"),
            pos.get("direction", "BUY"),
            pos.get("lot_size", 75),
            pos.get("num_lots", 1),
            pos.get("quantity"),
            pos.get("entry_premium"),
            pos.get("entry_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            pos.get("underlying_price_at_entry"),
            pos.get("entry_premium"),  # current premium initial = entry premium
            pos.get("current_sl_premium"),
            pos.get("current_sl_premium"),  # initial = current SL initially
            pos.get("target_premium"),
            pos.get("entry_premium"),  # peak premium initially = entry premium
            pos.get("timeframe")
        ))
        conn.commit()
        pos_id = cursor.lastrowid
        
        # Log creation event
        log_event(pos_id, "OPEN", None, pos.get("entry_premium"), "Position opened successfully")
        return pos_id
    except Exception as e:
        log.error("Failed to open position in db: %s", e)
        return -1
    finally:
        conn.close()


def update_position_db(pos_id: int, **fields) -> bool:
    """Update dynamic fields of an active options position in-place."""
    if not fields:
        return False
        
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        set_clauses = []
        params = []
        for col, val in fields.items():
            set_clauses.append(f"{col} = ?")
            params.append(val)
            
        params.append(pos_id)
        query = f"UPDATE option_positions SET {', '.join(set_clauses)} WHERE id = ?"
        
        cursor.execute(query, tuple(params))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log.error("Failed to update position %d in db: %s", pos_id, e)
        return False
    finally:
        conn.close()


def log_event(pos_id: int, event_type: str, old_val: Optional[float], new_val: Optional[float], note: str):
    """Log an operational audit event for a position."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO option_position_events (position_id, timestamp, event_type, old_value, new_value, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            pos_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            old_val,
            new_val,
            note
        ))
        conn.commit()
    except Exception as e:
        log.error("Failed to log event for position %d: %s", pos_id, e)
    finally:
        conn.close()


def get_open_positions() -> list[dict[str, Any]]:
    """Retrieve all open options positions."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM option_positions WHERE status = 'OPEN'")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error("Failed to fetch open positions from db: %s", e)
        return []
    finally:
        conn.close()


def get_closed_positions(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Retrieve paginated list of closed positions."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM option_positions 
            WHERE status = 'CLOSED' 
            ORDER BY close_time DESC, id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error("Failed to fetch closed positions from db: %s", e)
        return []
    finally:
        conn.close()


def get_position_events(pos_id: int) -> list[dict[str, Any]]:
    """Retrieve full audit log for a specific position."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM option_position_events 
            WHERE position_id = ? 
            ORDER BY timestamp ASC, id ASC
        """, (pos_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error("Failed to fetch events for position %d from db: %s", pos_id, e)
        return []
    finally:
        conn.close()

# Auto-initialize database on import
init_db()
