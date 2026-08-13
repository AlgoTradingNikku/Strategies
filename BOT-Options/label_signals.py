"""
===============================================================================
  Label Signals — offline script to label UT Bot signal outcomes
===============================================================================

Run this script after market close each day (or on weekends) to retroactively
label the signals logged by the bot.

Labelling logic:
    - Fetch candle data for the symbol/timeframe after each signal bar
    - Measure price change N candles after entry (configurable via config.yml)
    - BUY win  → price went UP   by >= win_threshold_pct %
    - SELL win → price went DOWN by >= win_threshold_pct %
    - label_5  : outcome after 5 candles
    - label_10 : outcome after 10 candles

Usage:
    python label_signals.py                   # label everything pending
    python label_signals.py --dry-run         # preview without writing
    python label_signals.py --status          # show DB summary
===============================================================================
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from openalgo import api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("Labeler")

_dir       = Path(__file__).resolve().parent
DB_PATH    = _dir / "signals.db"
CONFIG_PATH = _dir / "config.yml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Data fetch (reuses same logic as the bot)
# ---------------------------------------------------------------------------

def _fetch_candles(
    client,
    symbol:    str,
    exchange:  str,
    timeframe: str,
    start_str: str,
    end_str:   str,
) -> pd.DataFrame | None:
    try:
        raw = client.history(
            symbol=symbol, exchange=exchange, interval=timeframe,
            start_date=start_str, end_date=end_str,
        )
    except Exception as exc:
        log.error("Fetch error for %s %s: %s", symbol, timeframe, exc)
        return None

    if isinstance(raw, pd.DataFrame):
        df = raw
    elif isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, pd.DataFrame):
            df = data
        elif isinstance(data, list) and data:
            df = pd.DataFrame(data)
        else:
            return None
    else:
        return None

    if df is None or df.empty:
        return None

    for col in ("datetime", "timestamp"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
            df = df.set_index(col)
            break
    else:
        df.index = pd.to_datetime(df.index)

    df.columns = [c.lower() for c in df.columns]
    return df.sort_index()


# ---------------------------------------------------------------------------
# Labelling
# ---------------------------------------------------------------------------

def _nearest_loc(index: pd.DatetimeIndex, ts: pd.Timestamp) -> int:
    """Return the integer location of the bar closest to `ts`."""
    diffs = abs(index - ts)
    return int(diffs.argmin())


def label_signals(dry_run: bool = False) -> None:
    config = load_config()
    oa     = config.get("openalgo", {})
    ml_cfg = config.get("ml", {})

    lookahead        = int(ml_cfg.get("label_lookahead",   5))
    win_threshold    = float(ml_cfg.get("win_threshold_pct", 0.3))
    exchange         = config.get("exchange", "NSE")

    client = api(api_key=oa["apikey"], host=oa["base_url"])

    conn      = sqlite3.connect(str(DB_PATH))
    unlabeled = pd.read_sql(
        "SELECT * FROM signals WHERE labeled = 0 ORDER BY bar_time ASC",
        conn,
    )

    if unlabeled.empty:
        log.info("✅ Nothing to label — all signals are already labeled.")
        conn.close()
        return

    log.info("Found %d unlabeled signals.", len(unlabeled))
    updated = 0

    # Group by (symbol, timeframe) for efficient API usage
    for (symbol, timeframe), group in unlabeled.groupby(["symbol", "timeframe"]):
        log.info("  Processing %d signals — %s @ %s", len(group), symbol, timeframe)

        min_dt   = pd.to_datetime(group["bar_time"].min())
        max_dt   = pd.to_datetime(group["bar_time"].max())
        start_str = (min_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        end_str   = (max_dt + timedelta(days=7)).strftime("%Y-%m-%d")

        df = _fetch_candles(client, symbol, exchange, timeframe, start_str, end_str)
        if df is None:
            log.warning("  ⚠  Could not fetch data for %s %s — skipping.", symbol, timeframe)
            continue

        for _, row in group.iterrows():
            bar_ts = pd.to_datetime(row["bar_time"])
            loc    = _nearest_loc(df.index, bar_ts)

            # Check we have enough future bars
            future = df.iloc[loc + 1:]
            if len(future) < lookahead:
                log.debug("  Not enough future bars yet for %s @ %s.", symbol, bar_ts)
                continue

            entry = float(row["close"])
            stype = row["signal_type"]

            def _pct(exit_price: float) -> float:
                if stype == "BUY":
                    return (exit_price - entry) / entry * 100
                else:
                    return (entry - exit_price) / entry * 100

            # label_5
            outcome_5 = label_5 = None
            if len(future) >= 5:
                exit5     = float(future["close"].iloc[4])
                outcome_5 = _pct(exit5)
                label_5   = 1 if outcome_5 >= win_threshold else 0

            # label_10
            outcome_10 = label_10 = None
            if len(future) >= 10:
                exit10      = float(future["close"].iloc[9])
                outcome_10  = _pct(exit10)
                label_10    = 1 if outcome_10 >= win_threshold else 0

            if label_5 is None:
                continue   # can't label yet

            if not dry_run:
                conn.execute(
                    """
                    UPDATE signals
                    SET outcome_5=?, outcome_10=?, label_5=?, label_10=?, labeled=1
                    WHERE id=?
                    """,
                    (outcome_5, outcome_10, label_5, label_10, int(row["id"])),
                )
            else:
                log.info(
                    "  [DRY-RUN] id=%d  %s %s  outcome_5=%.2f%%  label_5=%d",
                    int(row["id"]), symbol, stype,
                    outcome_5 if outcome_5 is not None else float("nan"),
                    label_5 if label_5 is not None else -1,
                )
            updated += 1

        if not dry_run:
            conn.commit()
        log.info("  ✅ Labeled %d signals for %s @ %s.", updated, symbol, timeframe)

    conn.close()
    log.info("Labelling complete. %d signals updated.", updated)


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------

def print_status() -> None:
    if not DB_PATH.exists():
        print("No signals database found yet. Start the bot to collect data.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    total    = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    labeled  = conn.execute("SELECT COUNT(*) FROM signals WHERE labeled=1").fetchone()[0]
    unlabeled = total - labeled

    print(f"\n{'─'*45}")
    print(f"  Signal Database: {DB_PATH.name}")
    print(f"{'─'*45}")
    print(f"  Total signals  : {total}")
    print(f"  Labeled        : {labeled}")
    print(f"  Unlabeled      : {unlabeled}")

    if labeled > 0:
        wins5  = conn.execute("SELECT COUNT(*) FROM signals WHERE label_5=1").fetchone()[0]
        wr5    = wins5 / labeled * 100
        wins10 = conn.execute("SELECT COUNT(*) FROM signals WHERE label_10=1").fetchone()[0]
        wr10   = wins10 / labeled * 100 if wins10 else 0
        print(f"  Win rate (5c)  : {wr5:.1f}%  ({wins5}/{labeled})")
        print(f"  Win rate (10c) : {wr10:.1f}%  ({wins10}/{labeled})")

    # Per-symbol breakdown
    rows = conn.execute(
        """
        SELECT symbol, timeframe, signal_type,
               COUNT(*) as n,
               SUM(CASE WHEN labeled=1 THEN 1 ELSE 0 END) as n_labeled
        FROM signals
        GROUP BY symbol, timeframe, signal_type
        ORDER BY symbol, timeframe
        """
    ).fetchall()

    if rows:
        print(f"\n  {'Symbol':<12} {'TF':<5} {'Type':<5} {'Total':>6} {'Labeled':>8}")
        print(f"  {'─'*40}")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]:<5} {r[2]:<5} {r[3]:>6} {r[4]:>8}")

    print(f"{'─'*45}\n")
    conn.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label UT Bot signals for ML training")
    parser.add_argument("--dry-run",  action="store_true", help="Preview labels without writing")
    parser.add_argument("--status",   action="store_true", help="Show DB summary and exit")
    args = parser.parse_args()

    if args.status:
        print_status()
        sys.exit(0)

    label_signals(dry_run=args.dry_run)
