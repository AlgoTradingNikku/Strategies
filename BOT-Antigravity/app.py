"""
===============================================================================
  UT BOT ANTIGRAVITY — Python equivalent of UT Bot Alerts (PineScript v5)
===============================================================================

Strategy Logic (ported from PineScript):
  1. Compute ATR over `atr_period` bars.
  2. Multiply ATR by `key_value`  →  nLoss  (the "sensitivity" level).
  3. Iteratively build xATRTrailingStop (a ratcheting ATR stop):
       - Price above stop → stop moves up (max of previous stop, price − nLoss)
       - Price below stop → stop moves down (min of previous stop, price + nLoss)
  4. Position:
       - +1 (bullish) when price crosses above the trailing stop from below
       - -1 (bearish) when price crosses below the trailing stop from above
  5. EMA(1) ≡ close used for crossover detection.
       Buy  = close > xATRTrailingStop  AND  EMA crosses ABOVE xATRTrailingStop
       Sell = close < xATRTrailingStop  AND  xATRTrailingStop crosses ABOVE EMA

Features:
  - Reads symbols / timeframes / params from config.yml
  - Fetches historical candles via OpenAlgo REST API
  - Streams live prices via OpenAlgo WebSocket
  - Sends Telegram alerts on new Buy / Sell signals

Run:
    python app.py

Stop:
    Ctrl+C
===============================================================================
"""

import sys
import os
import threading
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from openalgo import api

# ---------------------------------------------------------------------------
# ML components (optional — bot works without a trained model)
# ---------------------------------------------------------------------------
try:
    from signal_logger import log_signal, extract_features, signal_count, labeled_count
    from ml_filter import MLFilter
    _ML_AVAILABLE = True
except ImportError as _ml_err:
    _ML_AVAILABLE = False
    log_signal = extract_features = signal_count = labeled_count = None  # type: ignore
    MLFilter = None  # type: ignore

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "utbot.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("UTBot")

# ---------------------------------------------------------------------------
# Import Telegram notifier from the sibling module
# ---------------------------------------------------------------------------
_bot_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert  # noqa: E402


# ============================================================================
# CONFIG LOADING
# ============================================================================

def load_config(path: Path | str = None) -> dict:
    """Load and return the YAML configuration."""
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ============================================================================
# UT BOT SIGNAL ENGINE
# ============================================================================

