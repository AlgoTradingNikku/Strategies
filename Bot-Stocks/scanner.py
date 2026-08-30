"""
===============================================================================
  Bot-UTBot-SR Channels — Nifty Scanner
===============================================================================

Scans NSE index stocks for Buy/Sell signals using:
  1. UT Bot (ATR trailing stop crossover)
  2. S/R Channels (support/resistance zone proximity)

Signal mode is controlled by 'signal_mode' in config.yml:
  "UTBot"    — UT Bot signals only
  "SR"       — S/R Channel signals only
  "UTBot+SR" — Both UT Bot AND S/R Channel conditions must be satisfied

Usage:
    python scanner.py              # Continuous scanning (re-scans every interval)
    python scanner.py --once       # Single scan and exit
    python scanner.py --tf 5m      # Override timeframe from CLI
    python scanner.py --segment BANKNIFTY --once
    python scanner.py --mode SR --once
    python scanner.py --list-segments

Stop:
    Ctrl+C
===============================================================================
"""

import sys
import argparse
import copy
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

# Sprint 4: Critical reliability and performance imports
from api_rate_limiter import get_rate_limiter
from circuit_breaker import CircuitBreaker
from trade_management.models import calc_sl_price

# ---------------------------------------------------------------------------
# Ensure UTF-8 output on Windows so emojis / box-drawing chars don't crash
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    import io as _io
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_bot_dir = Path(__file__).resolve().parent

# Define formatters
console_formatter = logging.Formatter("%(message)s")
file_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

# Create handlers
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(console_formatter)

file_handler = logging.FileHandler(
    _bot_dir / "scanner.log",
    encoding="utf-8",
)
file_handler.setFormatter(file_formatter)

# Configure logger
log = logging.getLogger("UTBotSRChannelsScanner")
log.setLevel(logging.INFO)
# Clear default handlers to avoid duplicate output
for handler in list(log.handlers):
    log.removeHandler(handler)
log.addHandler(console_handler)
log.addHandler(file_handler)
log.propagate = False  # Avoid propagating up to root logger

# Suppress verbose OpenAlgo library logging (floods console with "Debug - API Response Status")
logging.getLogger("data").setLevel(logging.WARNING)
logging.getLogger("openalgo").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Local modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert                       # noqa: E402
from nse_indices import get_index_symbols, list_available_segments  # noqa: E402
from signal_db import log_signals_batch, check_outcomes                            # noqa: E402
from regime    import classify_regime                                              # noqa: E402
import regime_gate                                                                  # noqa: E402
import signal_grader                                                                # noqa: E402
from signals import (                                          # noqa: E402
    compute_utbot_signals,
    compute_sr_signals,
    evaluate_composite_signals,
    check_mtf_confirmation,
    calculate_risk_reward,
)


# ---------------------------------------------------------------------------
# Sprint 4: Timeout wrapper for data fetches
# ---------------------------------------------------------------------------
def _timeout_wrapper(func, *args, timeout=15, **kwargs):
    """
    Execute func with a timeout. Returns result or raises TimeoutError.
    
    Uses ThreadPoolExecutor with a simple timeout mechanism.
    For critical operations like yfinance data fetch that sometimes hang.
    """
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"{func.__name__} timed out after {timeout}s")


# ============================================================================
# CONFIG
# ============================================================================

def load_config(path: Path | str = None) -> dict:
    """Load and return the YAML configuration file."""
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ============================================================================
# DATA FETCHING
# ============================================================================

# ---------------------------------------------------------------------------
# Index Symbol Mapping (Multi-Source Support)
# ---------------------------------------------------------------------------
def get_index_symbol(index_name: str, data_source: str) -> str:
    """
    Get the correct index symbol format for the configured data source.
    
    Parameters
    ----------
    index_name : str
        Standard index name: "NIFTY50", "BANKNIFTY", "FINNIFTY", "NIFTYIT"
    data_source : str
        Data provider: "yfinance", "openalgo", "tvdatafeed", "twelvedata"
    
    Returns
    -------
    str
        Data-source-specific symbol format
        
    Examples
    --------
    >>> get_index_symbol("NIFTY50", "yfinance")
    "^NSEI"
    >>> get_index_symbol("NIFTY50", "openalgo")
    "NIFTY 50"
    """
    INDEX_SYMBOL_MAP = {
        "NIFTY50": {
            "yfinance": "^NSEI",
            "openalgo": "NIFTY 50",
            "tvdatafeed": "NIFTY",
            "twelvedata": "NIFTY50",
        },
        "BANKNIFTY": {
            "yfinance": "^NSEBANK",
            "openalgo": "NIFTY BANK",
            "tvdatafeed": "BANKNIFTY",
            "twelvedata": "BANKNIFTY",
        },
        "FINNIFTY": {
            "yfinance": "^CNXFIN",
            "openalgo": "FINNIFTY",
            "tvdatafeed": "FINNIFTY",
            "twelvedata": "FINNIFTY",
        },
        "NIFTYIT": {
            "yfinance": "^CNXIT",
            "openalgo": "NIFTY IT",
            "tvdatafeed": "CNXIT",
            "twelvedata": "NIFTYIT",
        },
    }
    
    index_map = INDEX_SYMBOL_MAP.get(index_name.upper(), {})
    symbol = index_map.get(data_source.lower())
    
    if symbol is None:
        # Fallback: return the index name as-is
        log.warning(
            "Unknown index '%s' or data source '%s'. Using fallback: %s",
            index_name, data_source, index_name
        )
        return index_name
    
    return symbol


# ---------------------------------------------------------------------------
# OpenAlgo helpers (interval mapping + per-process broker capability cache)
# ---------------------------------------------------------------------------
# Bot-Stocks uses yfinance-style timeframes ("5m", "1h", "1d", "1W"). OpenAlgo
# expects broker-native codes ("5m", "1h", "D", "W"). Map before calling.
_OPENALGO_INTERVAL_MAP = {
    "1m": "1m", "2m": "2m", "3m": "3m", "5m": "5m",
    "10m": "10m", "15m": "15m", "20m": "20m", "30m": "30m",
    "1h": "1h", "2h": "2h", "3h": "3h", "4h": "4h",
    "1d": "D", "d": "D", "day": "D",
    "1w": "W", "w": "W", "1W": "W",
    "1M": "M", "1Q": "Q", "1Y": "Y",
}

# Cache client.intervals() response for the life of the process. Keyed on
# (host, apikey) so multi-tenant test envs stay separated. Value is either
# a set of supported broker codes, or None when the probe failed (in which
# case we skip the capability check silently and rely on the API's own 400).
_OPENALGO_SUPPORTED_CACHE: dict[tuple[str, str], set[str] | None] = {}


def _openalgo_map_interval(timeframe: str) -> str:
    """Translate a Bot-Stocks timeframe into an openalgo-native interval code."""
    return _OPENALGO_INTERVAL_MAP.get(timeframe, _OPENALGO_INTERVAL_MAP.get(timeframe.lower(), timeframe))


def _openalgo_supported_intervals(client, host: str, apikey: str) -> set[str] | None:
    """Return the set of interval codes the active openalgo broker supports.

    Cached per process. Returns None on any error so the caller can skip the
    pre-flight check gracefully.
    """
    key = (host, apikey)
    if key in _OPENALGO_SUPPORTED_CACHE:
        return _OPENALGO_SUPPORTED_CACHE[key]
    try:
        resp = client.intervals()
        if isinstance(resp, dict) and resp.get("status") == "success":
            data = resp.get("data", {}) or {}
            supported: set[str] = set()
            for group in ("seconds", "minutes", "hours", "days", "weeks", "months"):
                supported.update(data.get(group, []) or [])
            _OPENALGO_SUPPORTED_CACHE[key] = supported or None
            return _OPENALGO_SUPPORTED_CACHE[key]
    except Exception:
        pass
    _OPENALGO_SUPPORTED_CACHE[key] = None
    return None


def _parse_timeframe(tf: str) -> timedelta:
    """Convert a timeframe string (e.g. '15m', '1h', '1d') to a timedelta."""
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return timedelta(minutes=int(tf[:-1]))
    if tf.endswith("h"):
        return timedelta(hours=int(tf[:-1]))
    if tf in ("d", "1d", "day"):
        return timedelta(days=1)
    if tf in ("1w", "w"):
        return timedelta(weeks=1)
    raise ValueError(f"Unsupported timeframe: {tf!r}")


