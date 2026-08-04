"""
===============================================================================
  Bot-Options / db / option_signal_db.py
  Database layer for saving, querying, and updating options signals
  and calculating historical statistics.
===============================================================================
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "option_signals.db"

def get_db_connection() -> sqlite3.Connection:
    """Get connection to SQLite options signal database."""
    # Ensure parent dir exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize option signals table if it does not exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS option_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                underlying TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                option_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                entry_premium REAL NOT NULL,
                current_premium REAL,
                confidence_score REAL,
                score_reasons TEXT DEFAULT '[]',
                filter_status TEXT DEFAULT '{}',
                iv_proxy REAL,
                oi_at_signal INTEGER,
                underlying_price REAL,
                timeframe TEXT,
                status TEXT DEFAULT 'SIGNAL',
                outcome_pnl_pct REAL,
                outcome_checked INTEGER DEFAULT 0
            )
        """)
        # Performance indexes — safe to run on existing DB (IF NOT EXISTS guard)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp  ON option_signals(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status     ON option_signals(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol     ON option_signals(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_underlying ON option_signals(underlying)")
        conn.commit()
    except Exception as e:
        log.error("Failed to initialize option_signals database: %s", e)
    finally:
        conn.close()


def save_option_signal(sig: Dict[str, Any]) -> int:
    """Save an options signal to the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Convert objects to string representations if needed
        reasons_json = json.dumps(sig.get("score_reasons", []))
        filter_json = json.dumps(sig.get("filter_status", {}))
        
        cursor.execute("""
            INSERT INTO option_signals (
                timestamp, underlying, symbol, exchange, expiry, strike, option_type,
                direction, strategy_name, entry_premium, current_premium, confidence_score,
                score_reasons, filter_status, iv_proxy, oi_at_signal, underlying_price,
                timeframe, status, outcome_pnl_pct, outcome_checked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sig.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            sig.get("underlying"),
            sig.get("symbol"),
            sig.get("exchange", "NFO"),
            sig.get("expiry"),
            sig.get("strike"),
            sig.get("option_type"),
            sig.get("direction"),
            sig.get("strategy_name", "UTBot+SR"),
            sig.get("entry_premium"),
            sig.get("current_premium", sig.get("entry_premium")),
            sig.get("confidence_score"),
            reasons_json,
            filter_json,
            sig.get("iv_proxy"),
            sig.get("oi_at_signal"),
            sig.get("underlying_price"),
            sig.get("timeframe"),
            sig.get("status", "SIGNAL"),
            sig.get("outcome_pnl_pct", 0.0),
            sig.get("outcome_checked", 0)
        ))
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        log.error("Failed to save options signal to db: %s", e)
        return -1
    finally:
        conn.close()


def get_option_signals(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Retrieve paginated signals from the database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM option_signals 
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        
        rows = cursor.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["score_reasons"] = json.loads(d["score_reasons"])
            except Exception:
                d["score_reasons"] = []
                
            try:
                d["filter_status"] = json.loads(d["filter_status"])
            except Exception:
                d["filter_status"] = {}
                
            results.append(d)
        return results
    except Exception as e:
        log.error("Failed to fetch option signals from db: %s", e)
        return []
    finally:
        conn.close()


def update_signal_status(signal_id: int, status: str) -> bool:
    """Update status of a logged signal (e.g. to EXECUTED)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE option_signals 
            SET status = ? 
            WHERE id = ?
        """, (status, signal_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log.error("Failed to update option signal status in db: %s", e)
        return False
    finally:
        conn.close()


def get_option_statistics(days: int = 30) -> dict[str, Any]:
    """Retrieve statistical win/loss performance data for option signals."""
    conn = get_db_connection()
    stats = {
        "total_signals": 0,
        "executed_signals": 0,
        "winning_signals": 0,
        "losing_signals": 0,
        "win_rate": 0.0,
        "avg_pnl_pct": 0.0
    }
    try:
        cursor = conn.cursor()
        # Query total, executed, PnL
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='EXECUTED' THEN 1 ELSE 0 END) as executed,
                   SUM(CASE WHEN status='EXECUTED' AND outcome_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status='EXECUTED' AND outcome_pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                   AVG(CASE WHEN status='EXECUTED' THEN outcome_pnl_pct ELSE NULL END) as avg_pnl
            FROM option_signals
            WHERE timestamp >= datetime('now', '-' || ? || ' days')
        """, (days,))
        
        row = cursor.fetchone()
        if row and row["total"] > 0:
            stats["total_signals"] = row["total"]
            stats["executed_signals"] = row["executed"] or 0
            stats["winning_signals"] = row["wins"] or 0
            stats["losing_signals"] = row["losses"] or 0
            
            total_executed = stats["winning_signals"] + stats["losing_signals"]
            if total_executed > 0:
                stats["win_rate"] = round((stats["winning_signals"] / total_executed) * 100, 1)
            
            stats["avg_pnl_pct"] = round(row["avg_pnl"] or 0.0, 2)
            
        return stats
    except Exception as e:
        log.error("Failed to calculate option statistics: %s", e)
        return stats
    finally:
        conn.close()

# Auto-initialize database on import
init_db()
