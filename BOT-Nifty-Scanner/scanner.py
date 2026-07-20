"""
===============================================================================
  BOT-Nifty50-Scanner — UT Bot Signal Screener for Nifty 50
===============================================================================

Scans all Nifty 50 stocks for UT Bot BUY signals on a configurable timeframe.
Stocks with a buy signal on the current candle or up to N candles back are
filtered and displayed in a summary table + sent as a Telegram alert.

Usage:
    python scanner.py              # Continuous scanning (re-scans every interval)
    python scanner.py --once       # Single scan and exit
    python scanner.py --tf 5m      # Override timeframe from CLI

Stop:
    Ctrl+C
===============================================================================
"""

import sys
import os
import argparse
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Ensure UTF-8 output on Windows so emojis / box-drawing chars don't crash
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    import io
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_bot_dir = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            _bot_dir / "scanner.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("Nifty50Scanner")

# ---------------------------------------------------------------------------
# Import Telegram notifier & NSE indices
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert  # noqa: E402
from nse_indices import get_index_symbols, list_available_segments  # noqa: E402


# ============================================================================
# CONFIG
# ============================================================================

def load_config(path: Path | str = None) -> dict:
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ============================================================================
# UT BOT SIGNAL ENGINE (same as BOT-Antigravity)
# ============================================================================

def compute_utbot_signals(
    df: pd.DataFrame,
    key_value: float = 2.0,
    atr_period: int = 1,
    use_heikin_ashi: bool = False,
) -> pd.DataFrame:
    """
    Compute UT Bot ATR Trailing Stop signals.

    Returns DataFrame with additional columns:
        atr, nLoss, xATRTrailingStop, pos, buy, sell
    """
    df = df.copy()

    # ---- Source price -------------------------------------------------------
    if use_heikin_ashi:
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        src = ha_close
    else:
        src = df["close"]

    # ---- True Range / ATR ---------------------------------------------------
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # ATR as Wilder's RMA — Pine `ta.atr` uses RMA by default
    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    n_loss = key_value * atr

    # ---- xATRTrailingStop ---------------------------------------------------
    src_vals = src.values
    nl_vals = n_loss.values
    n = len(src_vals)
    stop = np.zeros(n)

    for i in range(1, n):
        prev_stop = stop[i - 1]
        prev_src = src_vals[i - 1]
        cur_src = src_vals[i]
        cur_nl = nl_vals[i]

        if np.isnan(cur_nl):
            stop[i] = prev_stop
            continue

        if cur_src > prev_stop and prev_src > prev_stop:
            stop[i] = max(prev_stop, cur_src - cur_nl)
        elif cur_src < prev_stop and prev_src < prev_stop:
            stop[i] = min(prev_stop, cur_src + cur_nl)
        elif cur_src > prev_stop:
            stop[i] = cur_src - cur_nl
        else:
            stop[i] = cur_src + cur_nl

    xATR = pd.Series(stop, index=df.index)

    # ---- Position -----------------------------------------------------------
    pos = np.zeros(n, dtype=int)
    src_arr = src.values
    for i in range(1, n):
        prev_pos = pos[i - 1]
        if src_arr[i - 1] < stop[i - 1] and src_arr[i] > stop[i]:
            pos[i] = 1
        elif src_arr[i - 1] > stop[i - 1] and src_arr[i] < stop[i]:
            pos[i] = -1
        else:
            pos[i] = prev_pos

    pos_series = pd.Series(pos, index=df.index)

    # ---- EMA(1) ≡ close ----------------------------------------------------
    ema = src

    # ---- Crossover helpers --------------------------------------------------
    def crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
        return (s1 > s2) & (s1.shift(1) <= s2.shift(1))

    above = crossover(ema, xATR)
    below = crossover(xATR, ema)

    # ---- Final signals ------------------------------------------------------
    df["atr"] = atr
    df["nLoss"] = n_loss
    df["xATRTrailingStop"] = xATR
    df["pos"] = pos_series
    df["src"] = src
    df["buy"] = (src > xATR) & above
    df["sell"] = (src < xATR) & below

    return df


# ============================================================================
# DATA FETCHING
# ============================================================================