def compute_utbot_signals(
    df: pd.DataFrame,
    key_value: float = 2.0,
    atr_period: int = 1,
    use_heikin_ashi: bool = False,
) -> pd.DataFrame:
    """
    Compute UT Bot ATR Trailing Stop signals.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV dataframe with columns [open, high, low, close, volume].
        Index must be datetime.
    key_value : float
        Sensitivity multiplier for ATR (Pine: `a`).
    atr_period : int
        ATR look-back period (Pine: `c`).
    use_heikin_ashi : bool
        If True, replace `src` with Heikin-Ashi close.

    Returns
    -------
    pd.DataFrame with additional columns:
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

    # ATR as simple RMA (Wilder's) — Pine `ta.atr` uses RMA by default
    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    n_loss = key_value * atr

    # ---- xATRTrailingStop (vectorised iterative reconstruction) -------------
    # Pine pseudocode (per bar):
    #   if src > prev_stop and prev_src > prev_stop:
    #       stop = max(prev_stop, src - nLoss)
    #   elif src < prev_stop and prev_src < prev_stop:
    #       stop = min(prev_stop, src + nLoss)
    #   elif src > prev_stop:
    #       stop = src - nLoss
    #   else:
    #       stop = src + nLoss

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

    # ---- EMA(1) ≡ close (Pine: `ema = ta.ema(src, 1)`) ---------------------
    ema = src  # EMA with span=1 is exactly the source price

    # ---- Crossover helpers --------------------------------------------------
    def crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
        """True when s1 crosses above s2."""
        return (s1 > s2) & (s1.shift(1) <= s2.shift(1))

    above = crossover(ema, xATR)   # EMA crosses above trailing stop
    below = crossover(xATR, ema)   # trailing stop crosses above EMA

    # ---- Final signals ------------------------------------------------------
    df["atr"] = atr
    df["nLoss"] = n_loss
    df["xATRTrailingStop"] = xATR
    df["pos"] = pos_series
    df["src"] = src

    # Buy:  close > xATRTrailingStop  AND  EMA crossed above stop
    df["buy"] = (src > xATR) & above

    # Sell: close < xATRTrailingStop  AND  stop crossed above EMA
    df["sell"] = (src < xATR) & below

    return df


# ============================================================================
# TIMEFRAME WORKER — one per (symbol, timeframe) pair
# ============================================================================

class TimeframeWorker:
    """
    Monitors one (symbol, timeframe) combination.
    Periodically fetches historical candles, computes UT Bot signals,
    and fires a Telegram alert when a new Buy or Sell signal appears
    on the *last closed* candle.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        client,
        config: dict,
        stop_event: threading.Event,
        ltp_map: dict | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.client = client
        self.config = config
        self.stop_event = stop_event
        self._ltp_map = ltp_map if ltp_map is not None else {}

        strat = config.get("strategy", {})
        self.key_value = float(strat.get("key_value", 2))
        self.atr_period = int(strat.get("atr_period", 1))
        self.use_heikin_ashi = bool(strat.get("use_heikin_ashi", False))

        data_cfg = config.get("data", {})
        self.lookback_days = int(data_cfg.get("lookback_days", 5))

        bot_cfg = config.get("bot", {})
        self.check_interval = int(bot_cfg.get("signal_check_interval", 5))

        # Track last signal to avoid duplicate alerts
        self._last_signal_ts = None
        self._last_signal_type = None

        self.exchange = config.get("exchange", "NSE")

        # Candle-boundary caching ─────────────────────────────────────────────
        # Parse the timeframe string into a timedelta so we know how often a
        # new candle can form.  We only re-fetch when we cross into a new bar.
        self._candle_duration: timedelta = _parse_timeframe(timeframe)
        self._last_fetched_boundary: datetime | None = None

        # ── ML Filter ────────────────────────────────────────────────────────
        ml_cfg = config.get("ml", {})
        self._ml_log_signals  = bool(ml_cfg.get("log_signals", True))
        self._ml_enabled      = bool(ml_cfg.get("enabled",     False)) and _ML_AVAILABLE
        ml_threshold          = float(ml_cfg.get("confidence_threshold", 0.60))
        if _ML_AVAILABLE and MLFilter is not None:
            self._ml_filter = MLFilter(threshold=ml_threshold)
        else:
            self._ml_filter = None

    # ------------------------------------------------------------------
    def _current_boundary(self) -> datetime:
        """Return the start of the candle bar that is currently open."""
        now = datetime.now()
        secs = int(self._candle_duration.total_seconds())
        # Floor to the nearest candle boundary relative to midnight
        epoch_secs = int(now.timestamp())
        boundary_secs = (epoch_secs // secs) * secs
        return datetime.fromtimestamp(boundary_secs)

    # ------------------------------------------------------------------
    def _fetch_history(self) -> pd.DataFrame | None:
        """Fetch OHLCV candles from OpenAlgo."""
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=self.lookback_days)

        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        log.info(
            "[%s|%s] Fetching history | lookback=%d days | from=%s to=%s | candle=%s",
            self.symbol, self.timeframe,
            self.lookback_days, start_str, end_str,
            str(self._candle_duration),
        )

        try:
            raw = self.client.history(
                symbol=self.symbol,
                exchange=self.exchange,
                interval=self.timeframe,
                start_date=start_str,
                end_date=end_str,
            )
        except Exception as exc:
            log.error("[%s|%s] History fetch error: %s", self.symbol, self.timeframe, exc)
            return None

        # The SDK may return a DataFrame directly or a dict like {"data": <DataFrame|list>}
        if isinstance(raw, pd.DataFrame):
            df = raw
        elif isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, pd.DataFrame):
                df = data
            elif isinstance(data, list) and data:
                df = pd.DataFrame(data)
            else:
                log.warning(
                    "[%s|%s] Unexpected history dict payload: %s",
                    self.symbol, self.timeframe, raw,
                )
                return None
        else:
            log.warning(
                "[%s|%s] Unexpected history response type: %s",
                self.symbol, self.timeframe, type(raw),
            )
            return None

        if df is None or df.empty:
            log.warning("[%s|%s] No historical data returned.", self.symbol, self.timeframe)
            return None

        # Normalise index to datetime
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime")
        elif "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        else:
            df.index = pd.to_datetime(df.index)

        df = df.sort_index()

        # Ensure lowercase column names
        df.columns = [c.lower() for c in df.columns]

        first_bar = df.index[0].strftime("%Y-%m-%d %H:%M")
        last_bar  = df.index[-1].strftime("%Y-%m-%d %H:%M")
        log.info(
            "[%s|%s] Received %d candles | first=%s | last=%s",
            self.symbol, self.timeframe,
            len(df), first_bar, last_bar,
        )

        return df

    # ------------------------------------------------------------------
    def _check_and_alert(self):
        """Fetch data only on a new candle boundary, then compute signals."""
        boundary = self._current_boundary()

        if self._last_fetched_boundary == boundary:
            # Same candle bar — no new data possible, skip the API call
            log.debug(
                "[%s|%s] Same candle boundary (%s), skipping fetch.",
                self.symbol, self.timeframe,
                boundary.strftime("%H:%M"),
            )
            return

        self._last_fetched_boundary = boundary
        df = self._fetch_history()
        if df is None or len(df) < self.atr_period + 3:
            return

        df = compute_utbot_signals(
            df,
            key_value=self.key_value,
            atr_period=self.atr_period,
            use_heikin_ashi=self.use_heikin_ashi,
        )

        # ── Dynamically find the last CLOSED candle ───────────────────────────
        # The broker API sometimes doesn't include the currently-forming bar,
        # so we can't assume iloc[-1] is always the live candle.
        # Compare the last bar's timestamp against the current boundary:
        #   • last bar timestamp >= boundary  →  it IS the forming candle → use iloc[-2]
        #   • last bar timestamp <  boundary  →  API lagged, ALL bars closed → use iloc[-1]
        if len(df) < 2:
            return

        last_bar_ts = df.index[-1].to_pydatetime().replace(tzinfo=None)
        boundary_naive = boundary.replace(tzinfo=None)

        if last_bar_ts >= boundary_naive:
            # Normal case: last bar is the currently-forming candle
            closed_idx = -2
        else:
            # API lag: broker hasn't pushed the current candle yet, all bars closed
            closed_idx = -1
            log.debug(
                "[%s|%s] API lag detected — last bar %s < boundary %s, using iloc[-1].",
                self.symbol, self.timeframe,
                last_bar_ts.strftime("%H:%M"),
                boundary_naive.strftime("%H:%M"),
            )

        last_closed = df.iloc[closed_idx]
        signal_ts   = df.index[closed_idx]

        buy_signal  = bool(last_closed["buy"])
        sell_signal = bool(last_closed["sell"])

        # ── Scan summary — always logged so you can confirm the bot is running ──
        pos_val   = int(last_closed["pos"])
        pos_label = "LONG" if pos_val == 1 else ("SHORT" if pos_val == -1 else "FLAT")
        sig_label = "BUY" if buy_signal else ("SELL" if sell_signal else "NONE")
        log.info(
            "[%s|%s] SCAN  bar=%s  close=%.2f  ATRStop=%.2f  pos=%s  signal=%s",
            self.symbol, self.timeframe,
            signal_ts.strftime("%H:%M"),
            last_closed["close"],
            last_closed["xATRTrailingStop"],
            pos_label,
            sig_label,
        )

        if not buy_signal and not sell_signal:
            return

        # Determine signal type
        signal_type = "BUY" if buy_signal else "SELL"

        # Deduplicate: only fire if this bar / signal type is new
        if signal_ts == self._last_signal_ts and signal_type == self._last_signal_type:
            return

        self._last_signal_ts   = signal_ts
        self._last_signal_type = signal_type

        close_price = float(last_closed["close"])
        atr_stop    = float(last_closed["xATRTrailingStop"])

        # ── ML Feature extraction ─────────────────────────────────────────────
        features     = {}
        ml_conf      = -1.0      # -1 = model not loaded
        ml_fired     = True      # default: always fire
        ml_conf_str  = ""

        if _ML_AVAILABLE and extract_features is not None:
            try:
                features = extract_features(df, closed_idx)
            except Exception as _fe:
                log.warning("[%s|%s] Feature extraction error: %s", self.symbol, self.timeframe, _fe)

        # ── Log signal for future training ────────────────────────────────────
        if self._ml_log_signals and _ML_AVAILABLE and log_signal is not None:
            try:
                log_signal(
                    bar_time    = signal_ts.to_pydatetime(),
                    symbol      = self.symbol,
                    timeframe   = self.timeframe,
                    signal_type = signal_type,
                    features    = features,
                )
                n_total  = signal_count()
                n_labeled = labeled_count()
                log.info(
                    "[%s|%s] Signal logged — DB: %d total, %d labeled",
                    self.symbol, self.timeframe, n_total, n_labeled,
                )
            except Exception as _le:
                log.warning("[%s|%s] Signal logging error: %s", self.symbol, self.timeframe, _le)

        # ── ML filter gate ────────────────────────────────────────────────────
        if self._ml_enabled and self._ml_filter is not None and self._ml_filter.is_ready():
            ml_fired, ml_conf = self._ml_filter.should_fire(features, signal_type)
            ml_conf_str = f"\nML Confidence : {ml_conf*100:.0f}%"
            if not ml_fired:
                log.info(
                    "[%s|%s] %s signal SUPPRESSED by ML filter (conf=%.0f%% < %.0f%%)",
                    self.symbol, self.timeframe, signal_type,
                    ml_conf * 100, self._ml_filter.threshold * 100,
                )
                return
            log.info(
                "[%s|%s] %s signal PASSED ML filter (conf=%.0f%%)",
                self.symbol, self.timeframe, signal_type, ml_conf * 100,
            )

        # ── Build Telegram message ────────────────────────────────────────────
        if signal_type == "BUY":
            emoji          = "🟢"
            direction_word = "Buy"
        else:
            emoji          = "🔴"
            direction_word = "Sell"

        # Confidence badge (only shown when model is active)
        if ml_conf >= 0:
            badge = f" ✅ {ml_conf*100:.0f}%" if ml_fired else f" ❌ {ml_conf*100:.0f}%"
        else:
            badge = ""

        bar_close_ts = signal_ts + self._candle_duration
        ltp_val = self._ltp_map.get(self.symbol)
        ltp_str = f"{ltp_val:.2f}" if ltp_val is not None else "N/A"
        message = (
            f"{emoji} *{direction_word} Signal{badge}* — {self.symbol} on {self.timeframe} chart\n"
            f"LTP        : {ltp_str}\n"
            f"Bar Close  : {close_price:.2f}\n"
            f"ATR Stop   : {atr_stop:.2f}\n"
            f"Bar Closed : {bar_close_ts.strftime('%Y-%m-%d %H:%M')}"
            f"{ml_conf_str}"
        )

        log.info("[%s|%s] %s signal detected @ %s (close=%.2f)",
                 self.symbol, self.timeframe, signal_type, signal_ts, close_price)

        # Send Telegram notification
        result = send_telegram_alert(message, priority=8)
        if "error" in result:
            log.warning("[%s|%s] Telegram alert failed: %s",
                        self.symbol, self.timeframe, result["error"])
        else:
            log.info("[%s|%s] Telegram alert sent.", self.symbol, self.timeframe)

    # ------------------------------------------------------------------
    def run(self):
        """Main loop for this (symbol, timeframe) worker thread."""
        log.info("[%s|%s] Worker started.", self.symbol, self.timeframe)

        while not self.stop_event.is_set():
            try:
                if _is_market_hours(self.config):
                    self._check_and_alert()
                else:
                    log.debug("[%s|%s] Outside market hours, skipping.", self.symbol, self.timeframe)
            except Exception as exc:
                log.error("[%s|%s] Unexpected error: %s", self.symbol, self.timeframe, exc)

            self.stop_event.wait(timeout=self.check_interval)

        log.info("[%s|%s] Worker stopped.", self.symbol, self.timeframe)


