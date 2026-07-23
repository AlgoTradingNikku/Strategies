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
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    import io as _io
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
log = logging.getLogger("UTBotSRChannelsScanner")

# ---------------------------------------------------------------------------
# Local modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert                       # noqa: E402
from nse_indices import get_index_symbols, list_available_segments  # noqa: E402
from signals import (                                          # noqa: E402
    compute_utbot_signals,
    compute_sr_signals,
    evaluate_composite_signals,
)


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

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str   = end_dt.strftime("%Y-%m-%d")

    try:
        # ------------------------------------------------------------------
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
                auto_adjust=True,
            )

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

            oa_cfg = config.get("openalgo", {})
            client = oa_api(
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
                    log.warning("[%s] Unexpected history payload from openalgo.", symbol)
                    return None
            else:
                log.warning("[%s] Unexpected response type from openalgo.", symbol)
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

def scan_symbol(
    symbol: str,
    timeframe: str,
    config: dict,
    lookback_candles: int = 2,
) -> list[dict]:
    """
    Scan a single symbol through the enabled signal engines.

    Returns a list of result dicts (0, 1, or 2 — one per signal direction).
    Each dict contains signal metadata and which conditions triggered.
    """
    strat  = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})

    df = fetch_history(symbol, timeframe, config)
    if df is None or len(df) < 20:
        return []

    # ---- Run enabled signal engines ----------------------------------------
    if strat.get("ut_enabled", True):
        df = compute_utbot_signals(
            df,
            key_value       = float(strat.get("key_value", 1.0)),
            atr_period      = int(strat.get("atr_period", 2)),
            use_heikin_ashi = bool(strat.get("use_heikin_ashi", False)),
        )

    if sr_cfg.get("enabled", True):
        df = compute_sr_signals(
            df,
            pivot_period      = int(sr_cfg.get("pivot_period", 10)),
            source            = sr_cfg.get("source", "High/Low"),
            channel_width_pct = int(sr_cfg.get("channel_width_pct", 5)),
            min_strength      = int(sr_cfg.get("min_strength", 1)),
            max_num_sr        = int(sr_cfg.get("max_num_sr", 6)),
            loopback          = int(sr_cfg.get("loopback", 290)),
            proximity_pct     = float(sr_cfg.get("proximity_pct", 0.5)),
        )

    # ---- Evaluate composite signals ----------------------------------------
    composite  = evaluate_composite_signals(df, config, lookback_candles)

    results    = []
    last_row   = df.iloc[-1]
    close_price = float(last_row["close"])

    base_info = {
        "symbol":      symbol,
        "close":       close_price,
        "signal_time": df.index[-1],
        "ut_trail":    composite["details"].get("ut_trail"),
        "ut_pos":      composite["details"].get("ut_pos"),
        "sr_zones":    composite["details"].get("sr_zones", []),
    }

    if composite["buy"]:
        results.append({**base_info, "signal": "BUY",  "triggered": composite["triggered_buy"]})

    if composite["sell"]:
        results.append({**base_info, "signal": "SELL", "triggered": composite["triggered_sell"]})

    return results