def fetch_history(symbol: str, timeframe: str, config: dict) -> pd.DataFrame | None:
    """
    Fetch OHLCV candles for a single symbol from the configured data source.

    Supported data sources (set via config.yml → data_source):
      - yfinance   (default)
      - tvdatafeed
      - twelvedata
      - openalgo

    Returns a DataFrame with lowercase columns [open, high, low, close, volume]
    indexed by datetime, or None on failure.
    """
    data_source  = config.get("data_source", "yfinance").lower()
    exchange     = config.get("exchange", "NSE")
    lookback_days = int(config.get("data", {}).get("lookback_days", 30))

    # yfinance hard limits for intraday intervals — silently cap to avoid empty results.
    # Limits: 1m=7d, 2m/5m/15m/30m/60m/90m practical safe cap is 7d (API often fails >7d).
    _YF_MAX_DAYS = {"1m": 7, "2m": 7, "5m": 7, "15m": 7, "30m": 7, "60m": 7, "90m": 7}
    if data_source == "yfinance" and timeframe in _YF_MAX_DAYS:
        max_allowed = _YF_MAX_DAYS[timeframe]
        if lookback_days > max_allowed:
            log.debug(
                "[%s] lookback_days=%d exceeds yfinance %s limit (%d days); capping to %d.",
                symbol, lookback_days, timeframe, max_allowed, max_allowed,
            )
            lookback_days = max_allowed

    # Calculate date range, adjusting end_date for weekends/non-trading days
    end_dt = datetime.now()
    
    # If today is Saturday/Sunday, roll back to last Friday to avoid
    # "no data available" errors from brokers that reject future/non-trading dates
    if end_dt.weekday() >= 5:  # 5=Saturday, 6=Sunday
        days_since_friday = end_dt.weekday() - 4
        end_dt = end_dt - timedelta(days=days_since_friday)
        log.debug(
            "[%s] Weekend detected; adjusted end_date to last Friday: %s",
            symbol, end_dt.strftime("%Y-%m-%d")
        )
    
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str   = end_dt.strftime("%Y-%m-%d")


    try:
        # ------------------------------------------------------------------
        if data_source == "yfinance":
            import yfinance as yf
            import random

            # Initial jitter to prevent synchronized concurrent request spikes
            time.sleep(random.uniform(0.05, 0.4))

            yf_symbol = symbol
            if symbol.startswith("^"):
                pass
            elif exchange == "NSE":
                yf_symbol = f"{symbol}.NS"
            elif exchange == "BSE":
                yf_symbol = f"{symbol}.BO"

            # Try up to 3 times with exponential backoff + jitter
            max_retries = 3
            df = pd.DataFrame()
            for attempt in range(max_retries):
                try:
                    # Sprint 4: Wrap yfinance.download with 15s timeout to prevent hangs
                    def _yf_download():
                        return yf.download(
                            tickers=yf_symbol,
                            start=start_dt,
                            end=end_dt,
                            interval=timeframe,
                            progress=False,
                            auto_adjust=True,
                        )
                    
                    df = _timeout_wrapper(_yf_download, timeout=15)
                    
                    if not df.empty:
                        break
                except TimeoutError as timeout_err:
                    log.warning("[%s] yfinance fetch timed out (attempt %d/%d): %s", symbol, attempt+1, max_retries, timeout_err)
                    if attempt == max_retries - 1:
                        return None
                except Exception as e:
                    if attempt == max_retries - 1:
                        log.warning("[%s] yfinance fetch failed after %d attempts: %s", symbol, max_retries, e)
                        return None
                time.sleep(1.0 * (attempt + 1) + random.uniform(0.1, 0.5))

            if df.empty:
                log.warning("[%s] No data from yfinance.", symbol)
                return None

            # Flatten MultiIndex if present (yfinance >= 0.2.x)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        # ------------------------------------------------------------------
        elif data_source == "tvdatafeed":
            from tvDatafeed import TvDatafeed, Interval

            tv_cfg   = config.get("tvdatafeed", {})
            username = tv_cfg.get("username", "")
            password = tv_cfg.get("password", "")

            tv = TvDatafeed(username=username, password=password) if (username and password) else TvDatafeed()

            interval_map = {
                "1m":  Interval.in_1_minute,
                "3m":  Interval.in_3_minute,
                "5m":  Interval.in_5_minute,
                "15m": Interval.in_15_minute,
                "30m": Interval.in_30_minute,
                "45m": Interval.in_45_minute,
                "1h":  Interval.in_1_hour,
                "2h":  Interval.in_2_hour,
                "3h":  Interval.in_3_hour,
                "4h":  Interval.in_4_hour,
                "1d":  Interval.in_daily,
                "1W":  Interval.in_weekly,
                "1M":  Interval.in_monthly,
            }
            tv_interval = interval_map.get(timeframe, Interval.in_15_minute)

            candle_dur = _parse_timeframe(timeframe)
            tf_minutes = max(1, int(candle_dur.total_seconds() / 60))
            bars_per_day = (24 * 60) // tf_minutes
            n_bars = min(5000, lookback_days * bars_per_day)

            df = tv.get_hist(symbol=symbol, exchange=exchange, interval=tv_interval, n_bars=n_bars)

            if df is None or df.empty:
                log.warning("[%s] No data from tvdatafeed.", symbol)
                return None

            df.columns = [c.lower() for c in df.columns]
            if "symbol" in df.columns:
                df = df.drop(columns=["symbol"])
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        # ------------------------------------------------------------------
        elif data_source == "twelvedata":
            from twelvedata import TDClient

            td_cfg = config.get("twelvedata", {})
            apikey = td_cfg.get("apikey", "")
            if not apikey:
                log.error("[%s] TwelveData API key missing in config.yml.", symbol)
                return None

            td = TDClient(apikey=apikey)
            td_interval = timeframe
            if td_interval.endswith("m") and td_interval != "1month":
                td_interval = td_interval + "in"
            elif td_interval == "1d":
                td_interval = "1day"
            elif td_interval == "1W":
                td_interval = "1week"

            ts = td.time_series(
                symbol=symbol,
                interval=td_interval,
                start_date=start_str,
                end_date=end_str,
                outputsize=5000,
            )
            df = ts.as_pandas()

            if df is None or df.empty:
                log.warning("[%s] No data from twelvedata.", symbol)
                return None

            df = df.iloc[::-1]  # oldest first
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        # ------------------------------------------------------------------
        elif data_source == "openalgo":
            from openalgo import api as oa_api
            import random

            oa_cfg = config.get("openalgo", {})
            host   = oa_cfg.get("base_url", "http://127.0.0.1:5000")
            apikey = oa_cfg.get("apikey", "")
            client = oa_api(api_key=apikey, host=host)

            # --- Interval mapping (openalgo uses "D"/"W"/"M", not "1d"/"1W"/"1M") ---
            oa_interval = _openalgo_map_interval(timeframe)

            # --- Pre-flight capability check (broker-specific) -----------------
            supported = _openalgo_supported_intervals(client, host, apikey)
            if supported is not None and oa_interval not in supported:
                log.warning(
                    "[%s] timeframe %r (openalgo=%r) not supported by active broker; supported=%s",
                    symbol, timeframe, oa_interval, sorted(supported),
                )
                return None

            # --- Fetch with retry/backoff (mirrors yfinance branch) ------------
            max_retries = 3
            raw = None
            for attempt in range(max_retries):
                try:
                    raw = client.history(
                        symbol=symbol,
                        exchange=exchange,
                        interval=oa_interval,
                        start_date=start_str,
                        end_date=end_str,
                    )
                    # Success signal: a DataFrame, or a dict with status=='success'.
                    if isinstance(raw, pd.DataFrame) and not raw.empty:
                        break
                    if isinstance(raw, dict):
                        if raw.get("status") == "error":
                            # Structured API error — surface once, don't retry blindly
                            log.warning(
                                "[%s] openalgo error: %s (code=%s)",
                                symbol, raw.get("message"), raw.get("code"),
                            )
                            return None
                        if raw.get("data") is not None:
                            break
                except Exception as ex:
                    if attempt == max_retries - 1:
                        log.warning(
                            "[%s] openalgo fetch failed after %d attempts: %s",
                            symbol, max_retries, ex,
                        )
                        return None
                time.sleep(0.5 * (attempt + 1) + random.uniform(0.05, 0.25))

            # --- Normalise response into a DataFrame ---------------------------
            if isinstance(raw, pd.DataFrame):
                df = raw
            elif isinstance(raw, dict):
                data = raw.get("data")
                if isinstance(data, pd.DataFrame):
                    df = data
                elif isinstance(data, list) and data:
                    df = pd.DataFrame(data)
                else:
                    log.warning("[%s] Unexpected history payload from openalgo.", symbol)
                    return None
            else:
                log.warning("[%s] Unexpected response type from openalgo.", symbol)
                return None

            if df is None or df.empty:
                log.warning("[%s] No data from openalgo.", symbol)
                return None

            # --- Index normalisation ------------------------------------------
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime")
            elif "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            else:
                df.index = pd.to_datetime(df.index)

            df = df.sort_index()
            df.columns = [c.lower() for c in df.columns]

            # Drop broker-specific columns not used anywhere in Bot-Stocks
            # (openalgo returns 'oi' / 'open_interest' for equities as 0).
            df = df.drop(columns=[c for c in ("oi", "open_interest") if c in df.columns])

            # Strip tz so downstream code matches the yfinance/tvdatafeed/twelvedata
            # branches (naive wall-clock in IST). Openalgo returns Asia/Kolkata tz-aware.
            if df.index.tz is not None:
                df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)

            # Collapse duplicate timestamps (Historify occasionally emits dupes on
            # backfill boundaries).
            df = df[~df.index.duplicated(keep="last")]

            # Coerce OHLC to numeric and drop rows with NaN in any OHLC field.
            for _c in ("open", "high", "low", "close"):
                if _c in df.columns:
                    df[_c] = pd.to_numeric(df[_c], errors="coerce")
            df = df.dropna(subset=[c for c in ("open", "high", "low", "close") if c in df.columns])

            # Filter synthetic post-close "flat" bars: volume==0 AND OHLC all equal.
            # These are emitted by openalgo Historify at 15:15/15:20 IST after
            # session close and would pull ATR toward zero if left in place.
            if "volume" in df.columns and not df.empty:
                _flat = (
                    (df["volume"] == 0)
                    & (df["open"] == df["close"])
                    & (df["high"] == df["low"])
                    & (df["open"] == df["high"])
                )
                if _flat.any():
                    log.debug("[%s] dropping %d synthetic zero-vol flat bars.", symbol, int(_flat.sum()))
                    df = df[~_flat]

            if df.empty:
                log.warning("[%s] No usable rows after openalgo cleanup.", symbol)
                return None

        # ------------------------------------------------------------------
        else:
            log.error("[%s] Unknown data_source: %s", symbol, data_source)
            return None

        return df

    except Exception as exc:
        log.error("[%s] Fetch error (%s): %s", symbol, data_source, exc)
        return None


