"""
OpenAlgo AI Trading Bot  —  with Flask Web Dashboard
------------------------------------------------------
Strategy  : Momentum + RSI + MACD  (configurable)
Exchange  : NSE / NFO
Broker API: OpenAlgo REST  (http://127.0.0.1:5000)
Dashboard : http://127.0.0.1:8080

Install deps:
    pip install openalgo pandas numpy requests schedule python-dotenv flask flask-socketio
"""

import time
import logging
import threading
import requests
import pandas as pd
import numpy as np
import schedule
from datetime import datetime, timedelta, time as dtime
from dataclasses import dataclass, field
from typing import Optional

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger("openalgo_bot")


# ── Config ───────────────────────────────────────────────────────────────────
@dataclass
class BotConfig:
    api_key:   str = "67bbfd35133ff6314697105c0f556c15efd2f357f23bf9c7f95577e1bb1f87a3"
    base_url:  str = "http://127.0.0.1:5000/api/v1"

    symbols: list = field(default_factory=lambda: [
        "RELIANCE", "INFY", "TCS", "HDFCBANK", "SBIN", "WIPRO"
    ])
    exchange: str = "NSE"
    product:  str = "MIS"

    rsi_period:      int   = 14
    rsi_oversold:    float = 35.0
    rsi_overbought:  float = 65.0
    macd_fast:       int   = 12
    macd_slow:       int   = 26
    macd_signal:     int   = 9
    candle_interval: str   = "5m"

    capital_per_trade: float = 25_000.0
    stop_loss_pct:     float = 2.0
    take_profit_pct:   float = 4.0
    max_open_trades:   int   = 4

    market_open:  dtime = dtime(9, 20)
    market_close: dtime = dtime(15, 10)
    squareoff_at: dtime = dtime(15, 10)
    order_delay_sec: float = 0.15

    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080


CONFIG = BotConfig()