def _parse_timeframe(tf: str) -> timedelta:
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
    """Fetch OHLCV candles for a single symbol."""
    data_source = config.get("data_source", "yfinance").lower()
    exchange = config.get("exchange", "NSE")
    lookback_days = int(config.get("data", {}).get("lookback_days", 5))

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        if data_source == "yfinance":
            import yfinance as yf
            yf_symbol = symbol
            if exchange == "NSE":
                yf_symbol = f"{symbol}.NS"
            elif exchange == "BSE":
                yf_symbol = f"{symbol}.BO"

            df = yf.download(
                tickers=yf_symbol,
                start=start_dt,
                end=end_dt,
                interval=timeframe,
                progress=False,
            )

            if df.empty:
                log.warning("[%s] No data from yfinance.", symbol)
                return None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        elif data_source == "tvdatafeed":
            from tvDatafeed import TvDatafeed, Interval
            tv_cfg = config.get("tvdatafeed", {})
            username = tv_cfg.get("username", "")
            password = tv_cfg.get("password", "")

            if username and password:
                tv = TvDatafeed(username=username, password=password)
            else:
                tv = TvDatafeed()

            interval_map = {
                "1m": Interval.in_1_minute,
                "3m": Interval.in_3_minute,
                "5m": Interval.in_5_minute,
                "15m": Interval.in_15_minute,
                "30m": Interval.in_30_minute,
                "45m": Interval.in_45_minute,
                "1h": Interval.in_1_hour,
                "2h": Interval.in_2_hour,
                "3h": Interval.in_3_hour,
                "4h": Interval.in_4_hour,
                "1d": Interval.in_daily,
                "1W": Interval.in_weekly,
                "1M": Interval.in_monthly,
            }
            tv_interval = interval_map.get(timeframe, Interval.in_5_minute)
            candle_dur = _parse_timeframe(timeframe)
            tf_minutes = int(candle_dur.total_seconds() / 60)
            bars_per_day = (24 * 60) // tf_minutes if tf_minutes > 0 else 1
            n_bars = min(5000, lookback_days * bars_per_day)

            df = tv.get_hist(
                symbol=symbol,
                exchange=exchange,
                interval=tv_interval,
                n_bars=n_bars,
            )

            if df is None or df.empty:
                log.warning("[%s] No data from tvdatafeed.", symbol)
                return None

            df.columns = [c.lower() for c in df.columns]
            if "symbol" in df.columns:
                df = df.drop(columns=["symbol"])
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        elif data_source == "twelvedata":
            from twelvedata import TDClient
            td_cfg = config.get("twelvedata", {})
            apikey = td_cfg.get("apikey", "")

            if not apikey:
                log.error("[%s] TwelveData API key missing.", symbol)
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

            df = df.iloc[::-1]
            df.columns = [c.lower() for c in df.columns]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        elif data_source == "openalgo":
            from openalgo import api
            oa_cfg = config.get("openalgo", {})
            client = api(
                api_key=oa_cfg.get("apikey", ""),
                host=oa_cfg.get("base_url", "http://127.0.0.1:5000"),
            )

            raw = client.history(
                symbol=symbol,
                exchange=exchange,
                interval=timeframe,
                start_date=start_str,
                end_date=end_str,
            )

            if isinstance(raw, pd.DataFrame):
                df = raw
            elif isinstance(raw, dict):
                data = raw.get("data")
                if isinstance(data, pd.DataFrame):
                    df = data
                elif isinstance(data, list) and data:
                    df = pd.DataFrame(data)
                else:
                    log.warning("[%s] Unexpected history payload.", symbol)
                    return None
            else:
                log.warning("[%s] Unexpected history response type.", symbol)
                return None

            if df is None or df.empty:
                log.warning("[%s] No data from openalgo.", symbol)
                return None

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

        else:
            log.error("[%s] Unknown data source: %s", symbol, data_source)
            return None

        return df

    except Exception as exc:
        log.error("[%s] Fetch error: %s", symbol, exc)
        return None


# ============================================================================
# SCANNER ENGINE
# ============================================================================

def scan_symbol(
    symbol: str,
    timeframe: str,
    config: dict,
    lookback_candles: int = 3,
) -> dict | None:
    """
    Scan a single symbol for UT Bot buy signals.

    Returns a dict with signal info if a buy was found in the last
    `lookback_candles` candles, otherwise None.
    """
    strat = config.get("strategy", {})
    key_value = float(strat.get("key_value", 2))
    atr_period = int(strat.get("atr_period", 1))
    use_heikin_ashi = bool(strat.get("use_heikin_ashi", False))

    df = fetch_history(symbol, timeframe, config)
    if df is None or len(df) < atr_period + 5:
        return None

    df = compute_utbot_signals(
        df,
        key_value=key_value,
        atr_period=atr_period,
        use_heikin_ashi=use_heikin_ashi,
    )

    # Check last N candles for buy signals
    tail = df.tail(lookback_candles)
    buy_candles = tail[tail["buy"] == True]

    if buy_candles.empty:
        return None

    # Use the most recent buy signal
    latest_buy = buy_candles.iloc[-1]
    signal_ts = buy_candles.index[-1]
    candles_ago = len(df) - df.index.get_loc(signal_ts) - 1

    return {
        "symbol": symbol,
        "signal_time": signal_ts,
        "candles_ago": candles_ago,
        "close": float(latest_buy["close"]),
        "atr_stop": float(latest_buy["xATRTrailingStop"]),
        "atr": float(latest_buy["atr"]),
        "pos": int(latest_buy["pos"]),
    }