# ============================================================================
# SCANNER ENGINE
# ============================================================================

def _evaluate_trade(
    entry: float,
    atr: float,
    future_high: np.ndarray,
    future_low: np.ndarray,
    is_buy: bool,
) -> tuple[bool, bool, int, int]:
    """
    Determine whether TP and/or SL were hit for a single trade, and at which
    bar index (relative to entry+1) each event first occurred.

    Returns (tp_ok, sl_ok, tp_hit_idx, sl_hit_idx).
    When an event was NOT hit, the corresponding hit_idx is -1 (not 0) so the
    caller cannot accidentally interpret a "no hit" as "hit at bar 0".
    """
    if is_buy:
        sl, tp     = entry - 2.0 * atr, entry + 3.0 * atr
        tp_mask    = future_high >= tp
        sl_mask    = future_low  <= sl
    else:
        sl, tp     = entry + 2.0 * atr, entry - 3.0 * atr
        tp_mask    = future_low  <= tp
        sl_mask    = future_high >= sl

    tp_ok  = bool(tp_mask.any()) if tp_mask.size else False
    sl_ok  = bool(sl_mask.any()) if sl_mask.size else False
    tp_hit = int(np.argmax(tp_mask)) if tp_ok else -1
    sl_hit = int(np.argmax(sl_mask)) if sl_ok else -1
    return tp_ok, sl_ok, tp_hit, sl_hit


def calculate_historical_win_rate(df: pd.DataFrame, signal_type: str) -> float:
    """
    Run a lightweight proxy backtest on the DataFrame history for this symbol.
    Uses historical UT Bot signals and a standard 2 ATR stop / 3 ATR target (1.5 R:R).
    Returns win rate percentage (0-100) or None if no completed trades found.
    """
    # Structural guards first — skip ATR computation for degenerate inputs
    col = "ut_buy" if signal_type == "BUY" else "ut_sell"
    if len(df) < 50 or col not in df.columns:
        return None

    # Calculate ATR for the whole series
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_v = tr.rolling(14).mean().values

    if np.all(np.isnan(atr_v)):
        return None

    sig_idx = np.where(df[col].values)[0]
    close_v = df["close"].values
    high_v  = df["high"].values
    low_v   = df["low"].values
    is_buy  = signal_type == "BUY"

    wins   = 0
    losses = 0

    for i in sig_idx:
        if i >= len(df) - 1:
            continue
        curr_atr = atr_v[i]
        if np.isnan(curr_atr) or curr_atr == 0:
            continue

        tp_ok, sl_ok, tp_hit, sl_hit = _evaluate_trade(
            close_v[i], curr_atr, high_v[i + 1:], low_v[i + 1:], is_buy
        )

        if tp_ok and sl_ok:
            if tp_hit <= sl_hit:
                wins += 1
            else:
                losses += 1
        elif tp_ok:
            wins += 1
        elif sl_ok:
            losses += 1

    total = wins + losses
    if total == 0:
        return None
    return round((wins / total) * 100.0, 1)


