"""
trade_db.py
===========
SQLite persistence layer for trade management positions and events.

Schema version history
-----------------------
v1 (original) : positions table with basic columns
v2 (this rev) : added `product`, `profit_lock_tier`, `partial_exit_tier`
                columns via safe ALTER TABLE migration so existing trades.db
                files are not broken.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("UTBotSRChannelsScanner")

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
            timeframe           TEXT
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

    # ---- Indexes (IF NOT EXISTS keeps them safe on existing DBs) ----
    # Speeds up:
    #   • get_open_positions()     — WHERE status = 'OPEN'
    #   • get_closed_positions()   — WHERE status != 'OPEN' ORDER BY close_time DESC
    #   • get_position_events()    — WHERE position_id = ? ORDER BY event_time
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_close_time ON positions(close_time DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_position ON position_events(position_id, event_time)")
    conn.commit()


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
                status, timeframe
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
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


def get_realized_pnl_pct_since(iso_start: str) -> float:
    """Return the SUM of ``pnl_pct`` for positions closed on/after ``iso_start``.

    Used by the risk-limits gate to enforce a daily-loss cutoff. When no
    matching rows exist, returns 0.0.  ``iso_start`` should be an
    'YYYY-MM-DD HH:MM:SS' string in the same local timezone the writer uses
    (execution paths write ``close_time = datetime.now().strftime(...)`` so
    this is always local naive time).
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_pct), 0.0) AS total "
            "FROM positions "
            "WHERE status = 'CLOSED' AND close_time >= ?",
            (iso_start,),
        ).fetchone()
        return float(row["total"] if row and row["total"] is not None else 0.0)
    finally:
        conn.close()


def get_realized_pnl_rupees_since(iso_start: str) -> float:
    """Return the SUM of realised ₹-PnL for positions closed on/after ``iso_start``.

    Sprint 2 addition: complements ``get_realized_pnl_pct_since`` for the
    absolute-rupee daily-loss cutoff. Because ``pnl_amount`` is not stored
    on the positions table today, we reconstruct it from
    ``(close_price - entry_price) × qty × direction_sign`` for CLOSED rows.

    When no matching rows exist, returns 0.0.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT direction, quantity, entry_price, close_price "
            "FROM positions "
            "WHERE status = 'CLOSED' AND close_time >= ? "
            "  AND close_price IS NOT NULL AND entry_price IS NOT NULL",
            (iso_start,),
        ).fetchall()
    finally:
        conn.close()

    total = 0.0
    for r in rows:
        try:
            direction = str(r["direction"]).upper()
            qty = float(r["quantity"] or 0)
            entry = float(r["entry_price"] or 0.0)
            close = float(r["close_price"] or 0.0)
            sign = 1.0 if direction == "BUY" else -1.0
            total += (close - entry) * qty * sign
        except (TypeError, ValueError, KeyError):
            continue
    return total




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


def clear_all_trades() -> bool:
    """Clear all records from positions and position_events tables to start fresh."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM position_events")
        conn.execute("DELETE FROM positions")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('positions', 'position_events')")
        except Exception:
            pass
        conn.commit()
        log.info("Cleared all trades and position events from database.")
        return True
    except Exception as exc:
        conn.rollback()
        log.error("DB error clearing trades: %s", exc)
        return False
    finally:
        conn.close()
