"""
Trade Persistence - SQLite-based crash recovery.

Saves trade state to disk so the bot can recover after crashes or restarts.
Critical for live trading - you don't want to lose track of open positions!
"""

import sqlite3
import json
from datetime import datetime
from typing import Optional, List
from pathlib import Path
from .state_machine import Trade, TradeState


class TradePersistence:
    """
    SQLite-based crash recovery for trading state.
    
    Every time a trade changes state (signal detected, position entered, exit triggered),
    this class saves it to disk. On restart, trades are restored to exact state.
    
    Example:
        # Save trade
        persistence = TradePersistence()
        persistence.save_trade(trade)
        
        # After crash/restart
        active_trades = persistence.load_active_trades()
        # Bot can continue from exactly where it left off
    """
    
    def __init__(self, db_path: str = "bot_state.db"):
        """
        Initialize persistence layer.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self._create_tables()
    
    def _create_tables(self):
        """Create database schema if it doesn't exist"""
        # Active trades table (current state)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                symbol TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                side TEXT,
                entry_price REAL,
                current_price REAL,
                highest_price REAL,
                lowest_price REAL,
                quantity INTEGER,
                entry_time TEXT,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                pnl_pct REAL,
                atr REAL,
                tsl_level REAL,
                last_stage TEXT,
                obs_candles INTEGER DEFAULT 0,
                obs_start_time TEXT,
                idx_at_resolution REAL,
                expiry_params TEXT,
                metadata TEXT,
                trend_reversed INTEGER DEFAULT 0,
                manual_exit_pending INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Trade history table (completed trades)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity INTEGER,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                pnl REAL,
                pnl_pct REAL,
                atr REAL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        # Create indices for performance
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_state ON trades(state)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_exit_time ON trade_history(exit_time)
        """)
        
        self.conn.commit()
    
    def save_trade(self, trade: Trade):
        """
        Persist trade state to disk.
        
        Called every time trade state changes (OBSERVING → ENTERING → POSITION → EXITED).
        Overwrites existing entry for same symbol.
        
        Args:
            trade: Trade to save
        """
        trade_dict = trade.to_dict()
        
        self.conn.execute("""
            INSERT OR REPLACE INTO trades VALUES 
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_dict["symbol"],
            trade_dict["state"],
            trade_dict["side"],
            trade_dict["entry_price"],
            trade_dict["current_price"],
            trade_dict["highest_price"],
            trade_dict["lowest_price"],
            trade_dict["quantity"],
            trade_dict["entry_time"],
            trade_dict["exit_time"],
            trade_dict["exit_price"],
            trade_dict["exit_reason"],
            trade_dict["pnl"],
            trade_dict["pnl_pct"],
            trade_dict["atr"],
            trade_dict["tsl_level"],
            trade_dict["last_stage"],
            trade_dict["obs_candles"],
            trade_dict["obs_start_time"],
            trade_dict["idx_at_resolution"],
            json.dumps(trade_dict["expiry_params"]) if trade_dict["expiry_params"] else None,
            json.dumps(trade_dict["metadata"]) if trade_dict["metadata"] else None,
            1 if trade_dict["trend_reversed"] else 0,
            1 if trade_dict["manual_exit_pending"] else 0,
            datetime.now().isoformat()
        ))
        self.conn.commit()
    
    def load_active_trades(self) -> List[Trade]:
        """
        Recover all non-exited trades on startup.
        
        Returns:
            List of Trade objects in OBSERVING, ENTERING, or POSITION states
            
        Example:
            >>> persistence = TradePersistence()
            >>> trades = persistence.load_active_trades()
            >>> for trade in trades:
            ...     print(f"Recovered {trade.symbol} at {trade.entry_price}")
        """
        cursor = self.conn.execute("""
            SELECT * FROM trades 
            WHERE state NOT IN ('EXITED', 'IDLE', 'BLOCKED')
            ORDER BY entry_time
        """)
        
        trades = []
        for row in cursor.fetchall():
            trade_dict = {
                "symbol": row[0],
                "state": row[1],
                "side": row[2],
                "entry_price": row[3],
                "current_price": row[4],
                "highest_price": row[5],
                "lowest_price": row[6],
                "quantity": row[7],
                "entry_time": row[8],
                "exit_time": row[9],
                "exit_price": row[10],
                "exit_reason": row[11],
                "pnl": row[12],
                "pnl_pct": row[13],
                "atr": row[14],
                "tsl_level": row[15],
                "last_stage": row[16],
                "obs_candles": row[17],
                "obs_start_time": row[18],
                "idx_at_resolution": row[19],
                "expiry_params": json.loads(row[20]) if row[20] else {},
                "metadata": json.loads(row[21]) if row[21] else {},
                "trend_reversed": bool(row[22]),
                "manual_exit_pending": bool(row[23]),
            }
            
            trades.append(Trade.from_dict(trade_dict))
        
        return trades
    
    def archive_trade(self, trade: Trade):
        """
        Move exited trade to history table and remove from active trades.
        
        Args:
            trade: Completed trade to archive
        """
        trade_dict = trade.to_dict()
        
        # Insert into history
        self.conn.execute("""
            INSERT INTO trade_history 
            (symbol, side, entry_price, exit_price, quantity, entry_time, exit_time, 
             exit_reason, pnl, pnl_pct, atr, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_dict["symbol"],
            trade_dict["side"],
            trade_dict["entry_price"],
            trade_dict["exit_price"],
            trade_dict["quantity"],
            trade_dict["entry_time"],
            trade_dict["exit_time"],
            trade_dict["exit_reason"],
            trade_dict["pnl"],
            trade_dict["pnl_pct"],
            trade_dict["atr"],
            json.dumps(trade_dict["metadata"]) if trade_dict["metadata"] else None,
            datetime.now().isoformat()
        ))
        
        # Remove from active trades
        self.conn.execute("DELETE FROM trades WHERE symbol = ?", (trade.symbol,))
        self.conn.commit()
    
    def delete_trade(self, symbol: str):
        """
        Manually delete a trade (for cleanup/debugging).
        
        Args:
            symbol: Symbol to remove
        """
        self.conn.execute("DELETE FROM trades WHERE symbol = ?", (symbol,))
        self.conn.commit()
    
    def get_trade(self, symbol: str) -> Optional[Trade]:
        """
        Get specific trade by symbol.
        
        Args:
            symbol: Symbol to look up
            
        Returns:
            Trade if found, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE symbol = ?", (symbol,)
        )
        row = cursor.fetchone()
        
        if not row:
            return None
        
        trade_dict = {
            "symbol": row[0],
            "state": row[1],
            "side": row[2],
            "entry_price": row[3],
            "current_price": row[4],
            "highest_price": row[5],
            "lowest_price": row[6],
            "quantity": row[7],
            "entry_time": row[8],
            "exit_time": row[9],
            "exit_price": row[10],
            "exit_reason": row[11],
            "pnl": row[12],
            "pnl_pct": row[13],
            "atr": row[14],
            "tsl_level": row[15],
            "last_stage": row[16],
            "obs_candles": row[17],
            "obs_start_time": row[18],
            "idx_at_resolution": row[19],
            "expiry_params": json.loads(row[20]) if row[20] else {},
            "metadata": json.loads(row[21]) if row[21] else {},
            "trend_reversed": bool(row[22]),
            "manual_exit_pending": bool(row[23]),
        }
        
        return Trade.from_dict(trade_dict)
    
    def get_history(self, days: int = 7) -> List[dict]:
        """
        Get trade history for last N days.
        
        Args:
            days: Number of days to look back
            
        Returns:
            List of completed trades
        """
        cutoff = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff = cutoff.replace(day=cutoff.day - days).isoformat()
        
        cursor = self.conn.execute("""
            SELECT * FROM trade_history 
            WHERE exit_time >= ?
            ORDER BY exit_time DESC
        """, (cutoff,))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                "id": row[0],
                "symbol": row[1],
                "side": row[2],
                "entry_price": row[3],
                "exit_price": row[4],
                "quantity": row[5],
                "entry_time": row[6],
                "exit_time": row[7],
                "exit_reason": row[8],
                "pnl": row[9],
                "pnl_pct": row[10],
                "atr": row[11],
                "metadata": json.loads(row[12]) if row[12] else {},
            })
        
        return history
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