def scan_symbol(
    symbol: str,
    timeframe: str,
    config: dict,
    lookback_candles: int = 2,
    nifty_df: pd.DataFrame = None,
    htf_df: pd.DataFrame = None,
) -> list[dict]:
    """
    Scan a single symbol through the enabled signal engines.

    Returns a list of result dicts (0, 1, or 2 — one per signal direction).
    Each dict contains signal metadata and which conditions triggered.

    Parameters
    ----------
    htf_df : optional pre-fetched higher-timeframe DataFrame. When provided,
             the MTF confirmation step uses it directly instead of making a
             second fetch_history call for this symbol.
    """
    strat  = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    filters_cfg = config.get("filters", {})

    df = fetch_history(symbol, timeframe, config)
    if df is None or len(df) < 20:
        return []

    # ---- Run enabled signal engines dynamically -----------------------------
    from signals import ENGINE_REGISTRY
    
    sr_zones: list = []  # Will be populated if S/R engine runs
    
    for engine in ENGINE_REGISTRY:
        cfg_section = config.get(engine["config_section"], {})
        is_enabled = cfg_section.get(engine["enabled_key"], True)
        
        if not is_enabled:
            continue
        
        # Execute engine based on its key
        if engine["key"] == "ut_bot":
            df = compute_utbot_signals(
                df,
                key_value       = float(strat.get("key_value", 1.0)),
                atr_period      = int(strat.get("atr_period", 2)),
                use_heikin_ashi = bool(strat.get("use_heikin_ashi", False)),
            )
        elif engine["key"] == "sr_channels":
            df, sr_zones = compute_sr_signals(
                df,
                pivot_period      = int(sr_cfg.get("pivot_period", 10)),
                source            = sr_cfg.get("source", "High/Low"),
                channel_width_pct = int(sr_cfg.get("channel_width_pct", 5)),
                min_strength      = int(sr_cfg.get("min_strength", 1)),
                max_num_sr        = int(sr_cfg.get("max_num_sr", 6)),
                loopback          = int(sr_cfg.get("loopback", 290)),
                proximity_pct     = float(sr_cfg.get("proximity_pct", 0.5)),
            )
        elif engine["key"] == "momentum":
            from signals import compute_momentum_signals
            momentum_cfg = config.get("momentum", {})
            df = compute_momentum_signals(df, momentum_cfg)
        elif engine["key"] == "mean_reversion":
            from signals import compute_mean_reversion_signals
            mr_cfg = config.get("mean_reversion", {})
            df = compute_mean_reversion_signals(df, mr_cfg)
        elif engine["key"] == "momentum_chatgpt":
            from signals import compute_momentum_chatgpt_signals
            df = compute_momentum_chatgpt_signals(df, config)
        # Future engines can be added here with new elif blocks

    # ---- Evaluate composite signals ----------------------------------------
    composite  = evaluate_composite_signals(df, config, lookback_candles, sr_zones=sr_zones)

    results    = []
    last_row   = df.iloc[-1]
    close_price = float(last_row["close"])
    zones = composite["details"].get("sr_zones", [])

    # ---- Multi-Timeframe Confirmation --------------------------------------
    # Use pre-fetched htf_df if supplied by run_scan (avoids a second HTTP call
    # per symbol). Falls back to an inline fetch when called standalone.
    mtf_result = None
    mtf_tf = filters_cfg.get("mtf_timeframe", "15m")
    if mtf_tf:
        try:
            if htf_df is None:
                htf_df = fetch_history(symbol, mtf_tf, config)
            mtf_result = check_mtf_confirmation(htf_df, config)
        except Exception as e:
            log.debug("MTF check failed for %s: %s", symbol, e)
            mtf_result = {"trend": "neutral", "htf_trail": None, "htf_close": None}

    # ---- Risk/Reward Calculation --------------------------------------------
    rr_result = {"stop_loss": None, "target": None, "risk_reward": None}
    if filters_cfg.get("risk_reward_enabled", True):
        # R:R is computed per signal direction, handled below per-result
        pass

    # ---- Relative Strength vs NIFTY50 --------------------------------------
    rs_ratio = None
    rs_period = int(filters_cfg.get("rs_period", 20))
    if nifty_df is not None and len(df) >= rs_period and len(nifty_df) >= rs_period:
        try:
            stock_ret = (df["close"].iloc[-1] / df["close"].iloc[-rs_period]) - 1.0
            nifty_ret = (nifty_df["close"].iloc[-1] / nifty_df["close"].iloc[-rs_period]) - 1.0
            if abs(nifty_ret) > 1e-10:
                rs_ratio = round((1.0 + stock_ret) / (1.0 + nifty_ret), 3)
        except Exception:
            rs_ratio = None

    base_info = {
        "symbol":      symbol,
        "close":       close_price,
        "signal_time": df.index[-1],
        "ut_trail":    composite["details"].get("ut_trail"),
        "ut_pos":      composite["details"].get("ut_pos"),
        "sr_zones":    zones,
        "adx":         composite["details"].get("adx"),
        "rs_ratio":    rs_ratio,
        "mtf":         mtf_result,
        "vol_ok":      composite.get("vol_ok", True),
        "ema_above":   composite.get("ema_above"),
        "adx_ok":      composite.get("adx_ok"),
        "rsi_ok":      composite.get("rsi_ok"),
        "sqz_ok":      composite.get("sqz_ok"),
    }

    # RS filter enablement state (needed by _build_result closure)
    rs_enabled = filters_cfg.get("rs_enabled", False)

    def _build_result(signal_type, triggered, score, reasons):
        """Build a single signal result dict with MTF, R:R, RS adjustments."""
        adj_score = score
        adj_reasons = list(reasons)
        
        # Historical Win-Rate Mini-Backtest
        hist_win_rate = None
        if filters_cfg.get("win_rate_backtest_enabled", False):
            hist_win_rate = calculate_historical_win_rate(df, signal_type)

        # MTF score adjustment
        if mtf_result and mtf_result["trend"] != "neutral":
            trend = mtf_result["trend"]
            confirms = (signal_type == "BUY" and trend == "bullish") or \
                       (signal_type == "SELL" and trend == "bearish")
            if confirms:
                adj_score += 15.0
                adj_reasons.append(f"MTF confirms {trend} trend (+15.0 pts)")
            else:
                if filters_cfg.get("mtf_filter_enabled", False):
                    return None
                adj_score -= 10.0
                adj_reasons.append(f"MTF counter-trend: {trend} (−10.0 pts)")
        elif mtf_result and mtf_result["trend"] == "neutral":
            adj_score += 5.0
            adj_reasons.append("MTF neutral (+5.0 pts)")

        # Relative Strength filter (HARD FILTER when enabled)
        rs_buy_thresh  = float(filters_cfg.get("rs_buy_threshold", 1.05))
        rs_sell_thresh = float(filters_cfg.get("rs_sell_threshold", 0.95))
        if rs_enabled and rs_ratio is not None:
            if signal_type == "BUY" and rs_ratio < rs_buy_thresh:
                # BUY signal but underperforming index → REJECT
                return None
            elif signal_type == "SELL" and rs_ratio > rs_sell_thresh:
                # SELL signal but outperforming index → REJECT
                return None
            # Pass through: signal aligns with RS
            if signal_type == "BUY":
                adj_reasons.append(f"✓ RS Filter: Outperforming (RS: {rs_ratio:.3f} >= {rs_buy_thresh})")
            else:
                adj_reasons.append(f"✓ RS Filter: Underperforming (RS: {rs_ratio:.3f} <= {rs_sell_thresh})")

        # Single final cap — applied here (Stage 2) AFTER all MTF and RS adjustments
        # so the full bonus impact is visible in scores rather than being silently truncated.
        adj_score = min(100.0, round(max(0.0, adj_score), 1))

        # Risk/Reward
        rr = {"stop_loss": None, "target": None, "risk_reward": None}
        if filters_cfg.get("risk_reward_enabled", True):
            rr = calculate_risk_reward(df, signal_type, zones, config)

        result = {
            **base_info,
            "signal":        signal_type,
            "triggered":     triggered,
            "setup_score":   adj_score,
            "score_reasons": adj_reasons,
            "stop_loss":     rr.get("stop_loss"),
            "target":        rr.get("target"),
            "risk_reward":   rr.get("risk_reward"),
            "hist_win_rate": hist_win_rate,
        }

        # ---- Sprint 3: signal grading -------------------------------------
        # Graded here (rather than in the auto-order block) so EVERY signal
        # carries a grade — including ones that never reach auto-order because
        # auto_order is off or a gate rejects them. That's what makes
        # win-rate-by-grade analysis possible over the whole signal population
        # rather than just the traded subset.
        try:
            grade_info = signal_grader.grade_signal(result, config)
            result["grade"]           = grade_info.get("grade")
            result["grade_score"]     = grade_info.get("score")
            result["grade_breakdown"] = grade_info.get("breakdown", {})
        except Exception as ge:      # pragma: no cover — grader is fail-open
            log.debug("Grading failed for %s: %s", symbol, ge)

        return result

    if composite["buy"]:
        buy_res = _build_result(
            "BUY", composite["triggered_buy"],
            composite.get("buy_score", 0.0), composite.get("buy_reasons", []),
        )
        if buy_res is not None:
            results.append(buy_res)

    if composite["sell"]:
        sell_res = _build_result(
            "SELL", composite["triggered_sell"],
            composite.get("sell_score", 0.0), composite.get("sell_reasons", []),
        )
        if sell_res is not None:
            results.append(sell_res)

    return results