# ── OpenAlgo API client ──────────────────────────────────────────────────────
class OpenAlgoClient:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _post(self, endpoint: str, payload: dict) -> dict:
        payload["apikey"] = self.cfg.api_key
        url = f"{self.cfg.base_url}{endpoint}"
        try:
            r = self.session.post(url, json=payload, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            log.error("Cannot reach OpenAlgo at %s", self.cfg.base_url)
            return {"status": "error", "message": "connection_error"}
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                log.warning("Rate limit hit — sleeping 1s")
                time.sleep(1)
            return {"status": "error", "message": str(e)}
        except Exception as e:
            log.error("API error: %s", e)
            return {"status": "error", "message": str(e)}

    def get_quote(self, symbol: str) -> Optional[dict]:
        res = self._post("/quotes", {"symbol": symbol, "exchange": self.cfg.exchange})
        return res.get("data") if res["status"] == "success" else None

    def get_history(self, symbol: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
        end_date   = datetime.now()
        start_date = end_date - timedelta(days=days)
        payload = {
            "symbol":     symbol,
            "exchange":   self.cfg.exchange,
            "interval":   interval,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date":   end_date.strftime("%Y-%m-%d"),
        }
        res = self._post("/history", payload)
        if res.get("status") != "success":
            log.warning("History API FAILED for %s | status=%s | message=%s | payload=%s",
                        symbol, res.get("status"), res.get("message", str(res)), payload)
            return pd.DataFrame()
        data = res.get("data", [])
        if not data:
            log.warning("History API returned SUCCESS but empty data for %s (start=%s end=%s)",
                        symbol, payload["start_date"], payload["end_date"])
            return pd.DataFrame()
        # data can be a list of dicts or a DataFrame-like structure
        df = pd.DataFrame(data) if not isinstance(data, pd.DataFrame) else data
        df.columns = [c.lower() for c in df.columns]
        if "datetime" not in df.columns and "date" in df.columns:
            df.rename(columns={"date": "datetime"}, inplace=True)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        log.info("History OK: %s  rows=%d  cols=%s", symbol, len(df), list(df.columns))
        return df

    def get_funds(self) -> Optional[dict]:
        res = self._post("/funds", {})
        return res.get("data") if res["status"] == "success" else None

    def get_positions(self) -> list:
        res = self._post("/positionbook", {})
        return res.get("data", []) if res["status"] == "success" else []

    def get_orderbook(self) -> list:
        res = self._post("/orderbook", {})
        return res.get("data", []) if res["status"] == "success" else []

    def place_order(self, symbol: str, action: str, qty: int,
                    price_type: str = "MARKET", price: float = 0.0) -> Optional[str]:
        payload = {
            "symbol": symbol, "exchange": self.cfg.exchange,
            "action": action, "quantity": str(qty),
            "pricetype": price_type, "price": str(price),
            "product": self.cfg.product,
            "disclosed_qty": "0", "trigger_price": "0",
        }
        res = self._post("/placeorder", payload)
        if res["status"] == "success":
            oid = res.get("data", {}).get("orderid", "unknown")
            log.info("✅ %s %s x%d  order_id=%s", action, symbol, qty, oid)
            return oid
        log.error("Order failed for %s: %s", symbol, res.get("message"))
        return None

    def close_all_positions(self) -> bool:
        res = self._post("/closeposition", {})
        return res["status"] == "success"


# ── Indicators ───────────────────────────────────────────────────────────────
class Indicators:
    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(close: pd.Series, fast=12, slow=26, signal=9):
        ema_fast    = close.ewm(span=fast,   adjust=False).mean()
        ema_slow    = close.ewm(span=slow,   adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram   = macd_line - signal_line
        return macd_line, signal_line, histogram


# ── Signal Engine ────────────────────────────────────────────────────────────
class SignalEngine:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg

    def analyse(self, df: pd.DataFrame) -> dict:
        """Returns signal dict with signal, rsi, macd values."""
        result = {"signal": "HOLD", "rsi": None, "macd_hist": None}
        if len(df) < self.cfg.macd_slow + self.cfg.macd_signal + 5:
            return result
        close = df["close"]
        rsi   = Indicators.rsi(close, self.cfg.rsi_period)
        macd, signal_line, hist = Indicators.macd(
            close, self.cfg.macd_fast, self.cfg.macd_slow, self.cfg.macd_signal
        )
        rsi_now   = round(float(rsi.iloc[-1]), 2)
        hist_now  = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])

        result["rsi"]       = rsi_now
        result["macd_hist"] = round(hist_now, 4)

        if rsi_now < self.cfg.rsi_oversold and hist_now > 0 and hist_prev <= 0:
            result["signal"] = "BUY"
        elif rsi_now > self.cfg.rsi_overbought and hist_now < 0 and hist_prev >= 0:
            result["signal"] = "SELL"
        return result

    def calc_qty(self, ltp: float, capital: float) -> int:
        return max(1, int(capital // ltp)) if ltp > 0 else 0


# ── Position Tracker ─────────────────────────────────────────────────────────
@dataclass
class OpenTrade:
    symbol:      str
    action:      str
    qty:         int
    entry_price: float
    stop_loss:   float
    take_profit: float
    order_id:    str
    entry_time:  datetime = field(default_factory=datetime.now)
    unrealised_pnl: float = 0.0

    def to_dict(self):
        return {
            "symbol":       self.symbol,
            "action":       self.action,
            "qty":          self.qty,
            "entry_price":  self.entry_price,
            "stop_loss":    self.stop_loss,
            "take_profit":  self.take_profit,
            "order_id":     self.order_id,
            "entry_time":   self.entry_time.strftime("%H:%M:%S"),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
        }


class PositionManager:
    def __init__(self):
        self.trades: dict[str, OpenTrade] = {}

    def add(self, trade: OpenTrade):
        self.trades[trade.symbol] = trade

    def remove(self, symbol: str):
        self.trades.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self.trades

    def count(self) -> int:
        return len(self.trades)

    def check_exits(self, symbol: str, ltp: float) -> Optional[str]:
        t = self.trades.get(symbol)
        if not t:
            return None
        if t.action == "BUY":
            if ltp <= t.stop_loss:   return "SL"
            if ltp >= t.take_profit: return "TP"
        else:
            if ltp >= t.stop_loss:   return "SL"
            if ltp <= t.take_profit: return "TP"
        return None

    def total_pnl(self) -> float:
        return sum(t.unrealised_pnl for t in self.trades.values())


# ── Global state (shared between bot thread & Flask) ────────────────────────
class BotState:
    def __init__(self):
        self.running        = False
        self.connected      = False
        self.available_cash = 0.0
        self.total_pnl      = 0.0
        self.total_trades   = 0
        self.wins           = 0
        self.losses         = 0
        self.activity_log   = []   # list of dicts
        self.watchlist      = []   # list of dicts
        self.closed_trades  = []   # list of dicts
        self.lock           = threading.Lock()

    def add_log(self, msg: str, kind: str = "info"):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg":  msg,
            "kind": kind,   # info / buy / sell / warn / error
        }
        with self.lock:
            self.activity_log.append(entry)
            if len(self.activity_log) > 200:
                self.activity_log.pop(0)
        return entry

    def to_summary(self) -> dict:
        win_rate = 0
        if self.total_trades:
            win_rate = round(self.wins / self.total_trades * 100, 1)
        return {
            "running":        self.running,
            "connected":      self.connected,
            "available_cash": round(self.available_cash, 2),
            "total_pnl":      round(self.total_pnl, 2),
            "total_trades":   self.total_trades,
            "wins":           self.wins,
            "losses":         self.losses,
            "win_rate":       win_rate,
        }


STATE = BotState()


# ── Main Bot ─────────────────────────────────────────────────────────────────
class TradingBot:
    def __init__(self, cfg: BotConfig, state: BotState, socketio: SocketIO):
        self.cfg      = cfg
        self.state    = state
        self.client   = OpenAlgoClient(cfg)
        self.signals  = SignalEngine(cfg)
        self.pm       = PositionManager()
        self.sio      = socketio

    def _emit(self, event: str, data: dict):
        """Thread-safe SocketIO emit."""
        self.sio.emit(event, data)

    def _log(self, msg: str, kind: str = "info"):
        entry = self.state.add_log(msg, kind)
        self._emit("log", entry)
        getattr(log, "warning" if kind == "warn" else kind if kind in ("info","error") else "info")(msg)

    def _market_open(self) -> bool:
        now = datetime.now().time()
        return self.cfg.market_open <= now <= self.cfg.market_close

    def run_cycle(self):
        if not self._market_open():
            self._log("Market closed — bot idle", "info")
            return

        self._log(f"── Scan cycle {datetime.now().strftime('%H:%M:%S')} ──")
        watchlist_snap = []

        for symbol in self.cfg.symbols:
            time.sleep(self.cfg.order_delay_sec)

            # Check exits
            if self.pm.has(symbol):
                quote = self.client.get_quote(symbol)
                if quote:
                    ltp = float(quote.get("ltp", 0))
                    trade = self.pm.trades[symbol]
                    # Update unrealised PnL
                    if trade.action == "BUY":
                        trade.unrealised_pnl = (ltp - trade.entry_price) * trade.qty
                    else:
                        trade.unrealised_pnl = (trade.entry_price - ltp) * trade.qty

                    reason = self.pm.check_exits(symbol, ltp)
                    if reason:
                        exit_action = "SELL" if trade.action == "BUY" else "BUY"
                        self.client.place_order(symbol, exit_action, trade.qty)
                        pnl = trade.unrealised_pnl
                        self.state.total_pnl += pnl
                        self.state.total_trades += 1
                        if pnl >= 0:
                            self.state.wins += 1
                        else:
                            self.state.losses += 1
                        self.state.closed_trades.append({
                            **trade.to_dict(),
                            "exit_price": ltp,
                            "exit_time": datetime.now().strftime("%H:%M:%S"),
                            "pnl": round(pnl, 2),
                            "reason": reason,
                        })
                        kind = "sell" if exit_action == "SELL" else "buy"
                        self._log(f"EXIT {symbol} [{reason}]  P&L=₹{pnl:+.2f}", kind)
                        self.pm.remove(symbol)
                        self._emit("positions", [t.to_dict() for t in self.pm.trades.values()])
                        self._emit("summary", self.state.to_summary())
                        time.sleep(self.cfg.order_delay_sec)

                watchlist_snap.append({
                    "symbol": symbol, "ltp": ltp,
                    "signal": "HOLD", "rsi": None, "macd_hist": None,
                    "in_position": True,
                })
                continue

            # Get signal
            df = self.client.get_history(symbol, self.cfg.candle_interval)
            if df.empty:
                self._log(f"No history for {symbol}", "warn")
                continue

            analysis = self.signals.analyse(df)
            sig  = analysis["signal"]
            ltp  = float(df["close"].iloc[-1])

            watchlist_snap.append({
                "symbol": symbol, "ltp": round(ltp, 2),
                "signal": sig,
                "rsi":    analysis["rsi"],
                "macd_hist": analysis["macd_hist"],
                "in_position": False,
            })

            self._log(f"{symbol:<12} ₹{ltp:.2f}  RSI={analysis['rsi']}  Signal={sig}")

            if sig == "HOLD":
                continue
            if self.pm.count() >= self.cfg.max_open_trades:
                self._log(f"Max trades reached — skipping {symbol}", "warn")
                continue

            qty = self.signals.calc_qty(ltp, self.cfg.capital_per_trade)
            if qty == 0:
                continue

            sl_pct = self.cfg.stop_loss_pct / 100
            tp_pct = self.cfg.take_profit_pct / 100
            if sig == "BUY":
                sl = round(ltp * (1 - sl_pct), 2)
                tp = round(ltp * (1 + tp_pct), 2)
            else:
                sl = round(ltp * (1 + sl_pct), 2)
                tp = round(ltp * (1 - tp_pct), 2)

            oid = self.client.place_order(symbol, sig, qty)
            if oid:
                trade = OpenTrade(
                    symbol=symbol, action=sig, qty=qty,
                    entry_price=ltp, stop_loss=sl,
                    take_profit=tp, order_id=oid,
                )
                self.pm.add(trade)
                kind = "buy" if sig == "BUY" else "sell"
                self._log(f"{sig} {symbol} x{qty}  SL=₹{sl}  TP=₹{tp}", kind)
                self._emit("positions", [t.to_dict() for t in self.pm.trades.values()])

        # Emit watchlist update
        self.state.watchlist = watchlist_snap
        self._emit("watchlist", watchlist_snap)
        self.state.total_pnl = self.pm.total_pnl()
        self._emit("summary", self.state.to_summary())

    def squareoff(self):
        self._log("🔔 EOD square-off triggered", "warn")
        if self.pm.count() > 0:
            ok = self.client.close_all_positions()
            if ok:
                self._log("All positions closed (EOD)", "info")
                self.pm.trades.clear()
                self._emit("positions", [])
            else:
                self._log("ClosePosition API failed — check manually!", "error")

    def start_loop(self):
        funds = self.client.get_funds()
        if funds:
            self.state.available_cash = float(funds.get("availablecash", 0))
            self.state.connected = True
            self._log(f"Connected to OpenAlgo  |  Cash: ₹{self.state.available_cash:,.2f}")
        else:
            self.state.connected = False
            self._log("Cannot connect to OpenAlgo — check server", "error")

        self.state.running = True
        schedule.every(5).minutes.do(self.run_cycle)
        schedule.every().day.at(
            self.cfg.squareoff_at.strftime("%H:%M")
        ).do(self.squareoff)

        self.run_cycle()
        while self.state.running:
            schedule.run_pending()
            time.sleep(10)

    def stop(self):
        self.state.running = False
        self.squareoff()
        schedule.clear()


# ── Flask App ────────────────────────────────────────────────────────────────
app     = Flask(__name__)
app.config["SECRET_KEY"] = "openalgo_dashboard_secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

bot: Optional[TradingBot] = None
bot_thread: Optional[threading.Thread] = None


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/summary")
def api_summary():
    return jsonify(STATE.to_summary())


@app.route("/api/positions")
def api_positions():
    if bot:
        return jsonify([t.to_dict() for t in bot.pm.trades.values()])
    return jsonify([])


@app.route("/api/watchlist")
def api_watchlist():
    return jsonify(STATE.watchlist)


@app.route("/api/log")
def api_log():
    return jsonify(STATE.activity_log[-100:])


@app.route("/api/closed_trades")
def api_closed():
    return jsonify(STATE.closed_trades[-50:])


@app.route("/api/bot/start", methods=["POST"])
def api_start():
    global bot, bot_thread
    if STATE.running:
        return jsonify({"ok": False, "msg": "Bot already running"})

    cfg = CONFIG
    data = request.json or {}
    if "symbols" in data:
        cfg.symbols = [s.strip().upper() for s in data["symbols"].split(",")]
    if "capital_per_trade" in data:
        cfg.capital_per_trade = float(data["capital_per_trade"])
    if "stop_loss_pct" in data:
        cfg.stop_loss_pct = float(data["stop_loss_pct"])
    if "take_profit_pct" in data:
        cfg.take_profit_pct = float(data["take_profit_pct"])
    if "candle_interval" in data:
        cfg.candle_interval = data["candle_interval"]

    bot = TradingBot(cfg, STATE, socketio)
    bot_thread = threading.Thread(target=bot.start_loop, daemon=True)
    bot_thread.start()
    return jsonify({"ok": True, "msg": "Bot started"})


@app.route("/api/bot/stop", methods=["POST"])
def api_stop():
    if bot:
        bot.stop()
    STATE.running = False
    return jsonify({"ok": True, "msg": "Bot stopped"})


@app.route("/api/bot/squareoff", methods=["POST"])
def api_squareoff():
    if bot:
        threading.Thread(target=bot.squareoff, daemon=True).start()
    return jsonify({"ok": True, "msg": "Square-off triggered"})


@socketio.on("connect")
def on_connect():
    emit("summary",   STATE.to_summary())
    emit("watchlist", STATE.watchlist)
    emit("log",       STATE.activity_log[-50:])
    if bot:
        emit("positions", [t.to_dict() for t in bot.pm.trades.values()])


# ── HTML Dashboard Template ───────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenAlgo Bot Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root {
    --bg:       #0a0d0f;
    --surface:  #111518;
    --border:   rgba(255,255,255,0.07);
    --border2:  rgba(255,255,255,0.13);
    --text:     #e8eaec;
    --muted:    #6b7580;
    --accent:   #00d4a0;
    --accent2:  #0099ff;
    --red:      #ff4d4d;
    --yellow:   #f5c518;
    --mono:     'JetBrains Mono', monospace;
    --sans:     'Syne', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 14px; }
  body { min-height: 100vh; display: flex; flex-direction: column; }

  /* NAV */
  nav {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 28px; border-bottom: 1px solid var(--border);
    background: var(--surface); position: sticky; top: 0; z-index: 100;
  }
  .nav-brand { display: flex; align-items: center; gap: 10px; }
  .nav-logo {
    width: 32px; height: 32px; background: var(--accent);
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
  }
  .nav-logo svg { width: 18px; height: 18px; }
  .brand-name { font-size: 16px; font-weight: 700; letter-spacing: -0.02em; }
  .brand-sub  { font-size: 11px; color: var(--muted); font-family: var(--mono); }
  .nav-status { display: flex; align-items: center; gap: 8px; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .status-dot.live { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: blink 1.5s infinite; }
  .status-dot.error { background: var(--red); }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .status-label { font-size: 12px; font-family: var(--mono); color: var(--muted); }
  .mkt-time { font-family: var(--mono); font-size: 12px; color: var(--muted); padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px; }

  /* LAYOUT */
  .main { display: grid; grid-template-columns: 1fr 340px; gap: 0; flex: 1; }
  .left  { padding: 20px 24px; display: flex; flex-direction: column; gap: 18px; border-right: 1px solid var(--border); }
  .right { padding: 20px 20px; display: flex; flex-direction: column; gap: 16px; background: var(--surface); }

  /* METRICS */
  .metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .metric {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px;
  }
  .metric-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; font-family: var(--mono); }
  .metric-val   { font-size: 22px; font-weight: 700; letter-spacing: -0.03em; }
  .metric-sub   { font-size: 11px; color: var(--muted); margin-top: 3px; font-family: var(--mono); }
  .up   { color: var(--accent) !important; }
  .down { color: var(--red)    !important; }
  .neutral { color: var(--text) !important; }

  /* SECTION TITLE */
  .section-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); font-family: var(--mono); }

  /* WATCHLIST TABLE */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
  thead th {
    text-align: left; padding: 8px 12px; font-size: 10px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid var(--border); font-weight: 400;
  }
  tbody tr { border-bottom: 1px solid var(--border); transition: background 0.12s; }
  tbody tr:hover { background: rgba(255,255,255,0.02); }
  tbody td { padding: 10px 12px; }
  .sym-name { font-family: var(--sans); font-weight: 600; font-size: 13px; }
  .pill {
    display: inline-block; padding: 2px 9px; border-radius: 20px;
    font-size: 10px; font-weight: 600; letter-spacing: 0.05em;
  }
  .pill-buy  { background: rgba(0,212,160,0.15); color: var(--accent); }
  .pill-sell { background: rgba(255,77,77,0.15);  color: var(--red); }
  .pill-hold { background: rgba(255,255,255,0.07); color: var(--muted); }

  /* P&L CHART */
  .chart-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px 18px;
  }
  .chart-wrap { position: relative; height: 180px; }

  /* POSITIONS */
  .pos-list { display: flex; flex-direction: column; gap: 8px; }
  .pos-card {
    background: rgba(255,255,255,0.03); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 14px;
    display: flex; justify-content: space-between; align-items: center;
  }
  .pos-sym  { font-weight: 600; font-size: 13px; }
  .pos-meta { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-top: 2px; }
  .pos-pnl  { font-family: var(--mono); font-size: 14px; font-weight: 600; }

  /* CONTROLS */
  .ctrl-card {
    background: rgba(255,255,255,0.02); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px;
  }
  .form-row { display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; }
  .form-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; font-family: var(--mono); }
  input[type=text], input[type=number], select {
    background: rgba(255,255,255,0.04); border: 1px solid var(--border);
    border-radius: 6px; padding: 7px 10px; color: var(--text);
    font-family: var(--mono); font-size: 12px; width: 100%;
    outline: none; transition: border-color 0.15s;
  }
  input:focus, select:focus { border-color: var(--accent); }
  select option { background: #1a1e22; }
  .range-row { display: flex; align-items: center; gap: 8px; }
  input[type=range] { flex: 1; accent-color: var(--accent); }
  .range-val { font-family: var(--mono); font-size: 12px; color: var(--accent); min-width: 42px; text-align: right; }
  .btn {
    width: 100%; padding: 10px; border-radius: 7px; font-family: var(--sans);
    font-size: 13px; font-weight: 600; cursor: pointer; border: none;
    transition: opacity 0.15s, transform 0.1s;
  }
  .btn:hover { opacity: 0.88; }
  .btn:active { transform: scale(0.98); }
  .btn-start { background: var(--accent); color: #000; }
  .btn-stop  { background: var(--red);   color: #fff; }
  .btn-sq    { background: transparent; border: 1px solid var(--border2); color: var(--text); margin-top: 6px; }

  /* ACTIVITY LOG */
  .log-box {
    background: #070a0c; border: 1px solid var(--border);
    border-radius: 8px; padding: 12px; font-family: var(--mono); font-size: 11px;
    height: 220px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
  }
  .log-box::-webkit-scrollbar { width: 4px; }
  .log-box::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }
  .log-entry { display: flex; gap: 8px; }
  .log-ts    { color: #3a4550; flex-shrink: 0; }
  .log-msg   { color: var(--muted); }
  .log-buy   .log-msg { color: var(--accent); }
  .log-sell  .log-msg { color: var(--red); }
  .log-warn  .log-msg { color: var(--yellow); }
  .log-error .log-msg { color: var(--red); }

  /* RESPONSIVE */
  @media (max-width: 900px) {
    .main { grid-template-columns: 1fr; }
    .right { border-top: 1px solid var(--border); }
    .metrics { grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 600px) {
    .metrics { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">
    <div class="nav-logo">
      <svg viewBox="0 0 18 18" fill="none">
        <polyline points="1,14 6,7 10,10 15,3" stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="12,3 15,3 15,6" stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </div>
    <div>
      <div class="brand-name">OpenAlgo Bot</div>
      <div class="brand-sub">NSE / BSE Auto-Trader</div>
    </div>
  </div>
  <div class="nav-status">
    <div class="status-dot" id="statusDot"></div>
    <span class="status-label" id="statusLabel">DISCONNECTED</span>
  </div>
  <div class="mkt-time" id="mktTime">--:--:-- IST</div>
</nav>

<div class="main">
  <!-- LEFT PANEL -->
  <div class="left">

    <!-- METRICS -->
    <div class="metrics">
      <div class="metric">
        <div class="metric-label">Available Cash</div>
        <div class="metric-val neutral" id="mCash">₹0</div>
        <div class="metric-sub">in account</div>
      </div>
      <div class="metric">
        <div class="metric-label">Unrealised P&L</div>
        <div class="metric-val" id="mPnl">₹0</div>
        <div class="metric-sub" id="mPnlSub">today</div>
      </div>
      <div class="metric">
        <div class="metric-label">Open Trades</div>
        <div class="metric-val neutral" id="mOpen">0</div>
        <div class="metric-sub" id="mOpenSub">of 4 max</div>
      </div>
      <div class="metric">
        <div class="metric-label">Win Rate</div>
        <div class="metric-val" id="mWin">—</div>
        <div class="metric-sub" id="mWinSub">0 trades</div>
      </div>
      <div class="metric">
        <div class="metric-label">Signals Today</div>
        <div class="metric-val neutral" id="mSigs">0</div>
        <div class="metric-sub" id="mSigSub">buy / sell</div>
      </div>
    </div>

    <!-- WATCHLIST -->
    <div>
      <div class="section-head">
        <span class="section-title">AI Watchlist</span>
        <span class="section-title" id="scanTime">last scan: —</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Symbol</th><th>LTP</th><th>RSI</th><th>MACD Hist</th><th>Signal</th><th>Position</th>
          </tr></thead>
          <tbody id="watchBody"><tr><td colspan="6" style="color:var(--muted);padding:20px;text-align:center;">Waiting for first scan...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- P&L CHART -->
    <div class="chart-card">
      <div class="section-head" style="margin-bottom:10px;">
        <span class="section-title">P&L History</span>
        <span class="section-title" id="chartSub">live session</span>
      </div>
      <div class="chart-wrap">
        <canvas id="pnlChart" role="img" aria-label="Live session P&L chart">Live P&L tracking chart.</canvas>
      </div>
    </div>

    <!-- CLOSED TRADES -->
    <div>
      <div class="section-head">
        <span class="section-title">Closed Trades</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Symbol</th><th>Action</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th>
          </tr></thead>
          <tbody id="closedBody"><tr><td colspan="7" style="color:var(--muted);padding:20px;text-align:center;">No closed trades yet</td></tr></tbody>
        </table>
      </div>
    </div>

  </div>

  <!-- RIGHT PANEL -->
  <div class="right">

    <!-- BOT CONTROLS -->
    <div class="ctrl-card">
      <div class="section-title" style="margin-bottom:14px;">Bot Controls</div>
      <div class="form-row">
        <div class="form-label">Symbols (comma-separated)</div>
        <input type="text" id="cfgSymbols" value="RELIANCE,INFY,TCS,HDFCBANK,SBIN,WIPRO">
      </div>
      <div class="form-row">
        <div class="form-label">Strategy</div>
        <select id="cfgStrategy">
          <option value="5m">Momentum + RSI (5m)</option>
          <option value="15m">Momentum + RSI (15m)</option>
          <option value="1m">Momentum + RSI (1m)</option>
        </select>
      </div>
      <div class="form-row">
        <div class="form-label">Capital per Trade (₹)</div>
        <div class="range-row">
          <input type="range" min="5000" max="100000" step="5000" value="25000" id="cfgCap"
            oninput="document.getElementById('cfgCapVal').textContent='₹'+Number(this.value).toLocaleString('en-IN')">
          <span class="range-val" id="cfgCapVal">₹25,000</span>
        </div>
      </div>
      <div class="form-row">
        <div class="form-label">Stop Loss %</div>
        <div class="range-row">
          <input type="range" min="0.5" max="5" step="0.5" value="2" id="cfgSL"
            oninput="document.getElementById('cfgSLVal').textContent=this.value+'%'">
          <span class="range-val" id="cfgSLVal">2%</span>
        </div>
      </div>
      <div class="form-row">
        <div class="form-label">Take Profit %</div>
        <div class="range-row">
          <input type="range" min="1" max="10" step="0.5" value="4" id="cfgTP"
            oninput="document.getElementById('cfgTPVal').textContent=this.value+'%'">
          <span class="range-val" id="cfgTPVal">4%</span>
        </div>
      </div>
      <button class="btn btn-start" id="startBtn" onclick="startBot()">▶  Start Bot</button>
      <button class="btn btn-sq" onclick="squareOff()">⏹  Square Off All</button>
    </div>

    <!-- OPEN POSITIONS -->
    <div>
      <div class="section-title" style="margin-bottom:10px;">Open Positions</div>
      <div class="pos-list" id="positionsList">
        <div style="color:var(--muted);font-size:12px;font-family:var(--mono);padding:8px 0;">No open positions</div>
      </div>
    </div>

    <!-- ACTIVITY LOG -->
    <div>
      <div class="section-head">
        <span class="section-title">Activity Log</span>
        <span class="section-title" style="cursor:pointer;" onclick="clearLog()">clear</span>
      </div>
      <div class="log-box" id="logBox"></div>
    </div>

  </div>
</div>

<script>
const socket = io();
let running = false;
let buySigs = 0, sellSigs = 0;
const pnlHistory = { labels: [], data: [] };
let pnlChart;

// ── Clock ────────────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  const t = now.toLocaleTimeString('en-IN', { hour12: false });
  document.getElementById('mktTime').textContent = t + ' IST';
}
setInterval(updateClock, 1000);
updateClock();

// ── P&L Chart ────────────────────────────────────────────────────────────
function initChart() {
  const ctx = document.getElementById('pnlChart').getContext('2d');
  pnlChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: pnlHistory.labels,
      datasets: [{
        data: pnlHistory.data,
        borderColor: '#00d4a0',
        backgroundColor: 'rgba(0,212,160,0.06)',
        fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#6b7580', font: { size: 10, family: 'JetBrains Mono' }, maxTicksLimit: 6 }, grid: { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#6b7580', font: { size: 10, family: 'JetBrains Mono' }, callback: v => '₹' + v.toLocaleString('en-IN') }, grid: { color: 'rgba(255,255,255,0.04)' } }
      }
    }
  });
}

function updateChart(pnl) {
  const now = new Date().toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit' });
  pnlHistory.labels.push(now);
  pnlHistory.data.push(parseFloat(pnl.toFixed(2)));
  if (pnlHistory.labels.length > 80) { pnlHistory.labels.shift(); pnlHistory.data.shift(); }
  pnlChart.update('none');
}

// ── Summary update ───────────────────────────────────────────────────────
socket.on('summary', d => {
  running = d.running;
  const dot = document.getElementById('statusDot');
  const lbl = document.getElementById('statusLabel');
  if (!d.connected) {
    dot.className = 'status-dot error'; lbl.textContent = 'DISCONNECTED';
  } else if (d.running) {
    dot.className = 'status-dot live'; lbl.textContent = 'BOT RUNNING';
  } else {
    dot.className = 'status-dot'; lbl.textContent = 'CONNECTED · IDLE';
  }

  const btn = document.getElementById('startBtn');
  if (d.running) {
    btn.textContent = '⏸  Stop Bot'; btn.className = 'btn btn-stop';
    btn.onclick = stopBot;
  } else {
    btn.textContent = '▶  Start Bot'; btn.className = 'btn btn-start';
    btn.onclick = startBot;
  }

  const cash = d.available_cash;
  document.getElementById('mCash').textContent = '₹' + cash.toLocaleString('en-IN', { maximumFractionDigits: 0 });

  const pnl = d.total_pnl;
  const pnlEl = document.getElementById('mPnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '₹' + Math.abs(pnl).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  pnlEl.className = 'metric-val ' + (pnl > 0 ? 'up' : pnl < 0 ? 'down' : 'neutral');

  document.getElementById('mOpen').textContent = d.running ? (document.getElementById('positionsList').querySelectorAll('.pos-card').length) : '0';
  const wr = d.win_rate;
  const wrEl = document.getElementById('mWin');
  wrEl.textContent = d.total_trades ? wr + '%' : '—';
  wrEl.className = 'metric-val ' + (wr >= 50 ? 'up' : wr > 0 ? 'down' : 'neutral');
  document.getElementById('mWinSub').textContent = d.total_trades + ' trades';
  document.getElementById('mSigSub').textContent = buySigs + ' buy · ' + sellSigs + ' sell';

  updateChart(pnl);
});

// ── Watchlist ────────────────────────────────────────────────────────────
socket.on('watchlist', list => {
  document.getElementById('scanTime').textContent = 'last scan: ' + new Date().toLocaleTimeString('en-IN', { hour12: false });
  const tbody = document.getElementById('watchBody');
  if (!list.length) return;
  buySigs  = list.filter(r => r.signal === 'BUY').length;
  sellSigs = list.filter(r => r.signal === 'SELL').length;
  document.getElementById('mSigs').textContent = buySigs + sellSigs;
  tbody.innerHTML = list.map(r => `
    <tr>
      <td><span class="sym-name">${r.symbol}</span></td>
      <td>₹${(r.ltp||0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
      <td style="font-family:var(--mono); color:${r.rsi < 35 ? 'var(--accent)' : r.rsi > 65 ? 'var(--red)' : 'var(--muted)'}">${r.rsi != null ? r.rsi : '—'}</td>
      <td style="font-family:var(--mono); color:${r.macd_hist > 0 ? 'var(--accent)' : r.macd_hist < 0 ? 'var(--red)' : 'var(--muted)'}">${r.macd_hist != null ? r.macd_hist : '—'}</td>
      <td><span class="pill pill-${r.signal.toLowerCase()}">${r.signal}</span></td>
      <td style="font-family:var(--mono);color:var(--muted)">${r.in_position ? '● OPEN' : '—'}</td>
    </tr>`).join('');
});

// ── Positions ────────────────────────────────────────────────────────────
socket.on('positions', list => {
  document.getElementById('mOpen').textContent = list.length;
  const cont = document.getElementById('positionsList');
  if (!list.length) {
    cont.innerHTML = '<div style="color:var(--muted);font-size:12px;font-family:var(--mono);padding:8px 0;">No open positions</div>';
    return;
  }
  cont.innerHTML = list.map(p => {
    const pnl = p.unrealised_pnl;
    return `<div class="pos-card">
      <div>
        <div class="pos-sym">${p.symbol}</div>
        <div class="pos-meta">${p.action} · ${p.qty} qty · In @ ₹${p.entry_price} · ${p.entry_time}</div>
        <div class="pos-meta" style="margin-top:2px;">SL ₹${p.stop_loss} | TP ₹${p.take_profit}</div>
      </div>
      <div class="pos-pnl ${pnl >= 0 ? 'up' : 'down'}">${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toFixed(2)}</div>
    </div>`;
  }).join('');
});

// ── Log ──────────────────────────────────────────────────────────────────
function appendLog(entry) {
  const box = document.getElementById('logBox');
  const arr = Array.isArray(entry) ? entry : [entry];
  arr.forEach(e => {
    const d = document.createElement('div');
    d.className = 'log-entry log-' + (e.kind || 'info');
    d.innerHTML = `<span class="log-ts">${e.time}</span><span class="log-msg">${e.msg}</span>`;
    box.appendChild(d);
  });
  box.scrollTop = box.scrollHeight;
  if (box.children.length > 150) box.removeChild(box.firstChild);
}
socket.on('log', entry => appendLog(entry));

function clearLog() { document.getElementById('logBox').innerHTML = ''; }

// ── Closed trades ────────────────────────────────────────────────────────
function refreshClosed() {
  fetch('/api/closed_trades').then(r => r.json()).then(list => {
    const tbody = document.getElementById('closedBody');
    if (!list.length) return;
    tbody.innerHTML = [...list].reverse().map(t => {
      const pnl = t.pnl;
      return `<tr>
        <td class="sym-name">${t.symbol}</td>
        <td><span class="pill pill-${t.action.toLowerCase()}">${t.action}</span></td>
        <td style="font-family:var(--mono)">${t.qty}</td>
        <td style="font-family:var(--mono)">₹${t.entry_price}</td>
        <td style="font-family:var(--mono)">₹${t.exit_price}</td>
        <td style="font-family:var(--mono)" class="${pnl >= 0 ? 'up' : 'down'}">${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toFixed(2)}</td>
        <td style="font-family:var(--mono);color:var(--muted)">${t.reason}</td>
      </tr>`;
    }).join('');
  });
}
setInterval(refreshClosed, 10000);

// ── Bot actions ──────────────────────────────────────────────────────────
function startBot() {
  const payload = {
    symbols:           document.getElementById('cfgSymbols').value,
    candle_interval:   document.getElementById('cfgStrategy').value,
    capital_per_trade: document.getElementById('cfgCap').value,
    stop_loss_pct:     document.getElementById('cfgSL').value,
    take_profit_pct:   document.getElementById('cfgTP').value,
  };
  fetch('/api/bot/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) })
    .then(r => r.json()).then(d => appendLog({ time: new Date().toLocaleTimeString('en-IN',{hour12:false}), msg: d.msg, kind: 'info' }));
}

function stopBot() {
  fetch('/api/bot/stop', { method: 'POST' })
    .then(r => r.json()).then(d => appendLog({ time: new Date().toLocaleTimeString('en-IN',{hour12:false}), msg: d.msg, kind: 'warn' }));
}

function squareOff() {
  if (!confirm('Square off ALL open positions now?')) return;
  fetch('/api/bot/squareoff', { method: 'POST' })
    .then(r => r.json()).then(d => appendLog({ time: new Date().toLocaleTimeString('en-IN',{hour12:false}), msg: d.msg, kind: 'warn' }));
}

// ── Init ─────────────────────────────────────────────────────────────────
initChart();
fetch('/api/summary').then(r=>r.json()).then(d => socket.emit('summary', d));
</script>
</body>
</html>"""

import os

def create_templates():
    os.makedirs("templates", exist_ok=True)
    with open("templates/dashboard.html", "w", encoding="utf-8") as f:
        f.write(DASHBOARD_HTML)


if __name__ == "__main__":
    create_templates()

    log.info("=" * 55)
    log.info("  OpenAlgo Bot  —  Dashboard at http://127.0.0.1:8080")
    log.info("=" * 55)

    # Run SocketIO / Flask server (bot starts via dashboard UI)
    socketio.run(
        app,
        host=CONFIG.dashboard_host,
        port=CONFIG.dashboard_port,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
