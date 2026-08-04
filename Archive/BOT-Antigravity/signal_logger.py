"""
===============================================================================
  Signal Logger — captures UT Bot signals with ML features to SQLite
===============================================================================

Every Buy/Sell signal detected by the bot is persisted here with ~10 technical
features computed at signal time.  These rows are later labelled by
`label_signals.py` and used to train the XGBoost filter in `ml_filter.py`.

Schema
------
signals table columns:
    id, logged_at, bar_time, symbol, timeframe, signal_type,
    close, atr, n_loss, atr_stop, atr_pct,
    volume, volume_ratio, rsi_14, ema20_dist_pct,
    candle_body_pct, atr_percentile,
    hour, minute, day_of_week,
    outcome_5, outcome_10, label_5, label_10, labeled
===============================================================================
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("UTBot.SignalLogger")

DB_PATH = Path(__file__).parent / "signals.db"

_DDL = """
CREATE TABLE IF NOT EXISTS signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at        TEXT    NOT NULL,
    bar_time         TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    timeframe        TEXT    NOT NULL,
    signal_type      TEXT    NOT NULL,
    close            REAL,
    atr              REAL,
    n_loss           REAL,
    atr_stop         REAL,
    atr_pct          REAL,
    volume           REAL,
    volume_ratio     REAL,
    rsi_14           REAL,
    ema20_dist_pct   REAL,
    candle_body_pct  REAL,
    atr_percentile   REAL,
    hour             INTEGER,
    minute           INTEGER,
    day_of_week      INTEGER,
    outcome_5        REAL,
    outcome_10       REAL,
    label_5          INTEGER,
    label_10         INTEGER,
    labeled          INTEGER DEFAULT 0
)
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Return an open SQLite connection, creating the DB/table if needed."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute(_DDL)
    conn.commit()
    return conn


def _compute_rsi(close: pd.Series, period: int, idx: int) -> float:
    """Wilder RSI at position `idx` (negative indices supported)."""
    end = idx + 1 if idx >= 0 else len(close) + idx + 1
    start = max(0, end - period * 3)          # warmup buffer
    sub = close.iloc[start:end]
    if len(sub) < period + 1:
        return float("nan")
    delta = sub.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_features(df: pd.DataFrame, signal_idx: int) -> dict:
    """
    Extract ML features at the bar identified by `signal_idx`.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV dataframe enriched by ``compute_utbot_signals`` (contains
        columns: open, high, low, close, volume, atr, nLoss, xATRTrailingStop).
    signal_idx : int
        Integer position of the signal bar (typically -2 for last closed).

    Returns
    -------
    dict of feature_name → float
    """
    bar = df.iloc[signal_idx]
    ts  = df.index[signal_idx]

    close  = float(bar["close"])
    atr    = float(bar.get("atr",              float("nan")))
    n_loss = float(bar.get("nLoss",            float("nan")))
    atr_stop = float(bar.get("xATRTrailingStop", float("nan")))
    atr_pct  = (atr / close * 100) if close else float("nan")

    # ── Volume ratio (current / 20-bar average) ──────────────────────────────
    vol = float(bar.get("volume", float("nan")))
    if "volume" in df.columns and len(df) >= 20:
        window_start = max(0, signal_idx - 20) if signal_idx >= 0 else max(0, len(df) + signal_idx - 20)
        avg_vol = df["volume"].iloc[window_start:signal_idx].mean()
        volume_ratio = vol / avg_vol if avg_vol and not np.isnan(avg_vol) else float("nan")
    else:
        volume_ratio = float("nan")

    # ── RSI (14) ─────────────────────────────────────────────────────────────
    rsi_14 = _compute_rsi(df["close"], period=14, idx=signal_idx)

    # ── EMA-20 distance % ────────────────────────────────────────────────────
    if len(df) >= 20:
        ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[signal_idx]
        ema20_dist_pct = (close - float(ema20)) / close * 100
    else:
        ema20_dist_pct = float("nan")

    # ── Candle body % of range ───────────────────────────────────────────────
    high  = float(bar["high"])
    low   = float(bar["low"])
    rng   = high - low
    body  = abs(close - float(bar["open"]))
    candle_body_pct = body / rng if rng else float("nan")

    # ── ATR percentile among last 20 ATR values ───────────────────────────────
    if "atr" in df.columns:
        window_start = max(0, signal_idx - 20) if signal_idx >= 0 else max(0, len(df) + signal_idx - 20)
        recent_atrs = df["atr"].iloc[window_start:signal_idx + 1].dropna()
        if len(recent_atrs) > 1:
            atr_percentile = float((recent_atrs < atr).mean() * 100)
        else:
            atr_percentile = float("nan")
    else:
        atr_percentile = float("nan")

    return {
        "close":           close,
        "atr":             atr,
        "n_loss":          n_loss,
        "atr_stop":        atr_stop,
        "atr_pct":         atr_pct,
        "volume":          vol,
        "volume_ratio":    volume_ratio,
        "rsi_14":          rsi_14,
        "ema20_dist_pct":  ema20_dist_pct,
        "candle_body_pct": candle_body_pct,
        "atr_percentile":  atr_percentile,
        "hour":            ts.hour,
        "minute":          ts.minute,
        "day_of_week":     ts.dayofweek,
    }


def log_signal(
    bar_time:    datetime,
    symbol:      str,
    timeframe:   str,
    signal_type: str,
    features:    dict,
) -> None:
    """
    Persist one signal row to the SQLite database.

    Parameters
    ----------
    bar_time    : timestamp of the closed candle that triggered the signal
    symbol      : e.g. "IOC"
    timeframe   : e.g. "5m"
    signal_type : "BUY" or "SELL"
    features    : dict returned by ``extract_features``
    """
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO signals (
                logged_at, bar_time, symbol, timeframe, signal_type,
                close, atr, n_loss, atr_stop, atr_pct,
                volume, volume_ratio, rsi_14, ema20_dist_pct,
                candle_body_pct, atr_percentile,
                hour, minute, day_of_week
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?
            )
            """,
            (
                datetime.now().isoformat(),
                bar_time.isoformat(),
                symbol,
                timeframe,
                signal_type,
                features.get("close"),
                features.get("atr"),
                features.get("n_loss"),
                features.get("atr_stop"),
                features.get("atr_pct"),
                features.get("volume"),
                features.get("volume_ratio"),
                features.get("rsi_14"),
                features.get("ema20_dist_pct"),
                features.get("candle_body_pct"),
                features.get("atr_percentile"),
                features.get("hour"),
                features.get("minute"),
                features.get("day_of_week"),
            ),
        )
        conn.commit()
        conn.close()
        log.debug("Logged %s signal for %s @ %s", signal_type, symbol, bar_time)
    except Exception as exc:
        log.error("signal_logger: failed to write row — %s", exc)


def signal_count() -> int:
    """Return total number of logged signals (labeled + unlabeled)."""
    try:
        conn = _get_conn()
        n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def labeled_count() -> int:
    """Return number of signals that have been labeled."""
    try:
        conn = _get_conn()
        n = conn.execute("SELECT COUNT(*) FROM signals WHERE labeled=1").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0