def run_scan(
    config: dict,
    timeframe_override: str = None,
    segment_override: str   = None,
    mode_override: str      = None,
) -> tuple[list[dict], list[dict], str, str, int, str]:
    """
    Scan all symbols in parallel and return buy/sell results.

    Returns
    -------
    tuple: (buy_results, sell_results, segment_label, timeframe, total_symbols_scanned, current_regime)
        - buy_results   : list[dict]  each dict is one BUY signal + metadata
        - sell_results  : list[dict]  each dict is one SELL signal + metadata
        - segment_label : str         human-readable segment name (e.g. "NIFTY50+CUSTOM")
        - timeframe     : str         effective candle timeframe used
        - total_symbols_scanned : int number of unique symbols the scanner iterated over
        - current_regime : str        Sprint-1.5 regime tag from NIFTY at scan time
                                       (e.g. "trending_up", "chop", "unknown")
    """
    strat  = copy.deepcopy(config.get("strategy", {}))
    sr_cfg = copy.deepcopy(config.get("sr_channels", {}))

    # Apply CLI mode overrides
    if mode_override:
        mode_upper = mode_override.upper().replace(" ", "")
        if mode_upper == "UTBOT":
            strat["ut_enabled"] = True
            sr_cfg["enabled"] = False
        elif mode_upper == "SR":
            strat["ut_enabled"] = False
            sr_cfg["enabled"] = True
        elif mode_upper == "UTBOT+SR":
            strat["ut_enabled"] = True
            sr_cfg["enabled"] = True

    # Construct a deep-copied config so mutations here don't affect the caller's dict
    config = copy.deepcopy(config)
    config["strategy"] = strat
    config["sr_channels"] = sr_cfg

    timeframe = timeframe_override or config.get("candle_timeframe", config.get("scan_timeframe", "5m"))
    lookback  = int(config.get("signal_lookback_candles", 2))

    # ---- Determine effective signal mode dynamically -----------------------
    from signals import ENGINE_REGISTRY
    
    active_engine_labels = []
    for engine in ENGINE_REGISTRY:
        cfg_section = config.get(engine["config_section"], {})
        is_enabled = cfg_section.get(engine["enabled_key"], True)
        if is_enabled:
            active_engine_labels.append(engine["label"])
    
    if len(active_engine_labels) == 0:
        eff_mode = "None"
    elif len(active_engine_labels) == 1:
        eff_mode = f"{active_engine_labels[0]} Only"
    else:
        eff_mode = " + ".join(active_engine_labels)

    # ---- Resolve symbols ---------------------------------------------------
    # segment can be a single string or a list of strings (from config or CLI).
    # use_symbols: true merges the custom symbols list on top of segment(s).
    segment     = segment_override or config.get("segment", "")
    use_symbols = config.get("use_symbols", False)

    # Normalise to a list of non-empty segment names
    if isinstance(segment, str):
        seg_list = [segment] if segment.strip() else []
    else:  # already a list
        seg_list = [s for s in (segment or []) if s and s.strip()]

    symbols       = []
    fetched_segs  = []
    failed_segs   = []

    for seg in seg_list:
        seg_syms = get_index_symbols(seg)
        if seg_syms:
            fetched_segs.append(seg.upper())
            symbols.extend(seg_syms)
        else:
            log.warning("Could not fetch symbols for '%s'.", seg)
            failed_segs.append(seg.upper())

    # Merge custom symbols list if use_symbols is True OR no segment was given
    custom_symbols = config.get("symbols", [])
    if use_symbols or not seg_list:
        symbols.extend(custom_symbols)
        if not seg_list:
            fetched_segs.append("CUSTOM")

    # If all segment fetches failed, fall back to custom list
    if seg_list and not fetched_segs and custom_symbols:
        log.warning("All segment fetches failed. Falling back to custom symbols list.")
        symbols       = list(custom_symbols)
        fetched_segs  = ["CUSTOM (fallback)"]

    # Deduplicate while preserving order
    seen = set()
    unique_symbols = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique_symbols.append(s)
    symbols = unique_symbols

    # Build a human-readable segment label
    if fetched_segs:
        segment_label = "+".join(fetched_segs)
    else:
        segment_label = "NONE"

    # ---- Build enabled engine list dynamically for logging -----------------
    from signals import ENGINE_REGISTRY
    
    engines = []
    for engine in ENGINE_REGISTRY:
        cfg_section = config.get(engine["config_section"], {})
        is_enabled = cfg_section.get(engine["enabled_key"], True)
        if is_enabled:
            engines.append(engine["label"])

    log.info("=" * 70)
    log.info("  UTBot + SR Channels Scanner — %s", segment_label)
    log.info("=" * 70)
    log.info("  Segment       : %s", segment_label)
    log.info("  Timeframe     : %s", timeframe)
    log.info("  Signal Mode   : %s", eff_mode)
    log.info("  Lookback      : %d candles (UT Bot window)", lookback)
    log.info("  Engines       : %s", " + ".join(engines) if engines else "NONE")
    log.info("  Symbols       : %d stocks", len(symbols))
    log.info("  Data Source   : %s", config.get("data_source", "yfinance").upper())
    tz = ZoneInfo("Asia/Kolkata") if config.get("exchange", "NSE").upper() in ("NSE", "BSE") else ZoneInfo("UTC")
    scan_time_str = datetime.now(tz).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    log.info("  Scan Time     : %s", scan_time_str)
    log.info("=" * 70)

    # ---- Fetch index data for Relative Strength ----------------------------
    nifty_df = None
    filters_cfg = config.get("filters", {})
    rs_enabled = filters_cfg.get("rs_enabled", False)
    
    if rs_enabled:
        data_source = config.get("data_source", "yfinance").lower()
        rs_index = filters_cfg.get("rs_index", "NIFTY50")
        
        try:
            index_symbol = get_index_symbol(rs_index, data_source)
            log.debug("Fetching %s as '%s' from %s for RS", rs_index, index_symbol, data_source)
            nifty_df = fetch_history(index_symbol, timeframe, config)
            if nifty_df is not None:
                log.info("  RS Index      : %s (%s) — %d bars fetched", rs_index, index_symbol, len(nifty_df))
        except Exception as e:
            log.warning("Could not fetch %s index for RS: %s", rs_index, e)

    # ---- Classify current market regime -----------------------------------
    # Uses the same nifty_df already fetched for Relative Strength — no extra
    # network call. Regime is a tag on every signal from this scan and is used
    # by Sprint 2 to gate engines (e.g. disable UT Bot in chop).
    regime_info = classify_regime(nifty_df, config)
    current_regime = regime_info["regime"]
    log.info(
        "  Market Regime : %s  (ADX=%s  +DI=%s  -DI=%s  vol_pct=%s)",
        current_regime,
        regime_info["adx"], regime_info["plus_di"],
        regime_info["minus_di"], regime_info["vol_pct"],
    )

    # ---- Pre-fetch HTF data for all symbols in parallel (only when enabled) ----
    # HTF (Higher Timeframe) confirmation is a HARD FILTER that rejects signals
    # when the higher timeframe trend opposes the signal direction.
    # Pre-fetching eliminates sequential fetches inside each worker thread.
    # 
    # Only runs when:
    #   • mtf_timeframe is set (e.g., "15m")
    #   • mtf_filter_enabled = true in config.yml
    filters_cfg = config.get("filters", {})
    htf_cache: dict = {}
    mtf_tf      = filters_cfg.get("mtf_timeframe", "15m")
    mtf_active  = (
        bool(mtf_tf) and 
        filters_cfg.get("mtf_filter_enabled", False)
    )
    if mtf_active:
        log.info("  Pre-fetching HTF (%s) data for %d symbols...", mtf_tf, len(symbols))
        with ThreadPoolExecutor(max_workers=10) as htf_executor:
            htf_futures = {
                htf_executor.submit(fetch_history, sym, mtf_tf, config): sym
                for sym in symbols
            }
            for f in as_completed(htf_futures):
                sym = htf_futures[f]
                try:
                    htf_cache[sym] = f.result()
                except Exception:
                    htf_cache[sym] = None

    buy_results  = []
    sell_results = []
    errors       = 0

    # ---- Log active hard filters -----------------------------------------------
    active_filters = []
    if filters_cfg.get("mtf_filter_enabled", False):
        active_filters.append(f"HTF ({filters_cfg.get('mtf_timeframe', '15m')})")
    if rs_enabled:
        rs_index_name = filters_cfg.get("rs_index", "NIFTY50")
        active_filters.append(f"Outperformers vs {rs_index_name}")
    if active_filters:
        log.info("  Hard Filters  : %s", " | ".join(active_filters))
    else:
        log.info("  Hard Filters  : None (all filters disabled)")

    log.info("  Scanning %d symbols on LTF (%s) ...", len(symbols), timeframe)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                scan_symbol, sym, timeframe, config, lookback,
                nifty_df, htf_cache.get(sym),
            ): sym
            for sym in symbols
        }

        for i, future in enumerate(as_completed(futures), 1):
            sym = futures[future]
            try:
                results = future.result()
                if results:
                    signals_str = ", ".join(
                        f"{r['signal']} ({'+'.join(r['triggered'])})" for r in results
                    )
                    log.debug("  [%3d/%3d] %-15s ✅ %s", i, len(symbols), sym, signals_str)
                    for r in results:
                        if r["signal"] == "BUY":
                            buy_results.append(r)
                        else:
                            sell_results.append(r)
                else:
                    log.debug("  [%3d/%3d] %-15s —", i, len(symbols), sym)
            except Exception as exc:
                errors += 1
                log.error("  [%3d/%3d] %-15s ❌ Error: %s", i, len(symbols), sym, exc)

    buy_results.sort(key=lambda r: (-r.get("setup_score", 0.0), r["symbol"]))
    sell_results.sort(key=lambda r: (-r.get("setup_score", 0.0), r["symbol"]))

    if errors:
        log.warning("  Scan completed with %d error(s).", errors)

    # ---- Auto Order Execution (when order_mode == 'auto') ----
    oa_cfg = config.get("openalgo", {})
    order_mode = str(oa_cfg.get("order_mode", "manual")).lower()
    allowed_actions = str(oa_cfg.get("allowed_actions", "BUY_ONLY")).upper()

    if order_mode == "auto":
        log.info("  🤖 AUTO ORDER MODE ACTIVE | Action Filter: %s", allowed_actions)
        import trading_adapter
        import trade_db
        import risk_limits
        from telegram import send_telegram_alert

        try:
            open_positions = trade_db.get_open_positions()
            open_syms = {p["symbol"] for p in open_positions}
        except Exception:
            open_positions = []
            open_syms = set()

        # ---- Sprint 4: Initialize circuit breaker for order failures ----
        _order_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=300)

        # Log risk-limits config once per scan so operators know it's active.
        rl_cfg = config.get("risk_limits", {}) or {}
        if rl_cfg.get("enabled", False):
            log.info(
                "  🛡  Risk limits ON | max_concurrent=%s max_per_sym=%s daily_loss=%s%% sizing=%s",
                rl_cfg.get("max_concurrent_positions", "∞"),
                rl_cfg.get("max_positions_per_symbol", "∞"),
                rl_cfg.get("daily_loss_stop_pct", "off"),
                rl_cfg.get("sizing_mode", "legacy"),
            )

        # Sprint-2: log regime-gate state once per scan.
        if regime_gate.is_gate_enabled(config):
            log.info(
                "  🚦 Regime gate ON | current_regime=%s (signals from disabled engines will be skipped)",
                current_regime,
            )
        else:
            log.debug("  Regime gate OFF — tagging only (current_regime=%s)", current_regime)

        for r in (buy_results + sell_results):
            sig = str(r.get("signal", "BUY")).upper()
            sym = r["symbol"]
            
            # ---- Sprint 4: Data freshness validation ----
            # Reject signals older than 2× scan_interval to avoid acting on stale data
            scan_interval = int(config.get("scan_interval_seconds", 60))
            max_age_seconds = scan_interval * 2
            
            try:
                signal_timestamp = r.get("timestamp")
                if signal_timestamp:
                    if isinstance(signal_timestamp, str):
                        signal_dt = datetime.fromisoformat(signal_timestamp.replace("Z", "+00:00"))
                    else:
                        signal_dt = signal_timestamp
                    
                    age_seconds = (datetime.now(ZoneInfo("Asia/Kolkata")) - signal_dt).total_seconds()
                    
                    if age_seconds > max_age_seconds:
                        log.warning(
                            "  [%s] ⚠️ Stale signal rejected (age: %.1fs, max: %ds) - skipping",
                            sym, age_seconds, max_age_seconds
                        )
                        continue
            except Exception as freshness_err:
                log.warning("  [%s] Could not validate signal freshness: %s", sym, freshness_err)
                # Don't reject on validation error - continue

            if allowed_actions == "BUY_ONLY" and sig != "BUY":
                log.info("  [%s] Skipped auto order for %s signal (openalgo.allowed_actions = BUY_ONLY)", sym, sig)
                continue
            elif allowed_actions == "SELL_ONLY" and sig != "SELL":
                log.info("  [%s] Skipped auto order for %s signal (openalgo.allowed_actions = SELL_ONLY)", sym, sig)
                continue

            if sym in open_syms:
                log.info("  [%s] Skipped auto order: position already open in trade_db", sym)
                continue

            # ---- Sprint-2 Regime gate ----
            # Derive engine tag from the ``triggered`` list. A composite
            # signal (UT + S/R both triggered) is treated as UT-Bot for
            # gating purposes because UT is the trend-following leg; if
            # UT is disabled in the current regime we should block the
            # composite too.
            triggered_list = r.get("triggered", []) or []
            triggered_upper = " ".join(str(t).upper() for t in triggered_list)
            if "UT" in triggered_upper:
                engine_tag = "utbot"
            elif "S/R" in triggered_upper or "SR" in triggered_upper:
                engine_tag = "sr"
            else:
                engine_tag = "unknown"
            r["engine"] = engine_tag  # journal-friendly

            ok_gate, gate_reason = regime_gate.check_signal_allowed(
                engine_tag, current_regime, config,
            )
            if not ok_gate:
                log.info("  [%s] Skipped auto order — %s", sym, gate_reason)
                r["regime_gate_ok"] = False
                r["regime_gate_reason"] = gate_reason
                continue
            r["regime_gate_ok"] = True

            # ---- Sprint-3 Min-grade gate ----
            # Runs after the regime gate (cheap, no DB) and before the
            # risk-limits gate (which hits trade_db). Default
            # min_grade_to_trade="D" makes this a no-op until an operator
            # tightens it, so Sprint 3 ships observe-only.
            sig_grade = r.get("grade")
            ok_grade, grade_reason = signal_grader.meets_min_grade(sig_grade, config)
            if not ok_grade:
                log.info("  [%s] Skipped auto order — %s", sym, grade_reason)
                r["grade_gate_ok"] = False
                r["grade_gate_reason"] = grade_reason
                continue
            r["grade_gate_ok"] = True

            # ---- Risk-limits gate ----
            # Re-uses the cached ``open_positions`` list so all candidates in
            # this scan share the same "current state" snapshot rather than
            # racing against each other's DB writes.
            ok, why = risk_limits.check_can_open_new(sym, config, open_positions)
            if not ok:
                log.info("  [%s] Skipped auto order — risk gate: %s", sym, why)
                continue

            close_price = float(r.get("close") or 0.0)
            stop_loss   = float(r.get("stop_loss") or close_price * 0.99)
            target_price = float(r.get("target")   or close_price * 1.02)

            # ---- Sprint-2 sizing ----
            # When risk_limits.sizing_mode is set (risk_based / capital_pct),
            # use the new sizer. Otherwise fall back to legacy compute_quantity
            # so existing configs keep working unchanged.
            sizing_mode_cfg = str((config.get("risk_limits", {}) or {}).get("sizing_mode", "legacy")).lower()
            if sizing_mode_cfg in ("risk_based", "capital_pct"):
                sizing = risk_limits.compute_quantity_risk_based(
                    entry_price=close_price,
                    stop_loss=stop_loss,
                    config=config,
                    fallback_qty=1,
                    grade=sig_grade,
                )
                quantity = int(sizing.get("quantity", 0) or 0)
                r["position_sizing"] = sizing
                if quantity < 1:
                    log.info(
                        "  [%s] Skipped auto order — sizer returned 0 qty (%s)",
                        sym, sizing.get("reason", "?"),
                    )
                    continue
                log.info(
                    "  [%s] 📏 Sized qty=%d | risk=₹%.0f (%.2f%% of ₹%.0f) | mode=%s | grade=%s ×%.2f",
                    sym, quantity, sizing.get("risk_amount", 0.0),
                    sizing.get("risk_pct", 0.0), sizing.get("capital", 0.0),
                    sizing.get("mode", "?"),
                    sizing.get("grade") or "—",
                    sizing.get("grade_multiplier", 1.0),
                )
            else:
                # Legacy path — capital-aware qty from openalgo.capital_per_trade.
                quantity = risk_limits.compute_quantity(close_price, config, fallback_qty=1)

            # ---- Sprint-3 Portfolio exposure cap ----
            # Checked AFTER sizing because the projected notional depends on the
            # final quantity (which the grade multiplier may have scaled up).
            # Re-uses the same cached ``open_positions`` snapshot as the
            # risk-limits gate so every candidate in this scan sees consistent
            # state. Disabled unless risk_limits.enabled AND
            # max_portfolio_exposure_pct are both set.
            ok_exp, exp_reason = risk_limits.check_portfolio_exposure(
                config,
                new_notional=quantity * close_price,
                open_positions=open_positions,
            )
            if not ok_exp:
                log.info("  [%s] Skipped auto order — %s", sym, exp_reason)
                r["exposure_gate_ok"] = False
                r["exposure_gate_reason"] = exp_reason
                continue
            r["exposure_gate_ok"] = True

            product = str(oa_cfg.get("order_product", "MIS"))
            price_type = str(oa_cfg.get("order_type", "MARKET"))
            exchange = str(config.get("exchange", "NSE"))

            req = SimpleNamespace(
                symbol=sym,
                exchange=exchange,
                action=sig,
                quantity=quantity,
                product=product,
                price_type=price_type,
                price=close_price,
                trigger_price=0.0,
                strategy="UTBot_SR_Stocks",
            )

            log.info("  [%s] Auto-executing %s order via trading adapter...", sym, sig)
            
            # ---- Sprint 4: Circuit breaker wrapping ----
            try:
                ord_res = _order_circuit_breaker.call(trading_adapter.place_order, config, req)
            except Exception as order_err:
                log.error("  [%s] Order failed: %s", sym, order_err)
                
                if _order_circuit_breaker.is_open:
                    log.critical("🚨 Circuit breaker OPEN - stopping auto-orders for this scan")
                    send_telegram_alert(
                        f"🚨 AUTO-ORDERS STOPPED\n"
                        f"3 consecutive order failures detected.\n"
                        f"Last error: {str(order_err)[:200]}\n"
                        f"Please check broker connection and API status.",
                        priority=10, config=config
                    )
                    break  # Exit the auto-order loop
                
                continue  # Skip this symbol, try next

            if ord_res.get("status") == "success":
                order_id = ord_res.get("orderid")
                
                # ---- Sprint 4: Verify filled quantity (handle partial fills) ----
                filled_qty = quantity  # Default to requested qty
                try:
                    # Wait 2 seconds for order execution
                    import time
                    time.sleep(2)
                    
                    # Query order status from broker
                    # For OpenAlgo:
                    from openalgo import api as oa_api
                    oa_cfg_check = config.get("openalgo", {})
                    oa_client = oa_api(api_key=oa_cfg_check["apikey"], host=oa_cfg_check.get("base_url", "http://127.0.0.1:5000"))
                    orderbook = oa_client.orderbook()
                    
                    # Find our order
                    our_order = next((o for o in orderbook if str(o.get("orderid")) == str(order_id)), None)
                    
                    if our_order:
                        status = str(our_order.get("status", "")).upper()
                        if status in ["COMPLETE", "FILLED"]:
                            filled_qty = int(our_order.get("filled_quantity", quantity))
                        elif status in ["PENDING", "OPEN", "TRIGGER_PENDING"]:
                            log.warning("  [%s] Order %s still pending (status: %s) - skipping position registration", sym, order_id, status)
                            continue
                    
                    if filled_qty < quantity:
                        log.warning("  [%s] ⚠️ Partial fill: requested %d, filled %d", sym, quantity, filled_qty)
                        send_telegram_alert(
                            f"⚠️ Partial Fill\n{sym}: {filled_qty}/{quantity} filled",
                            priority=7, config=config
                        )
                        
                except Exception as fill_check_err:
                    log.error("  [%s] Could not verify fill quantity: %s - using requested qty", sym, fill_check_err)
                    # Continue with requested quantity as fallback
                
                try:
                    pos_id = trade_db.open_position_db({
                        "order_id": order_id or f"AUTO_{int(datetime.now().timestamp()*1000)}",
                        "symbol": sym,
                        "exchange": exchange,
                        "direction": sig,
                        "quantity": filled_qty,  # ✅ Use actual filled quantity
                        "entry_price": close_price,
                        "current_sl": stop_loss,
                        "initial_sl": stop_loss,
                        "target_price": target_price,
                        "product": product,
                        "timeframe": timeframe,
                    })
                    open_syms.add(sym)
                    # Append to the in-memory snapshot so subsequent candidates
                    # in this same scan see the fresh position when the risk
                    # gate re-evaluates max_concurrent_positions.
                    open_positions.append({
                        "id": pos_id,
                        "symbol": sym,
                        "exchange": exchange,
                        "direction": sig,
                        "quantity": filled_qty,
                        "entry_price": close_price,
                        "current_sl": stop_loss,
                        "target_price": target_price,
                    })
                    log.info("  [%s] Registered auto position ID %d in trade_db", sym, pos_id)
                except Exception as db_err:
                    log.error("  [%s] Failed to record auto position in DB: %s", sym, db_err)

                tg_msg = (
                    f"🚀 <b>AUTO-ORDER EXECUTED (STOCKS)</b>\n"
                    f"Symbol: <b>{sym}</b>\n"
                    f"Action: <b>{sig}</b>\n"
                    f"Price: ₹{close_price:.2f}\n"
                    f"SL: ₹{stop_loss:.2f} | Target: ₹{target_price:.2f}\n"
                    f"Qty: {quantity}\n"
                )
                # Sprint-2: append risk-based sizing metrics when available.
                _ps = r.get("position_sizing") or {}
                if _ps.get("mode", "").startswith("risk_based"):
                    tg_msg += (
                        f"Risk: ₹{_ps.get('risk_amount', 0):.0f} "
                        f"({_ps.get('risk_pct', 0):.2f}% of ₹{_ps.get('capital', 0):.0f})\n"
                    )
                tg_msg += (
                    f"Regime: <b>{current_regime}</b> | "
                    f"Engine: {r.get('engine', '?')}\n"
                    f"Setup Score: {r.get('setup_score', 0.0):.1f}"
                )
                send_telegram_alert(tg_msg, config=config)

    return buy_results, sell_results, segment_label, timeframe, len(symbols), current_regime


# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def _format_conditions(triggered: list[str]) -> str:
    """Compact string of triggered condition names."""
    short = []
    for t in triggered:
        if "UT" in t:
            short.append("UT")
        elif "S/R" in t or "SR" in t.upper():
            short.append("SR")
        else:
            short.append(t)
    return "+".join(short) if short else "—"


def _format_zones(zones: list) -> str:
    """Format top SR zones for display."""
    if not zones:
        return "—"
    parts = []
    for hi, lo, *_ in zones[:2]:
        parts.append(f"{lo:.1f}–{hi:.1f}")
    return ", ".join(parts)


def print_results_table(
    buy_results: list[dict],
    sell_results: list[dict],
    segment_label: str,
    timeframe: str,
    total_stocks: int = 0,
):
    """Print formatted Buy/Sell result tables to the console/log."""
    total = total_stocks or (len(buy_results) + len(sell_results))

    if not buy_results and not sell_results:
        log.info("")
        log.info("=" * 70)
        log.info("  📭 NO SIGNALS FOUND in %s (%s)", segment_label, timeframe)
        log.info("=" * 70)
        return

    def _print_signal_table(results, label, emoji):
        log.info("")
        log.info("=" * 70)
        log.info("  %s %s — %s Signals (%s)", emoji, segment_label, label, timeframe)
        log.info("=" * 70)
        log.info(
            "  %-4s  %-14s  %-9s  %-8s  %-10s  %-10s  %-5s  %-10s",
            "#", "SYMBOL", "CLOSE", "SCORE", "SL", "TARGET", "R:R", "CONDITIONS",
        )
        log.info("  " + "-" * 80)

        for i, r in enumerate(results, 1):
            conds = _format_conditions(r["triggered"])
            score = f"{r.get('setup_score', 0.0):.1f}"
            sl  = f"{r['stop_loss']:.2f}"  if r.get("stop_loss")  is not None else "—"
            tgt = f"{r['target']:.2f}"     if r.get("target")     is not None else "—"
            rr  = f"{r['risk_reward']:.1f}" if r.get("risk_reward") is not None else "—"
            log.info(
                "  %-4d  %-14s  %-9.2f  %-8s  %-10s  %-10s  %-5s  %-10s",
                i, r["symbol"], r["close"], score, sl, tgt, rr, conds,
            )

        log.info("  " + "-" * 80)
        log.info("  Total: %d %s signals", len(results), label)
        log.info("=" * 70)

    # ---- BUY table ----------------------------------------------------------
    if buy_results:
        _print_signal_table(buy_results, "BUY", "🟢")

    # ---- SELL table ---------------------------------------------------------
    if sell_results:
        _print_signal_table(sell_results, "SELL", "🔴")

    # ---- Summary ------------------------------------------------------------
    log.info("")
    log.info(
        "  Summary: %d BUY + %d SELL out of %d stocks scanned",
        len(buy_results), len(sell_results), total,
    )


