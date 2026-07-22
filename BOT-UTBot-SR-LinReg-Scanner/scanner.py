"""
===============================================================================
  BOT-UTBot-SR-LinReg-Scanner — Multi-Signal Screener
===============================================================================

Scans NSE index stocks for composite Buy/Sell signals combining:
  1. UT Bot (ATR trailing stop crossover)
  2. S/R Channels (support/resistance zone proximity)
  3. LinReg Candles (price vs linear regression signal line)

Each condition can be enabled/disabled independently. Conditions are combined
using AND or OR mode as configured.

Usage:
    python scanner.py              # Continuous scanning (re-scans every interval)
    python scanner.py --once       # Single scan and exit
    python scanner.py --tf 5m      # Override timeframe from CLI
    python scanner.py --segment BANKNIFTY --once

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
log = logging.getLogger("UTBotSRLinRegScanner")

# ---------------------------------------------------------------------------
# Import local modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert          # noqa: E402
from nse_indices import get_index_symbols, list_available_segments  # noqa: E402
from signals import (                              # noqa: E402
    compute_utbot_signals,
    compute_sr_signals,
    compute_linreg_signals,
    evaluate_composite_signals,
)


# ============================================================================
# CONFIG
# ============================================================================

def load_config(path: Path | str = None) -> dict:
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ============================================================================
# DATA FETCHING  (mirrors BOT-Nifty-Scanner)
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
) -> list[dict]:
    """
    Scan a single symbol through all three signal engines.

    Returns a list of result dicts (0, 1, or 2 — one per signal direction).
    Each dict contains signal metadata and which conditions triggered.
    """
    strat = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    lr_cfg = config.get("linreg", {})

    df = fetch_history(symbol, timeframe, config)
    if df is None or len(df) < 20:
        return []

    # ---- Run enabled signal engines ----------------------------------------
    if strat.get("ut_enabled", True):
        df = compute_utbot_signals(
            df,
            key_value=float(strat.get("key_value", 2)),
            atr_period=int(strat.get("atr_period", 1)),
            use_heikin_ashi=bool(strat.get("use_heikin_ashi", False)),
        )

    if sr_cfg.get("enabled", True):
        df = compute_sr_signals(
            df,
            pivot_period=int(sr_cfg.get("pivot_period", 10)),
            source=sr_cfg.get("source", "High/Low"),
            channel_width_pct=int(sr_cfg.get("channel_width_pct", 5)),
            min_strength=int(sr_cfg.get("min_strength", 1)),
            max_num_sr=int(sr_cfg.get("max_num_sr", 6)),
            loopback=int(sr_cfg.get("loopback", 290)),
            proximity_pct=float(sr_cfg.get("proximity_pct", 0.5)),
        )

    if lr_cfg.get("enabled", True):
        df = compute_linreg_signals(
            df,
            length=int(lr_cfg.get("length", 11)),
            signal_length=int(lr_cfg.get("signal_length", 7)),
            use_sma=bool(lr_cfg.get("use_sma", True)),
        )

    # ---- Evaluate composite signals ----------------------------------------
    composite = evaluate_composite_signals(df, config, lookback_candles)

    results = []
    last_row = df.iloc[-1]
    close_price = float(last_row["close"])

    base_info = {
        "symbol": symbol,
        "close": close_price,
        "signal_time": df.index[-1],
        "ut_trail": composite["details"].get("ut_trail"),
        "lr_signal": composite["details"].get("lr_signal"),
        "sr_zones": composite["details"].get("sr_zones", []),
    }

    if composite["buy"]:
        results.append({
            **base_info,
            "signal": "BUY",
            "triggered": composite["triggered_buy"],
        })

    if composite["sell"]:
        results.append({
            **base_info,
            "signal": "SELL",
            "triggered": composite["triggered_sell"],
        })

    return results


def run_scan(
    config: dict,
    timeframe_override: str = None,
    segment_override: str = None,
) -> tuple[list[dict], list[dict], str, str]:
    """
    Scan symbols in parallel and return buy/sell results.

    Returns
    -------
    tuple of (buy_results, sell_results, segment_label, timeframe)
    """
    timeframe = timeframe_override or config.get("scan_timeframe", "15m")
    lookback = int(config.get("signal_lookback_candles", 3))
    mode = config.get("signal_mode", "AND").upper()

    strat = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})
    lr_cfg = config.get("linreg", {})

    # Resolve symbols
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

    # Build engine status line
    engines = []
    if strat.get("ut_enabled", True):
        engines.append("UT Bot")
    if sr_cfg.get("enabled", True):
        engines.append("S/R Channels")
    if lr_cfg.get("enabled", True):
        engines.append("LinReg")

    log.info("=" * 70)
    log.info("  UTBot+SR+LinReg Multi-Signal Scanner — %s", segment_label)
    log.info("=" * 70)
    log.info("  Segment       : %s", segment_label)
    log.info("  Timeframe     : %s", timeframe)
    log.info("  Lookback      : %d candles (current + %d prior)", lookback, lookback - 1)
    log.info("  Engines       : %s", " + ".join(engines) if engines else "NONE")
    log.info("  Signal Mode   : %s", mode)
    log.info("  Symbols       : %d stocks", len(symbols))
    log.info("  Data Source   : %s", config.get("data_source", "yfinance").upper())
    log.info("  Scan Time     : %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 70)

    buy_results = []
    sell_results = []
    errors = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scan_symbol, sym, timeframe, config, lookback): sym
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
                    log.info("  [%2d/%2d] %-15s ✅ %s", i, len(symbols), sym, signals_str)
                    for r in results:
                        if r["signal"] == "BUY":
                            buy_results.append(r)
                        else:
                            sell_results.append(r)
                else:
                    log.info("  [%2d/%2d] %-15s —", i, len(symbols), sym)
            except Exception as exc:
                errors += 1
                log.error("  [%2d/%2d] %-15s ❌ Error: %s", i, len(symbols), sym, exc)

    # Sort by symbol name
    buy_results.sort(key=lambda r: r["symbol"])
    sell_results.sort(key=lambda r: r["symbol"])

    return buy_results, sell_results, segment_label, timeframe


# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def _format_conditions(triggered: list[str]) -> str:
    """Compact string of triggered condition names."""
    short = []
    for t in triggered:
        if "UT" in t:
            short.append("UT")
        elif "S/R" in t:
            short.append("SR")
        elif "LinReg" in t:
            short.append("LR")
        else:
            short.append(t)
    return "+".join(short)


def print_results_table(
    buy_results: list[dict],
    sell_results: list[dict],
    segment_label: str,
    timeframe: str,
    total_stocks: int = 0,
):
    """Print formatted tables of buy and sell results to console."""
    total = total_stocks or (len(buy_results) + len(sell_results))

    if not buy_results and not sell_results:
        log.info("")
        log.info("=" * 70)
        log.info("  📭 NO SIGNALS FOUND in %s (%s)", segment_label, timeframe)
        log.info("=" * 70)
        return

    # ---- BUY table ----------------------------------------------------------
    if buy_results:
        log.info("")
        log.info("=" * 70)
        log.info("  🟢 %s — BUY Signals (%s)", segment_label, timeframe)
        log.info("=" * 70)
        log.info(
            "  %-4s  %-15s  %-10s  %-10s  %-10s  %-12s",
            "#", "SYMBOL", "CLOSE", "UT TRAIL", "LR SIGNAL", "CONDITIONS",
        )
        log.info("  " + "-" * 66)

        for i, r in enumerate(buy_results, 1):
            ut_trail = f"{r['ut_trail']:.2f}" if r.get("ut_trail") is not None else "—"
            lr_sig = f"{r['lr_signal']:.2f}" if r.get("lr_signal") is not None else "—"
            conds = _format_conditions(r["triggered"])
            log.info(
                "  %-4d  %-15s  %-10.2f  %-10s  %-10s  %-12s",
                i, r["symbol"], r["close"], ut_trail, lr_sig, conds,
            )

        log.info("  " + "-" * 66)
        log.info("  Total: %d BUY signals", len(buy_results))
        log.info("=" * 70)

    # ---- SELL table ---------------------------------------------------------
    if sell_results:
        log.info("")
        log.info("=" * 70)
        log.info("  🔴 %s — SELL Signals (%s)", segment_label, timeframe)
        log.info("=" * 70)
        log.info(
            "  %-4s  %-15s  %-10s  %-10s  %-10s  %-12s",
            "#", "SYMBOL", "CLOSE", "UT TRAIL", "LR SIGNAL", "CONDITIONS",
        )
        log.info("  " + "-" * 66)

        for i, r in enumerate(sell_results, 1):
            ut_trail = f"{r['ut_trail']:.2f}" if r.get("ut_trail") is not None else "—"
            lr_sig = f"{r['lr_signal']:.2f}" if r.get("lr_signal") is not None else "—"
            conds = _format_conditions(r["triggered"])
            log.info(
                "  %-4d  %-15s  %-10.2f  %-10s  %-10s  %-12s",
                i, r["symbol"], r["close"], ut_trail, lr_sig, conds,
            )

        log.info("  " + "-" * 66)
        log.info("  Total: %d SELL signals", len(sell_results))
        log.info("=" * 70)

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
    total_stocks: int = 0,
) -> str:
    """Build a consolidated Telegram message for buy and sell signals."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = total_stocks or (len(buy_results) + len(sell_results))

    if not buy_results and not sell_results:
        return (
            f"📭 *{segment_label} Scanner — No Signals*\n"
            f"Timeframe: {timeframe}\n"
            f"Scanned at: {now}"
        )

    lines = [
        f"📊 *{segment_label} — Multi-Signal Scanner*",
        f"Timeframe: `{timeframe}` | Scanned: {now}",
        "",
    ]

    if buy_results:
        lines.append("🟢 *BUY Signals*")
        for i, r in enumerate(buy_results, 1):
            conds = _format_conditions(r["triggered"])
            trail_str = f" | Trail: {r['ut_trail']:.2f}" if r.get("ut_trail") is not None else ""
            lines.append(
                f"{i}. *{r['symbol']}* — ₹{r['close']:.2f} [{conds}]{trail_str}"
            )
        lines.append("")

    if sell_results:
        lines.append("🔴 *SELL Signals*")
        for i, r in enumerate(sell_results, 1):
            conds = _format_conditions(r["triggered"])
            trail_str = f" | Trail: {r['ut_trail']:.2f}" if r.get("ut_trail") is not None else ""
            lines.append(
                f"{i}. *{r['symbol']}* — ₹{r['close']:.2f} [{conds}]{trail_str}"
            )
        lines.append("")

    lines.append(
        f"_Total: {len(buy_results)} BUY + {len(sell_results)} SELL / {total} stocks_"
    )

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
        description="UTBot+SR+LinReg Multi-Signal Scanner for NSE Indices",
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

        buy_results, sell_results, seg_label, tf = run_scan(
            config, timeframe_override=timeframe, segment_override=segment,
        )

        # Resolve total symbol count for display
        _seg = segment or config.get("segment", "")
        if _seg:
            _total_syms = get_index_symbols(_seg)
            total = len(_total_syms) if _total_syms else len(buy_results) + len(sell_results)
        else:
            total = len(config.get("symbols", []))

        print_results_table(buy_results, sell_results, seg_label, tf, total)

        # Send Telegram alert
        msg = build_telegram_message(buy_results, sell_results, seg_label, tf, total)
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

                        buy_results, sell_results, seg_label, tf = run_scan(
                            config, timeframe_override=timeframe, segment_override=segment,
                        )

                        _seg = segment or config.get("segment", "")
                        if _seg:
                            _total_syms = get_index_symbols(_seg)
                            total = len(_total_syms) if _total_syms else len(buy_results) + len(sell_results)
                        else:
                            total = len(config.get("symbols", []))

                        print_results_table(buy_results, sell_results, seg_label, tf, total)

                        # Send Telegram alert if there are results
                        if buy_results or sell_results:
                            msg = build_telegram_message(
                                buy_results, sell_results, seg_label, tf, total,
                            )
                            tg_result = send_telegram_alert(msg, priority=8)
                            if "error" in tg_result:
                                log.warning("Telegram alert failed: %s", tg_result["error"])
                            else:
                                log.info("✅ Telegram alert sent successfully.")
                        else:
                            log.info("No signals — skipping Telegram alert.")
                    else:
                        log.debug("Same candle boundary, waiting for next bar...")
                else:
                    log.debug("Outside market hours, sleeping...")

                time.sleep(scan_interval)

        except KeyboardInterrupt:
            log.info("\nScanner stopped. Bye!")


if __name__ == "__main__":
    main()
