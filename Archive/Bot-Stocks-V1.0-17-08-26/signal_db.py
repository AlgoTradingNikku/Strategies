"""
Signal History Database — SQLite-backed signal logging and outcome tracking.

Stores every generated signal with its metadata, then periodically checks
whether the target or stop-loss was hit by re-fetching the current price.

Database: signals.db (auto-created in the bot directory)
"""

import sqlite3
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("UTBotSRChannelsScanner")

_DB_PATH         = Path(__file__).resolve().parent / "signals.db"
_db_initialized  = False   # DDL runs only once per process


# ============================================================================
# DATABASE SETUP
# ============================================================================

def _get_tz(config: dict) -> object:
    """Return the ZoneInfo timezone for the configured exchange."""
    exchange = (config or {}).get("exchange", "NSE")
    return ZoneInfo("Asia/Kolkata") if exchange.upper() in ("NSE", "BSE") else ZoneInfo("UTC")


def _get_connection(config: dict = None) -> sqlite3.Connection:
    """Get a connection to the SQLite database, creating tables if needed (once per process)."""
    global _db_initialized
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if not _db_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp          TEXT NOT NULL,
                symbol             TEXT NOT NULL,
                signal_type        TEXT NOT NULL,
                close_price        REAL NOT NULL,
                setup_score        REAL DEFAULT 0.0,
                score_reasons      TEXT DEFAULT '[]',
                stop_loss          REAL,
                target             REAL,
                risk_reward        REAL,
                triggered_conditions TEXT DEFAULT '[]',
                timeframe          TEXT,
                adx                REAL,
                rs_ratio           REAL,
                mtf_trend          TEXT,
                outcome_checked    INTEGER DEFAULT 0,
                outcome_pnl_pct    REAL,
                outcome_hit_target INTEGER DEFAULT 0,
                outcome_hit_stop   INTEGER DEFAULT 0,
                outcome_price      REAL,
                outcome_time       TEXT
            )
        """)
        conn.commit()
        _db_initialized = True
    return conn


# ============================================================================
# SIGNAL LOGGING
# ============================================================================


def log_signals_batch(signals_list: list[dict], timeframe: str = None, config: dict = None) -> list[int]:  # noqa: E501
    """
    Insert a list of signals into the database in a single transaction.

    Parameters
    ----------
    signals_list : list of dicts from scanner.py's scan_symbol result
    timeframe    : scan timeframe string (e.g. "5m", "1h")
    config       : optional configuration dict

    Returns
    -------
    list[int] : list of row IDs of the inserted signals
    """
    if not signals_list:
        return []

    if config is None:
        try:
            from scanner import load_config
            config = load_config()
        except Exception:
            config = {}

    tz  = _get_tz(config)
    now = datetime.now(tz).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_connection(config)
    inserted_ids = []
    try:
        cursor = conn.cursor()
        for signal_dict in signals_list:
            mtf = signal_dict.get("mtf")
            mtf_trend = mtf.get("trend") if isinstance(mtf, dict) else None

            cursor.execute("""
                INSERT INTO signals (
                    timestamp, symbol, signal_type, close_price, setup_score,
                    score_reasons, stop_loss, target, risk_reward,
                    triggered_conditions, timeframe, adx, rs_ratio, mtf_trend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now,
                signal_dict.get("symbol", ""),
                signal_dict.get("signal", ""),
                signal_dict.get("close", 0.0),
                signal_dict.get("setup_score", 0.0),
                json.dumps(signal_dict.get("score_reasons", [])),
                signal_dict.get("stop_loss"),
                signal_dict.get("target"),
                signal_dict.get("risk_reward"),
                json.dumps(signal_dict.get("triggered", [])),
                timeframe,
                signal_dict.get("adx"),
                signal_dict.get("rs_ratio"),
                mtf_trend,
            ))
            inserted_ids.append(cursor.lastrowid)
        conn.commit()
        log.debug("Logged %d signals to DB in batch", len(signals_list))
        return inserted_ids
    except Exception as e:
        conn.rollback()
        log.error("Batch signal logging failed: %s", e)
        raise e
    finally:
        conn.close()