# ============================================================================
# LIVE PRICE MONITOR (WebSocket)
# ============================================================================

class LivePriceMonitor:
    """
    Opens a single WebSocket connection to OpenAlgo and subscribes to
    LTP (Last Traded Price) updates for all configured symbols.
    Prints live prices to the console — can be extended to feed into
    the signal engine if sub-candle real-time updates are needed.
    """

    def __init__(self, client, symbols: list[str], exchange: str, stop_event: threading.Event):
        self.client = client
        self.exchange = exchange
        self.stop_event = stop_event
        self.instruments = [{"exchange": exchange, "symbol": s} for s in symbols]
        self.ltp_map: dict[str, float] = {}

    def _on_data(self, data: dict):
        if data.get("type") != "market_data":
            return
        sym = data.get("symbol", "")
        ltp_val = data.get("data", {}).get("ltp")
        if ltp_val is not None:
            self.ltp_map[sym] = float(ltp_val)
            log.debug("[WS] %s LTP = %.2f", sym, self.ltp_map[sym])

    def run(self):
        log.info("[WS] Connecting to WebSocket...")
        try:
            self.client.connect()
            self.client.subscribe_ltp(self.instruments, on_data_received=self._on_data)
            log.info("[WS] Subscribed to LTP for: %s", [i["symbol"] for i in self.instruments])

            while not self.stop_event.is_set():
                time.sleep(1)

        except Exception as exc:
            log.error("[WS] WebSocket error: %s", exc)
        finally:
            log.info("[WS] Disconnecting...")
            try:
                self.client.unsubscribe_ltp(self.instruments)
                self.client.disconnect()
            except Exception:
                pass
            log.info("[WS] Disconnected.")