def run_scan(
    config: dict,
    timeframe_override: str = None,
    segment_override: str = None,
) -> tuple[list[dict], str, str]:
    """
    Scan symbols in parallel and return list of stocks with buy signals.

    Returns
    -------
    tuple of (results, segment_label, timeframe)
    """
    timeframe = timeframe_override or config.get("scan_timeframe", "15m")
    lookback = int(config.get("signal_lookback_candles", 3))

    # Resolve symbols: --segment > config.segment > config.symbols
    segment = segment_override or config.get("segment", "")
    if segment:
        segment_label = segment.upper()
        symbols = get_index_symbols(segment)
        if not symbols:
            log.warning("Could not fetch symbols for '%s'. Falling back to config symbols list.", segment)
            symbols = config.get("symbols", [])
            segment_label = "CONFIG"
    else:
        symbols = config.get("symbols", [])
        segment_label = "CONFIG"

    log.info("=" * 70)
    log.info("  UT Bot Buy Signal Screener — %s", segment_label)
    log.info("=" * 70)
    log.info("  Segment       : %s", segment_label)
    log.info("  Timeframe     : %s", timeframe)
    log.info("  Lookback      : %d candles (current + %d prior)", lookback, lookback - 1)
    log.info("  Symbols       : %d stocks", len(symbols))
    log.info("  Data Source   : %s", config.get("data_source", "yfinance").upper())
    log.info("  Scan Time     : %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 70)

    results = []
    errors = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scan_symbol, sym, timeframe, config, lookback): sym
            for sym in symbols
        }

        for i, future in enumerate(as_completed(futures), 1):
            sym = futures[future]
            try:
                result = future.result()
                status = "✅ BUY" if result else "—"
                log.info(
                    "  [%2d/%2d] %-15s %s",
                    i, len(symbols), sym, status,
                )
                if result:
                    results.append(result)
            except Exception as exc:
                errors += 1
                log.error("  [%2d/%2d] %-15s ❌ Error: %s", i, len(symbols), sym, exc)

    # Sort results by candles_ago (most recent first)
    results.sort(key=lambda r: r["candles_ago"])

    return results, segment_label, timeframe


# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def print_results_table(results: list[dict], segment_label: str, timeframe: str, total_stocks: int = 0):
    """Print a formatted table of scan results to console."""
    total = total_stocks or len(results)

    if not results:
        log.info("")
        log.info("=" * 70)
        log.info("  📭 NO BUY SIGNALS FOUND in %s", segment_label)
        log.info("=" * 70)
        return

    log.info("")
    log.info("=" * 70)
    log.info("  🟢 %s — UT Bot BUY Signals (%s)", segment_label, timeframe)
    log.info("=" * 70)
    log.info(
        "  %-4s  %-15s  %-10s  %-10s  %-10s  %-12s",
        "#", "SYMBOL", "CLOSE", "ATR STOP", "ATR", "SIGNAL BAR",
    )
    log.info("  " + "-" * 66)

    for i, r in enumerate(results, 1):
        ago_label = "current" if r["candles_ago"] == 0 else f"{r['candles_ago']} ago"
        log.info(
            "  %-4d  %-15s  %-10.2f  %-10.2f  %-10.2f  %-12s",
            i,
            r["symbol"],
            r["close"],
            r["atr_stop"],
            r["atr"],
            ago_label,
        )

    log.info("  " + "-" * 66)
    log.info("  Total: %d / %d stocks with BUY signals", len(results), total)
    log.info("=" * 70)