# ============================================================================
# OUTCOME CHECKING
# ============================================================================

def check_outcomes(hours: int = 4, config: dict = None, fetch_fn=None) -> int:
    """
    Check outcomes for signals older than `hours` that haven't been checked yet.

    For each unchecked signal, fetches the current price and determines if
    the target or stop-loss was hit.

    Parameters
    ----------
    fetch_fn : callable, optional
        A function with signature fetch_fn(symbol, timeframe, config) -> DataFrame.
        When None, outcome checking is skipped (returns 0). Pass
        scanner.fetch_history from the call site to avoid a circular import.

    Returns the number of signals updated.
    """
    if fetch_fn is None:
        log.debug("check_outcomes: no fetch_fn provided, skipping outcome check.")
        return 0

    tz   = _get_tz(config)
    conn = _get_connection(config)
    try:
        cutoff = (datetime.now(tz).replace(tzinfo=None) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

        unchecked = conn.execute("""
            SELECT id, symbol, signal_type, close_price, stop_loss, target, timeframe
            FROM signals
            WHERE outcome_checked = 0 AND timestamp <= ?
        """, (cutoff,)).fetchall()

        if not unchecked:
            return 0

        updated = 0
        for row in unchecked:
            sig_id = row["id"]
            symbol = row["symbol"]
            sig_type = row["signal_type"]
            entry_price = row["close_price"]
            stop_loss = row["stop_loss"]
            target = row["target"]
            tf = row["timeframe"] or "5m"

            try:
                df = fetch_fn(symbol, tf, config or {})
                if df is None or len(df) == 0:
                    continue

                # Current close for unrealised P&L display
                current_price = float(df["close"].iloc[-1])
                now = datetime.now(tz).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

                # Filter to candles that closed AFTER the signal was generated.
                # This lets us check whether the target or stop was touched on
                # any bar's high/low intrabar — not just the latest close price.
                sig_time = pd.to_datetime(row["timestamp"])
                post_df = df[df.index > sig_time]

                if post_df.empty:
                    # No new candles since the signal — skip until data arrives
                    continue

                post_high = float(post_df["high"].max())
                post_low  = float(post_df["low"].min())

                # Determine outcome using OHLC extremes on post-signal candles
                hit_target = 0
                hit_stop   = 0
                if sig_type == "BUY":
                    if target is not None and post_high >= target:
                        hit_target = 1
                    if stop_loss is not None and post_low <= stop_loss:
                        hit_stop = 1
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                else:  # SELL
                    if target is not None and post_low <= target:
                        hit_target = 1
                    if stop_loss is not None and post_high >= stop_loss:
                        hit_stop = 1
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100

                conn.execute("""
                    UPDATE signals
                    SET outcome_checked = 1, outcome_pnl_pct = ?, outcome_hit_target = ?,
                        outcome_hit_stop = ?, outcome_price = ?, outcome_time = ?
                    WHERE id = ?
                """, (round(pnl_pct, 2), hit_target, hit_stop, current_price, now, sig_id))
                updated += 1

            except Exception as e:
                log.debug("Outcome check failed for signal %d (%s): %s", sig_id, symbol, e)

        conn.commit()
        if updated > 0:
            log.info("📊 Checked outcomes for %d signal(s).", updated)
        return updated

    finally:
        conn.close()


# ============================================================================
# STATISTICS
# ============================================================================

def get_statistics(days: int = 30, config: dict = None) -> dict:
    """
    Compute signal statistics over the last N days.

    Returns
    -------
    dict with keys:
        total_signals       : int
        checked_signals     : int
        by_score_tier       : dict mapping score tier to {total, wins, win_rate}
        by_signal_type      : dict mapping BUY/SELL to {total, wins, win_rate}
        by_timeframe        : dict mapping TF to {total, wins, win_rate}
        avg_rr_winners      : float — average R:R of winning trades
        avg_rr_losers       : float — average R:R of losing trades
    """
    if config is None:
        try:
            from scanner import load_config
            config = load_config()
        except Exception:
            config = {}

    tz   = _get_tz(config)
    conn = _get_connection(config)
    try:
        cutoff = (datetime.now(tz).replace(tzinfo=None) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        rows = conn.execute("""
            SELECT * FROM signals
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """, (cutoff,)).fetchall()

        stats = {
            "total_signals": len(rows),
            "checked_signals": sum(1 for r in rows if r["outcome_checked"]),
            "by_score_tier": {},
            "by_signal_type": {},
            "by_timeframe": {},
            "avg_rr_winners": 0.0,
            "avg_rr_losers": 0.0,
        }

        def _tier(score):
            if score >= 70:
                return "70-100"
            elif score >= 40:
                return "40-69"
            else:
                return "0-39"

        def _update_bucket(bucket, key, is_win):
            if key not in bucket:
                bucket[key] = {"total": 0, "wins": 0, "win_rate": 0.0}
            bucket[key]["total"] += 1
            if is_win:
                bucket[key]["wins"] += 1
            t = bucket[key]["total"]
            bucket[key]["win_rate"] = round(bucket[key]["wins"] / t * 100, 1) if t > 0 else 0.0

        winner_rrs = []
        loser_rrs = []

        for r in rows:
            if not r["outcome_checked"]:
                continue

            is_win = r["outcome_hit_target"] == 1 or (r["outcome_pnl_pct"] is not None and r["outcome_pnl_pct"] > 0)

            _update_bucket(stats["by_score_tier"], _tier(r["setup_score"]), is_win)
            _update_bucket(stats["by_signal_type"], r["signal_type"], is_win)
            if r["timeframe"]:
                _update_bucket(stats["by_timeframe"], r["timeframe"], is_win)

            if r["risk_reward"] is not None:
                if is_win:
                    winner_rrs.append(r["risk_reward"])
                else:
                    loser_rrs.append(r["risk_reward"])

        stats["avg_rr_winners"] = round(sum(winner_rrs) / len(winner_rrs), 2) if winner_rrs else 0.0
        stats["avg_rr_losers"] = round(sum(loser_rrs) / len(loser_rrs), 2) if loser_rrs else 0.0

        return stats

    finally:
        conn.close()


def get_signal_history(limit: int = 50, offset: int = 0) -> list[dict]:
    """
    Retrieve paginated signal history from the database.

    Returns a list of signal dicts, most recent first.
    """
    conn = _get_connection(None)
    try:
        rows = conn.execute("""
            SELECT * FROM signals
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        results = []
        for r in rows:
            results.append({
                "id":                   r["id"],
                "timestamp":            r["timestamp"],
                "symbol":               r["symbol"],
                "signal_type":          r["signal_type"],
                "close_price":          r["close_price"],
                "setup_score":          r["setup_score"],
                "score_reasons":        json.loads(r["score_reasons"]) if r["score_reasons"] else [],
                "stop_loss":            r["stop_loss"],
                "target":               r["target"],
                "risk_reward":          r["risk_reward"],
                "triggered_conditions": json.loads(r["triggered_conditions"]) if r["triggered_conditions"] else [],
                "timeframe":            r["timeframe"],
                "adx":                  r["adx"],
                "rs_ratio":             r["rs_ratio"],
                "mtf_trend":            r["mtf_trend"],
                "outcome_checked":      bool(r["outcome_checked"]),
                "outcome_pnl_pct":      r["outcome_pnl_pct"],
                "outcome_hit_target":   bool(r["outcome_hit_target"]),
                "outcome_hit_stop":     bool(r["outcome_hit_stop"]),
                "outcome_price":        r["outcome_price"],
                "outcome_time":         r["outcome_time"],
            })
        return results
    finally:
        conn.close()


def clear_all_signals() -> bool:
    """Clear all logged signals from the database to start fresh."""
    conn = _get_connection(None)
    try:
        conn.execute("DELETE FROM signals")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'signals'")
        except Exception:
            pass
        conn.commit()
        log.info("Cleared all signal history from database.")
        return True
    except Exception as exc:
        conn.rollback()
        log.error("DB error clearing signal history: %s", exc)
        return False
    finally:
        conn.close()