# ============================================================================
# HELPERS
# ============================================================================

def _parse_timeframe(tf: str) -> timedelta:
    """
    Convert an OpenAlgo timeframe string to a timedelta.
    Supported: 1m, 3m, 5m, 10m, 15m, 30m, 1h, D
    """
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return timedelta(minutes=int(tf[:-1]))
    if tf.endswith("h"):
        return timedelta(hours=int(tf[:-1]))
    if tf in ("d", "1d", "day"):
        return timedelta(days=1)
    raise ValueError(f"Unsupported timeframe: {tf!r}")


def _is_market_hours(config: dict) -> bool:
    """Return True if current IST time is within configured market hours (Mon–Fri only)."""
    bot_cfg = config.get("bot", {})
    open_str = bot_cfg.get("market_open", "09:15")
    close_str = bot_cfg.get("market_close", "15:30")

    now = datetime.now()

    # Reject weekends (Saturday=5, Sunday=6)
    if now.weekday() >= 5:
        return False

    today = now.date()
    market_open  = datetime.strptime(f"{today} {open_str}",  "%Y-%m-%d %H:%M")
    market_close = datetime.strptime(f"{today} {close_str}", "%Y-%m-%d %H:%M")

    return market_open <= now <= market_close


def _print_banner(config: dict):
    symbols = config.get("symbols", [])
    timeframes = config.get("timeframes", [])
    strat = config.get("strategy", {})

    width = 62
    sep = "=" * width
    log.info(sep)
    log.info("  UT BOT ANTIGRAVITY — Signal Monitor")
    log.info(sep)
    log.info("  Symbols    : %s", ", ".join(symbols))
    log.info("  Timeframes : %s", ", ".join(timeframes))
    log.info("  Exchange   : %s", config.get("exchange", "NSE"))
    log.info("  Key Value  : %s  |  ATR Period: %s  |  HA: %s",
             strat.get("key_value", 2),
             strat.get("atr_period", 1),
             strat.get("use_heikin_ashi", False))
    log.info("  Host       : %s", config.get("openalgo", {}).get("base_url", ""))
    log.info(sep)
    log.info("  Press Ctrl+C to stop the bot")
    log.info(sep)