def build_telegram_message(results: list[dict], segment_label: str, timeframe: str, total_stocks: int = 0) -> str:
    """Build a consolidated Telegram message for all filtered stocks."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = total_stocks or len(results)

    if not results:
        return (
            f"📭 *{segment_label} Scanner — No Buy Signals*\n"
            f"Timeframe: {timeframe}\n"
            f"Scanned at: {now}"
        )

    lines = [
        f"🟢 *{segment_label} — UT Bot BUY Signals*",
        f"Timeframe: `{timeframe}` | Scanned: {now}",
        f"",
    ]

    for i, r in enumerate(results, 1):
        ago_label = "current" if r["candles_ago"] == 0 else f"{r['candles_ago']} candles ago"
        lines.append(
            f"{i}. *{r['symbol']}* — ₹{r['close']:.2f} "
            f"(Stop: {r['atr_stop']:.2f}) [{ago_label}]"
        )

    lines.append(f"")
    lines.append(f"_Total: {len(results)} / {total} stocks_")

    return "\n".join(lines)


# ============================================================================
# MARKET HOURS CHECK
# ============================================================================

def _is_market_hours(config: dict) -> bool:
    bot_cfg = config.get("bot", {})

    if not bot_cfg.get("market_hours_check", True):
        return True

    open_str = bot_cfg.get("market_open", "09:15")
    close_str = bot_cfg.get("market_close", "15:30")

    now = datetime.now()

    # Reject weekends
    if now.weekday() >= 5:
        return False

    today = now.date()
    market_open = datetime.strptime(f"{today} {open_str}", "%Y-%m-%d %H:%M")
    market_close = datetime.strptime(f"{today} {close_str}", "%Y-%m-%d %H:%M")

    return market_open <= now <= market_close


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="UT Bot Buy Signal Scanner for NSE Indices",
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
        help="Override scan timeframe (e.g. 5m, 15m, 1h, 1d)",
    )
    parser.add_argument(
        "--segment",
        type=str,
        default=None,
        help="Index segment to scan (e.g. NIFTY50, BANKNIFTY, NIFTY200, NIFTYIT)",
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

    # Apply log level
    log_level_str = config.get("bot", {}).get("log_level", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.getLogger().setLevel(log_level)

    timeframe = args.tf or config.get("scan_timeframe", "15m")
    segment = args.segment  # None means use config
    scan_interval = int(config.get("scan_interval_seconds", 300))

    if args.once:
        # ── Single scan mode ──────────────────────────────────────────────
        if not _is_market_hours(config):
            log.info("Outside market hours. Running scan anyway (--once mode).")

        results, seg_label, tf = run_scan(config, timeframe_override=timeframe, segment_override=segment)
        # Count total symbols scanned (resolve again for display)
        total = len(config.get("symbols", [])) if not (segment or config.get("segment")) else len(results) or 1
        # For accurate total, re-resolve
        _seg = segment or config.get("segment", "")
        if _seg:
            _total_syms = get_index_symbols(_seg)
            total = len(_total_syms) if _total_syms else total
        else:
            total = len(config.get("symbols", []))

        print_results_table(results, seg_label, tf, total)

        # Send Telegram alert
        msg = build_telegram_message(results, seg_label, tf, total)
        tg_result = send_telegram_alert(msg, priority=8)
        if "error" in tg_result:
            log.warning("Telegram alert failed: %s", tg_result["error"])
        else:
            log.info("✅ Telegram alert sent successfully.")

    else:
        # ── Continuous scanning mode ──────────────────────────────────────
        log.info("Starting continuous scan mode (interval: %ds)", scan_interval)
        log.info("Press Ctrl+C to stop.\n")

        last_scan_boundary = None

        try:
            while True:
                if _is_market_hours(config):
                    # Calculate current candle boundary to avoid redundant scans
                    try:
                        candle_secs = int(_parse_timeframe(timeframe).total_seconds())
                        epoch_secs = int(datetime.now().timestamp())
                        boundary = (epoch_secs // candle_secs) * candle_secs
                    except ValueError:
                        boundary = None

                    if boundary != last_scan_boundary:
                        last_scan_boundary = boundary

                        results, seg_label, tf = run_scan(config, timeframe_override=timeframe, segment_override=segment)
                        _seg = segment or config.get("segment", "")
                        if _seg:
                            _total_syms = get_index_symbols(_seg)
                            total = len(_total_syms) if _total_syms else len(results)
                        else:
                            total = len(config.get("symbols", []))

                        print_results_table(results, seg_label, tf, total)

                        # Send Telegram alert only if there are results
                        if results:
                            msg = build_telegram_message(results, seg_label, tf, total)
                            tg_result = send_telegram_alert(msg, priority=8)
                            if "error" in tg_result:
                                log.warning("Telegram alert failed: %s", tg_result["error"])
                            else:
                                log.info("✅ Telegram alert sent successfully.")
                        else:
                            log.info("No buy signals — skipping Telegram alert.")
                    else:
                        log.debug("Same candle boundary, waiting for next bar...")
                else:
                    log.debug("Outside market hours, sleeping...")

                time.sleep(scan_interval)

        except KeyboardInterrupt:
            log.info("\nScanner stopped. Bye!")


if __name__ == "__main__":
    main()
