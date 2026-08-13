"""
trade_db.py
===========
SQLite persistence layer for trade management positions and events.

This is BOT-Options' own trades.db — separate from signals.db (which stores
raw UT Bot signals for the ML pipeline) and separate from Bot-Stocks' own
trades.db. Nothing here is shared across bots.

Schema version history
-----------------------
v1 (Bot-Stocks original) : positions table with basic columns
v2 (Bot-Stocks)          : added `product`, `profit_lock_tier`, `partial_exit_tier`
v3 (BOT-Options)         : added options metadata (`underlying`, `strike`,
                           `option_type`, `expiry_date`, `lot_size`) and
                           `pnl_amount` (rupee P&L, alongside the existing
                           `pnl_pct`) — all via safe ALTER TABLE migrations,
                           so an existing trades.db is never broken.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("UTBot.TradeManagement")

_DB_PATH = Path(__file__).resolve().parent / "trades.db"
_db_initialized = False


# ---------------------------------------------------------------------------
# Connection + Schema
# ---------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    global _db_initialized
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    if not _db_initialized:
        _init_schema(conn)
        _db_initialized = True

    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and apply safe column migrations."""

    # ---- positions table ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id            TEXT,
            symbol              TEXT    NOT NULL,
            exchange            TEXT    NOT NULL,
            direction           TEXT    NOT NULL,
            quantity            INTEGER NOT NULL,
            entry_price         REAL    NOT NULL,
            entry_time          TEXT    NOT NULL,
            current_sl          REAL    NOT NULL,
            initial_sl          REAL    NOT NULL,
            target_price        REAL    NOT NULL,
            high_water_mark     REAL    NOT NULL,
            profit_locked       INTEGER DEFAULT 0,
            profit_lock_tier    INTEGER DEFAULT 0,
            trailing_active     INTEGER DEFAULT 0,
            partial_exit_tier   INTEGER DEFAULT 0,
            product             TEXT    DEFAULT 'MIS',
            status              TEXT    DEFAULT 'OPEN',
            close_reason        TEXT,
            close_price         REAL,
            close_time          TEXT,
            pnl_pct             REAL,
            pnl_amount          REAL,
            realized_pnl_amount REAL    DEFAULT 0,
            timeframe           TEXT,
            underlying          TEXT,
            strike              REAL,
            option_type         TEXT,
            expiry_date         TEXT,
            lot_size            INTEGER
        )
    """)

    # ---- position_events table ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id  INTEGER NOT NULL,
            event_time   TEXT    NOT NULL,
            event_type   TEXT    NOT NULL,
            old_value    REAL,
            new_value    REAL,
            note         TEXT,
            FOREIGN KEY(position_id) REFERENCES positions(id)
        )
    """)

    conn.commit()

    # ---- Safe migrations for databases created before v2 ----
    _add_column_if_missing(conn, "positions", "product",           "TEXT DEFAULT 'MIS'")
    _add_column_if_missing(conn, "positions", "profit_lock_tier",  "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "positions", "partial_exit_tier", "INTEGER DEFAULT 0")

    # ---- Safe migrations for databases created before v3 (options metadata) ----
    _add_column_if_missing(conn, "positions", "pnl_amount",          "REAL")
    _add_column_if_missing(conn, "positions", "realized_pnl_amount", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "positions", "underlying",          "TEXT")
    _add_column_if_missing(conn, "positions", "strike",              "REAL")
    _add_column_if_missing(conn, "positions", "option_type",         "TEXT")
    _add_column_if_missing(conn, "positions", "expiry_date",         "TEXT")
    _add_column_if_missing(conn, "positions", "lot_size",            "INTEGER")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """ALTER TABLE to add a column only if it doesn't already exist."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
        log.info("DB migration: added column '%s' to table '%s'.", column, table)


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def open_position_db(pos: dict) -> int:
    """Insert a new position record and return its auto-generated ID."""
    conn = _get_connection()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO positions (
                order_id, symbol, exchange, direction, quantity,
                entry_price, entry_time,
                current_sl, initial_sl, target_price, high_water_mark,
                profit_lock_tier, partial_exit_tier, product,
                status, timeframe,
                underlying, strike, option_type, expiry_date, lot_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
        """, (
            pos.get("order_id"),
            pos["symbol"],
            pos["exchange"],
            pos["direction"],
            pos["quantity"],
            pos["entry_price"],
            now,
            pos["current_sl"],
            pos["initial_sl"],
            pos["target_price"],
            pos["entry_price"],     # high_water_mark starts at entry
            pos.get("profit_lock_tier", 0),
            pos.get("partial_exit_tier", 0),
            pos.get("product", "MIS"),
            pos.get("timeframe"),
            pos.get("underlying"),
            pos.get("strike"),
            pos.get("option_type"),
            pos.get("expiry_date"),
            pos.get("lot_size"),
        ))
        pos_id = cur.lastrowid
        conn.commit()
        log_event(pos_id, "OPEN", None, pos["entry_price"],
                  f"Position opened @ ₹{pos['entry_price']:.2f}")
        return pos_id
    except Exception as exc:
        conn.rollback()
        log.error("DB error opening position: %s", exc)
        raise
    finally:
        conn.close()


def update_position(pos_id: int, **fields) -> bool:
    """Update arbitrary columns on a position row by ID."""
    if not fields:
        return True
    conn = _get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values     = list(fields.values()) + [pos_id]
        conn.execute(f"UPDATE positions SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        log.error("DB error updating position %d: %s", pos_id, exc)
        return False
    finally:
        conn.close()


def log_event(pos_id: int, event_type: str, old_val, new_val, note: str) -> None:
    """Append an audit event for a position."""
    conn = _get_connection()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute("""
            INSERT INTO position_events
                (position_id, event_time, event_type, old_value, new_value, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pos_id, now, event_type, old_val, new_val, note))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        log.error("DB error logging event for position %d: %s", pos_id, exc)
    finally:
        conn.close()


def get_open_positions() -> list[dict]:
    """Return all positions with status = 'OPEN'."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY entry_time DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_closed_positions(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return closed positions, newest first, with pagination."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status != 'OPEN' "
            "ORDER BY close_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_position_events(pos_id: int) -> list[dict]:
    """Return the full audit event log for a specific position."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM position_events WHERE position_id = ? ORDER BY event_time ASC",
            (pos_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