def build_telegram_message(
    buy_results: list[dict],
    sell_results: list[dict],
    segment_label: str,
    timeframe: str,
    mode: str,
    total_stocks: int = 0,
) -> str:
    """Build a consolidated HTML Telegram message for buy and sell signals, sorted by Setup Score."""
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = total_stocks or (len(buy_results) + len(sell_results))

    def _esc(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if not buy_results and not sell_results:
        return (
            f"📬 <b>{_esc(segment_label)} — No Signals</b>\n"
            f"Mode: {_esc(mode)} | Timeframe: {timeframe}\n"
            f"Scanned at: {now}"
        )

    lines = [
        f"📊 <b>{_esc(segment_label)} — UTBot+SR Channels Scanner</b>",
        f"Mode: <code>{_esc(mode)}</code> | TF: <code>{timeframe}</code> | {now}",
        "",
    ]

    def _signal_line(i, r):
        score = r.get("setup_score", 0.0)
        prefix = "🔥 " if score >= 70 else ""

        # R:R info
        rr_str = ""
        if r.get("risk_reward") is not None:
            rr_emoji = "🎯" if r["risk_reward"] >= 2.0 else ""
            rr_str = f" | R:R {rr_emoji}{r['risk_reward']:.1f}"
            if r.get("stop_loss") is not None and r.get("target") is not None:
                rr_str += f" (SL: {r['stop_loss']:.0f} → TGT: {r['target']:.0f})"

        # MTF trend
        mtf_str = ""
        mtf = r.get("mtf")
        if mtf and mtf.get("trend"):
            trend_emoji = {"📈": "bullish", "📉": "bearish", "➖": "neutral"}
            for emoji, t in trend_emoji.items():
                if mtf["trend"] == t:
                    mtf_str = f" | HTF: {emoji}"
                    break

        reasons_list = r.get("score_reasons", [])
        brief_reason = f"\n   └ <i>{reasons_list[0]}</i>" if reasons_list else ""

        return (
            f"{i}. {prefix}<b>{_esc(r['symbol'])}</b> — ₹{r['close']:.2f}"
            f" (Score: <b>{score:.1f}</b>{rr_str}{mtf_str}){brief_reason}"
        )

    if buy_results:
        lines.append("🟢 <b>BUY Signals (Sorted by Setup Score)</b>")
        for i, r in enumerate(buy_results, 1):
            lines.append(_signal_line(i, r))
        lines.append("")

    if sell_results:
        lines.append("🔴 <b>SELL Signals (Sorted by Setup Score)</b>")
        for i, r in enumerate(sell_results, 1):
            lines.append(_signal_line(i, r))
        lines.append("")

    lines.append(
        f"<i>Total: {len(buy_results)} BUY + {len(sell_results)} SELL / {total} stocks</i>"
    )

    return "\n".join(lines)


# ============================================================================
# MARKET HOURS CHECK
# ============================================================================

def _is_market_hours(config: dict) -> bool:
    """Return True if the current time is within configured market hours."""
    bot_cfg = config.get("bot", {})

    if not bot_cfg.get("market_hours_check", False):
        return True  # Always scan if check is disabled

    open_str  = bot_cfg.get("market_open",  "09:15")
    close_str = bot_cfg.get("market_close", "15:30")

    exchange = config.get("exchange", "NSE")
    tz = ZoneInfo("Asia/Kolkata") if exchange.upper() in ("NSE", "BSE") else ZoneInfo("UTC")
    now = datetime.now(tz).replace(tzinfo=None)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    today        = now.date()
    market_open  = datetime.strptime(f"{today} {open_str}",  "%Y-%m-%d %H:%M")
    market_close = datetime.strptime(f"{today} {close_str}", "%Y-%m-%d %H:%M")

    return market_open <= now <= market_close


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="UTBot + SR Channels Scanner for NSE Indices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scanner.py --once                          # Single scan, default config
  python scanner.py --once --segment BANKNIFTY      # Scan Bank Nifty
  python scanner.py --once --tf 5m --mode UTBot     # UTBot-only on 5-min chart
  python scanner.py --segment NIFTY200 --tf 1h      # Continuous, 1-hour bars
  python scanner.py --list-segments                 # Show available segments
        """,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit (no continuous loop)",
    )
    parser.add_argument(
        "--tf",
        type=str,
        default=None,
        metavar="TIMEFRAME",
        help="Override scan timeframe (e.g. 1m, 5m, 15m, 1h, 1d)",
    )
    parser.add_argument(
        "--segment",
        type=str,
        default=None,
        metavar="SEGMENT",
        help="Index segment to scan (e.g. NIFTY50, BANKNIFTY, NIFTYIT)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        metavar="MODE",
        choices=["UTBot", "SR", "UTBot+SR"],
        help="Signal mode override: UTBot | SR | UTBot+SR",
    )
    parser.add_argument(
        "--list-segments",
        action="store_true",
        help="List all available segment names and exit",
    )
    args = parser.parse_args()

    # Handle --list-segments
    if args.list_segments:
        segments = list_available_segments()
        print("\nAvailable segments:")
        for s in segments:
            print(f"  • {s}")
        print(f"\nTotal: {len(segments)} segments")
        print("\nUsage: python scanner.py --segment BANKNIFTY --once")
        return

    config = load_config()

    # Apply log level from config to our custom logger and console handler
    log_level_str = config.get("bot", {}).get("log_level", "INFO").upper()
    log_level     = getattr(logging, log_level_str, logging.INFO)
    log.setLevel(log_level)
    for h in log.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(log_level)

    timeframe     = args.tf or config.get("candle_timeframe", config.get("scan_timeframe", "5m"))
    segment       = args.segment  # None → use config value
    mode_override = args.mode     # None → use config value
    scan_interval = int(config.get("scan_interval_seconds", 300))
    
    if mode_override:
        eff_mode = mode_override
    else:
        # Determine effective mode dynamically from ENGINE_REGISTRY
        from signals import ENGINE_REGISTRY
        
        active_labels = []
        for engine in ENGINE_REGISTRY:
            cfg_section = config.get(engine["config_section"], {})
            is_enabled = cfg_section.get(engine["enabled_key"], True)
            if is_enabled:
                active_labels.append(engine["label"])
        
        if len(active_labels) == 0:
            eff_mode = "None"
        elif len(active_labels) == 1:
            eff_mode = f"{active_labels[0]} Only"
        else:
            eff_mode = " + ".join(active_labels)
            eff_mode = "None"

    def _do_scan() -> tuple[list, list, str, str, int, str]:
        return run_scan(
            config,
            timeframe_override=timeframe,
            segment_override=segment,
            mode_override=mode_override,
        )

    if args.once:
        # ── Single scan mode ─────────────────────────────────────────────
        if not _is_market_hours(config):
            log.info("Outside market hours — running scan anyway (--once mode).")

        buy_results, sell_results, seg_label, tf, total, market_regime = _do_scan()

        print_results_table(buy_results, sell_results, seg_label, tf, total)

        msg       = build_telegram_message(buy_results, sell_results, seg_label, tf, eff_mode, total)
        
        # Determine if any signal is priority
        filters_cfg = config.get("filters", {})
        min_alert_score = filters_cfg.get("min_alert_score", 70)
        has_priority = any(r.get("setup_score", 0.0) >= min_alert_score for r in buy_results + sell_results)
        
        tg_result = send_telegram_alert(msg, priority=8, silent=not has_priority, config=config)
        if "error" in tg_result:
            log.warning("Telegram alert failed: %s", tg_result["error"])
        else:
            if has_priority:
                log.info("✅ Priority Telegram alert sent successfully (score >= %d).", min_alert_score)
            else:
                log.info("✅ Silent Telegram alert sent successfully (score < %d).", min_alert_score)

        # Log signals to history database in batch
        if config.get("filters", {}).get("signal_history_enabled", True):
            try:
                log_signals_batch(buy_results + sell_results, timeframe=tf,
                                  config=config, regime=market_regime)
            except Exception as e:
                log.debug("Signal history log failed: %s", e)

    else:
        # ── Continuous scanning mode ──────────────────────────────────────
        log.info("Starting continuous scan mode (interval: %ds).", scan_interval)
        log.info("Press Ctrl+C to stop.\n")

        last_scan_boundary = None

        try:
            while True:
                if _is_market_hours(config):
                    # Calculate current candle boundary to avoid duplicate scans
                    try:
                        candle_secs = int(_parse_timeframe(timeframe).total_seconds())
                        epoch_secs  = int(datetime.now().timestamp())
                        boundary    = (epoch_secs // candle_secs) * candle_secs
                    except ValueError:
                        boundary = None

                    if boundary != last_scan_boundary:
                        last_scan_boundary = boundary

                        buy_results, sell_results, seg_label, tf, total, market_regime = _do_scan()

                        print_results_table(buy_results, sell_results, seg_label, tf, total)

                        # Send Telegram only when signals are found
                        if buy_results or sell_results:
                            msg       = build_telegram_message(
                                buy_results, sell_results, seg_label, tf, eff_mode, total,
                            )
                            # Determine if any signal is priority
                            filters_cfg = config.get("filters", {})
                            min_alert_score = filters_cfg.get("min_alert_score", 70)
                            has_priority = any(r.get("setup_score", 0.0) >= min_alert_score for r in buy_results + sell_results)
                            
                            tg_result = send_telegram_alert(msg, priority=8, silent=not has_priority, config=config)
                            if "error" in tg_result:
                                log.warning("Telegram alert failed: %s", tg_result["error"])
                            else:
                                if has_priority:
                                    log.info("✅ Priority Telegram alert sent successfully (score >= %d).", min_alert_score)
                                else:
                                    log.info("✅ Silent Telegram alert sent successfully (score < %d).", min_alert_score)

                            # Log signals to history database in batch
                            if config.get("filters", {}).get("signal_history_enabled", True):
                                try:
                                    log_signals_batch(buy_results + sell_results, timeframe=tf,
                                                      config=config, regime=market_regime)
                                except Exception as e:
                                    log.debug("Signal history log failed: %s", e)
                        else:
                            log.info("No signals — skipping Telegram alert.")

                        # Periodic outcome check (every scan cycle)
                        if config.get("filters", {}).get("signal_history_enabled", True):
                            try:
                                outcome_hours = config.get("filters", {}).get("outcome_check_hours", 4)
                                check_outcomes(hours=outcome_hours, config=config, fetch_fn=fetch_history)
                            except Exception as e:
                                log.debug("Outcome check failed: %s", e)
                    else:
                        log.debug("Same candle boundary — waiting for next bar...")
                else:
                    log.debug("Outside market hours — sleeping...")

                # Dynamic sleep to align with candle boundaries
                try:
                    candle_secs = int(_parse_timeframe(timeframe).total_seconds())
                    now = datetime.now()
                    time_passed = now.timestamp() % candle_secs
                    time_remaining = candle_secs - time_passed
                    
                    # Sleep until boundary + 0.5s safety buffer
                    sleep_time = min(scan_interval, time_remaining + 0.5)
                    if sleep_time < 2.0:  # If we are too close, sleep for the next interval or a buffer
                        sleep_time += candle_secs
                    log.info("Sleeping for %.2f seconds until next candle boundary...", sleep_time)
                    time.sleep(sleep_time)
                except Exception:
                    time.sleep(scan_interval)

        except KeyboardInterrupt:
            log.info("\nScanner stopped. Goodbye!")


if __name__ == "__main__":
    main()