# ============================================================================
# MAIN
# ============================================================================

def main():
    config = load_config()

    # Apply log level from config (bot.log_level: DEBUG | INFO | WARNING | ERROR)
    _log_level_str = config.get("bot", {}).get("log_level", "INFO").upper()
    _log_level = getattr(logging, _log_level_str, logging.INFO)
    logging.getLogger().setLevel(_log_level)
    log.info("Log level set to: %s", _log_level_str)

    _print_banner(config)

    oa_cfg = config.get("openalgo", {})
    api_key = oa_cfg.get("apikey", "")
    base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
    ws_url = oa_cfg.get("ws_url", "ws://127.0.0.1:8765")

    symbols: list[str] = config.get("symbols", [])
    timeframes: list[str] = config.get("timeframes", ["5m"])
    exchange: str = config.get("exchange", "NSE")

    if not symbols:
        log.error("No symbols configured in config.yml. Exiting.")
        sys.exit(1)

    # ---- OpenAlgo API client ------------------------------------------------
    client = api(api_key=api_key, host=base_url, ws_url=ws_url)

    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    # ---- WebSocket live price monitor (one shared connection) ---------------
    ws_monitor = LivePriceMonitor(client, symbols, exchange, stop_event)
    ws_thread = threading.Thread(
        target=ws_monitor.run,
        name="WS-LivePrices",
        daemon=True,
    )
    threads.append(ws_thread)
    ws_thread.start()

    # Give WS a moment to connect before spawning signal workers
    time.sleep(2)

    # ---- One worker thread per (symbol, timeframe) pair --------------------
    for symbol in symbols:
        for tf in timeframes:
            worker = TimeframeWorker(
                symbol=symbol,
                timeframe=tf,
                client=client,
                config=config,
                stop_event=stop_event,
                ltp_map=ws_monitor.ltp_map,
            )
            t = threading.Thread(
                target=worker.run,
                name=f"Worker-{symbol}-{tf}",
                daemon=True,
            )
            threads.append(t)
            t.start()
            log.info("Started worker: %s @ %s", symbol, tf)

    # ---- Keep main thread alive; handle Ctrl+C gracefully ------------------
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\nShutting down — waiting for workers to stop...")
        stop_event.set()

        for t in threads:
            t.join(timeout=10)

        log.info("All workers stopped. Bye!")


if __name__ == "__main__":
    main()