def run_scan(
    config: dict,
    timeframe_override: str = None,
    segment_override: str   = None,
    mode_override: str      = None,
) -> tuple[list[dict], list[dict], str, str]:
    """
    Scan all symbols in parallel and return buy/sell results.

    Returns
    -------
    tuple: (buy_results, sell_results, segment_label, timeframe)
    """
    # Apply CLI overrides into config
    if mode_override:
        config = dict(config)
        config["signal_mode"] = mode_override

    timeframe = timeframe_override or config.get("scan_timeframe", "15m")
    lookback  = int(config.get("signal_lookback_candles", 2))
    mode      = config.get("signal_mode", "UTBot+SR").upper().replace(" ", "")

    strat  = config.get("strategy", {})
    sr_cfg = config.get("sr_channels", {})

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

    # ---- Build enabled engine list for logging ----------------------------
    engines = []
    if mode in ("UTBOT", "UTBOT+SR") and strat.get("ut_enabled", True):
        engines.append("UT Bot")
    if mode in ("SR", "UTBOT+SR") and sr_cfg.get("enabled", True):
        engines.append("S/R Channels")

    log.info("=" * 70)
    log.info("  UTBot + SR Channels Scanner — %s", segment_label)
    log.info("=" * 70)
    log.info("  Segment       : %s", segment_label)
    log.info("  Timeframe     : %s", timeframe)
    log.info("  Signal Mode   : %s", mode)
    log.info("  Lookback      : %d candles (UT Bot window)", lookback)
    log.info("  Engines       : %s", " + ".join(engines) if engines else "NONE")
    log.info("  Symbols       : %d stocks", len(symbols))
    log.info("  Data Source   : %s", config.get("data_source", "yfinance").upper())
    log.info("  Scan Time     : %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 70)

    buy_results  = []
    sell_results = []
    errors       = 0

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

    buy_results.sort(key=lambda r: r["symbol"])
    sell_results.sort(key=lambda r: r["symbol"])

    if errors:
        log.warning("  Scan completed with %d error(s).", errors)

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
    for hi, lo in zones[:2]:
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

    # ---- BUY table ----------------------------------------------------------
    if buy_results:
        log.info("")
        log.info("=" * 70)
        log.info("  🟢 %s — BUY Signals (%s)", segment_label, timeframe)
        log.info("=" * 70)
        log.info(
            "  %-4s  %-15s  %-10s  %-12s  %-20s  %-10s",
            "#", "SYMBOL", "CLOSE", "UT TRAIL", "SR ZONES", "CONDITIONS",
        )
        log.info("  " + "-" * 76)

        for i, r in enumerate(buy_results, 1):
            trail = f"{r['ut_trail']:.2f}" if r.get("ut_trail") is not None else "—"
            zones = _format_zones(r.get("sr_zones", []))
            conds = _format_conditions(r["triggered"])
            log.info(
                "  %-4d  %-15s  %-10.2f  %-12s  %-20s  %-10s",
                i, r["symbol"], r["close"], trail, zones, conds,
            )

        log.info("  " + "-" * 76)
        log.info("  Total: %d BUY signals", len(buy_results))
        log.info("=" * 70)

    # ---- SELL table ---------------------------------------------------------
    if sell_results:
        log.info("")
        log.info("=" * 70)
        log.info("  🔴 %s — SELL Signals (%s)", segment_label, timeframe)
        log.info("=" * 70)
        log.info(
            "  %-4s  %-15s  %-10s  %-12s  %-20s  %-10s",
            "#", "SYMBOL", "CLOSE", "UT TRAIL", "SR ZONES", "CONDITIONS",
        )
        log.info("  " + "-" * 76)

        for i, r in enumerate(sell_results, 1):
            trail = f"{r['ut_trail']:.2f}" if r.get("ut_trail") is not None else "—"
            zones = _format_zones(r.get("sr_zones", []))
            conds = _format_conditions(r["triggered"])
            log.info(
                "  %-4d  %-15s  %-10.2f  %-12s  %-20s  %-10s",
                i, r["symbol"], r["close"], trail, zones, conds,
            )

        log.info("  " + "-" * 76)
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
    mode: str,
    total_stocks: int = 0,
) -> str:
    """Build a consolidated HTML Telegram message for buy and sell signals."""
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = total_stocks or (len(buy_results) + len(sell_results))

    def _esc(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if not buy_results and not sell_results:
        return (
            f"\U0001f4ed <b>{_esc(segment_label)} — No Signals</b>\n"
            f"Mode: {_esc(mode)} | Timeframe: {timeframe}\n"
            f"Scanned at: {now}"
        )

    lines = [
        f"\U0001f4ca <b>{_esc(segment_label)} — UTBot+SR Channels Scanner</b>",
        f"Mode: <code>{_esc(mode)}</code> | TF: <code>{timeframe}</code> | {now}",
        "",
    ]

    if buy_results:
        lines.append("\U0001f7e2 <b>BUY Signals</b>")
        for i, r in enumerate(buy_results, 1):
            conds     = _format_conditions(r["triggered"])
            trail_str = f" | Trail: {r['ut_trail']:.2f}" if r.get("ut_trail") is not None else ""
            zones     = _format_zones(r.get("sr_zones", []))
            zone_str  = f" | Zones: {zones}" if zones != "—" else ""
            lines.append(
                f"{i}. <b>{_esc(r['symbol'])}</b> — \u20b9{r['close']:.2f}"
                f" [{conds}]{trail_str}{zone_str}"
            )
        lines.append("")

    if sell_results:
        lines.append("\U0001f534 <b>SELL Signals</b>")
        for i, r in enumerate(sell_results, 1):
            conds     = _format_conditions(r["triggered"])
            trail_str = f" | Trail: {r['ut_trail']:.2f}" if r.get("ut_trail") is not None else ""
            zones     = _format_zones(r.get("sr_zones", []))
            zone_str  = f" | Zones: {zones}" if zones != "—" else ""
            lines.append(
                f"{i}. <b>{_esc(r['symbol'])}</b> — \u20b9{r['close']:.2f}"
                f" [{conds}]{trail_str}{zone_str}"
            )
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

    now = datetime.now()
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

    # Apply log level from config
    log_level_str = config.get("bot", {}).get("log_level", "INFO").upper()
    log_level     = getattr(logging, log_level_str, logging.INFO)
    logging.getLogger().setLevel(log_level)

    timeframe     = args.tf or config.get("scan_timeframe", "15m")
    segment       = args.segment  # None → use config value
    mode_override = args.mode     # None → use config value
    scan_interval = int(config.get("scan_interval_seconds", 300))
    eff_mode      = mode_override or config.get("signal_mode", "UTBot+SR")

    def _do_scan() -> tuple[list, list, str, str]:
        return run_scan(
            config,
            timeframe_override=timeframe,
            segment_override=segment,
            mode_override=mode_override,
        )

    def _get_total(seg_label: str) -> int:
        """Return the total number of symbols that would be scanned."""
        _seg = segment or config.get("segment", "")
        _use = config.get("use_symbols", False)
        if isinstance(_seg, str):
            seg_list = [_seg] if _seg.strip() else []
        else:
            seg_list = [s for s in (_seg or []) if s and s.strip()]

        total_syms: set = set()
        for s in seg_list:
            syms = get_index_symbols(s)
            total_syms.update(syms)
        if _use or not seg_list:
            total_syms.update(config.get("symbols", []))
        return len(total_syms) if total_syms else len(config.get("symbols", []))

    if args.once:
        # ── Single scan mode ─────────────────────────────────────────────
        if not _is_market_hours(config):
            log.info("Outside market hours — running scan anyway (--once mode).")

        buy_results, sell_results, seg_label, tf = _do_scan()
        total = _get_total(seg_label)

        print_results_table(buy_results, sell_results, seg_label, tf, total)

        msg       = build_telegram_message(buy_results, sell_results, seg_label, tf, eff_mode, total)
        tg_result = send_telegram_alert(msg, priority=8)
        if "error" in tg_result:
            log.warning("Telegram alert failed: %s", tg_result["error"])
        else:
            log.info("✅ Telegram alert sent successfully.")

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

                        buy_results, sell_results, seg_label, tf = _do_scan()
                        total = _get_total(seg_label)

                        print_results_table(buy_results, sell_results, seg_label, tf, total)

                        # Send Telegram only when signals are found
                        if buy_results or sell_results:
                            msg       = build_telegram_message(
                                buy_results, sell_results, seg_label, tf, eff_mode, total,
                            )
                            tg_result = send_telegram_alert(msg, priority=8)
                            if "error" in tg_result:
                                log.warning("Telegram alert failed: %s", tg_result["error"])
                            else:
                                log.info("✅ Telegram alert sent successfully.")
                        else:
                            log.info("No signals — skipping Telegram alert.")
                    else:
                        log.debug("Same candle boundary — waiting for next bar...")
                else:
                    log.debug("Outside market hours — sleeping...")

                time.sleep(scan_interval)

        except KeyboardInterrupt:
            log.info("\nScanner stopped. Goodbye!")


if __name__ == "__main__":
    main()
