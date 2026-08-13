# BOT-Antigravity — Architecture & Working

> **UT Bot Antigravity** is a Python algorithmic trading bot that implements the classic *UT Bot Alerts* strategy (a ratcheting ATR trailing-stop ported from PineScript v5), enriched with an optional XGBoost ML signal filter and full auto-trading via the [OpenAlgo](https://openalgo.in) broker API.

---

## 1. Repository Layout

```
BOT-Antigravity/
├── app.py              ← Main bot: strategy engine + threading + order placement
├── server.py           ← FastAPI dashboard server (wraps app.py's engine as background threads)
├── signal_logger.py    ← Captures every signal with 14 features to SQLite
├── label_signals.py    ← Offline script: grades each signal WIN/LOSS after the fact
├── ml_filter.py        ← XGBoost classifier: training (CLI) + inference (runtime)
├── telegram.py         ← Sends Telegram alerts (direct API or via OpenAlgo server)
├── trade_management/   ← SL/target/trailing-SL/profit-lock/partial-exit monitoring (see §13)
│   ├── models.py        ← Shared dataclasses + gain/SL/target/lots/P&L helpers
│   ├── rules_engine.py  ← Pure decision logic (no side effects)
│   ├── executor.py      ← Order placement + trade_db writes for each decision
│   ├── alerts.py        ← Telegram notifications for position lifecycle events
│   └── monitor.py       ← PositionMonitor: WS/polling loop, position registry
├── trade_db.py          ← SQLite persistence for trade_management (trades.db)
├── trading_adapter.py   ← Slim OpenAlgo-only place_order()/get_ltp() used by trade_management
├── instrument_master.py ← Options metadata (lot size/strike/expiry) via instruments_cache.pkl
├── config.yml          ← Single source of truth for all parameters
├── signals.db          ← SQLite database (created at runtime)
├── trades.db           ← Trade-management positions + audit log (created at runtime)
├── ml_model.pkl        ← Trained model (created after running --train)
├── utbot.log           ← Rolling application log (created at runtime)
├── .utbot.lock         ← PID lock file (prevents duplicate instances)
├── utbot-pinescript.txt← Original PineScript for reference
├── AGENTS.md           ← Agent / developer guidelines
└── ARCHITECTURE.md     ← This file
```

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                           app.py  (main)                        │
│                                                                 │
│  ┌──────────────────┐   shared        ┌──────────────────────┐  │
│  │ LivePriceMonitor │──── ltp_map ───▶│  TimeframeWorker×N   │  │
│  │  (WS thread)     │                 │  one per             │  │
│  │                  │                 │  (symbol, timeframe) │  │
│  │  OpenAlgo WS     │                 │                      │  │
│  │  subscribe_ltp() │                 │  _check_and_alert()  │  │
│  └──────────────────┘                 └──────────┬───────────┘  │
│                                                  │              │
│                          ┌───────────────────────▼──────────┐  │
│                          │  compute_utbot_signals()          │  │
│                          │  ATR → nLoss → xATRTrailingStop   │  │
│                          │  → BUY / SELL crossover           │  │
│                          └───────────────────────┬──────────┘  │
│                                                  │              │
│          ┌───────────────────────────────────────▼──────────┐  │
│          │  Signal Pipeline (on every new signal)            │  │
│          │                                                   │  │
│          │  1. extract_features()  (signal_logger.py)        │  │
│          │  2. log_signal()   → signals.db                   │  │
│          │  3. MLFilter.should_fire()  (ml_filter.py)        │  │
│          │  4. _place_order() → OpenAlgo REST API            │  │
│          │  5. send_telegram_alert()  (telegram.py)          │  │
│          └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Offline ML workflow (run manually after market close):
  label_signals.py  →  ml_filter.py --train  →  ml_model.pkl
```

---

## 3. Strategy Engine — UT Bot Algorithm

The core strategy is a faithful Python port of the PineScript *UT Bot Alerts* indicator.

### Step-by-step

| Step | What happens |
|------|-------------|
| **1** | Compute **True Range** per bar: `max(H-L, |H-prevC|, |L-prevC|)` |
| **2** | Smooth TR into **ATR** using Wilder's RMA: `ewm(alpha=1/atr_period)` |
| **3** | `nLoss = key_value × ATR` — the sensitivity band |
| **4** | Build **xATRTrailingStop** (ratcheting stop) iteratively: |
|       | → price rising above stop → stop ratchets UP: `max(prev_stop, price − nLoss)` |
|       | → price falling below stop → stop ratchets DOWN: `min(prev_stop, price + nLoss)` |
|       | → price flips side → stop resets to `price ± nLoss` |
| **5** | Track **position** (+1 / -1 / 0) from stop crossovers |
| **6** | **BUY** = `close > xATRStop` AND `EMA(1) crosses above xATRStop` |
| **7** | **SELL** = `close < xATRStop` AND `xATRStop crosses above EMA(1)` |

> `EMA(1)` of close = close itself (span-1 EMA degenerates to the source). This exactly mirrors the PineScript behaviour.

### Configurable parameters (`strategy:` section in `config.yml`)

| Parameter | Default | Effect |
|-----------|---------|--------|
| `key_value` | `2` | ATR multiplier — higher = wider bands, fewer signals |
| `atr_period` | `1` | ATR lookback — 1 = very responsive, 14 = smooth |
| `use_heikin_ashi` | `false` | Use HA close as source price |

---

## 4. Component Deep-Dive

### 4.1 `app.py` — The Orchestrator

**Startup sequence:**

1. Load `config.yml`
2. Acquire `.utbot.lock` (single-instance guard)
3. Create `openalgo.api` client
4. Start `LivePriceMonitor` thread (WebSocket LTP stream)
5. Spawn one `TimeframeWorker` thread per `(symbol, timeframe)` pair
6. Enter main loop — watches `config.yml` for changes and hot-reloads if it is saved

**`TimeframeWorker` inner loop (every `signal_check_interval` seconds):**

```
every N seconds
    └─ _is_market_hours()?  → skip if outside window
    └─ _current_boundary()  → which candle bar are we in now?
    └─ same boundary as last fetch?  → skip (no new bar yet)
    └─ _fetch_history()     → pull OHLCV from broker / yfinance / etc.
    └─ compute_utbot_signals()
    └─ identify last CLOSED candle (handles API lag)
    └─ BUY or SELL on that candle?
        └─ deduplicate (same bar + same type already fired?)
        └─ extract_features()
        └─ log_signal()  → signals.db
        └─ MLFilter.should_fire()?  → suppress if below threshold
        └─ _place_order() via OpenAlgo
        └─ send_telegram_alert()
```

**Candle boundary caching** is a key optimisation — the bot computes `floor(epoch_seconds / candle_seconds)` to get the current candle's start timestamp and only calls the broker REST API when that value changes. This eliminates redundant calls within the same bar.

**Dynamic closed-candle detection:**
```python
if last_bar_ts >= boundary_naive:
    closed_idx = -2   # last bar is still forming → use second-to-last
else:
    closed_idx = -1   # API lagged → all returned bars are closed
```

---

### 4.2 `LivePriceMonitor` — Real-time Prices

- Opens one **WebSocket** connection to the OpenAlgo server (`ws://127.0.0.1:8765`)
- Subscribes to LTP for **all instruments** (equity + options) in a single connection
- Polls the SDK's internal `ltp_data` store every second and writes into a shared `ltp_map` dict
- Auto-reconnects with a 5-second back-off on disconnection
- Workers read `ltp_map[symbol]` to get live price for LIMIT orders and Telegram messages

---

### 4.3 `signal_logger.py` — Feature Store

Every detected signal (before ML filtering) is persisted to `signals.db` with:

| Feature | Description |
|---------|-------------|
| `atr_pct` | ATR as % of close price |
| `volume_ratio` | Current volume / 20-bar average |
| `rsi_14` | Wilder RSI(14) at signal bar |
| `ema20_dist_pct` | Distance from EMA(20) as % |
| `candle_body_pct` | Body size / total range |
| `atr_percentile` | ATR rank among last 20 ATR values |
| `hour`, `minute` | Time of day |
| `day_of_week` | 0=Mon … 4=Fri |

Plus raw values: `close`, `atr`, `n_loss`, `atr_stop`, `volume`.

---

### 4.4 `label_signals.py` — Outcome Labeling (offline)

Run **after market close** to grade past signals:

```bash
python label_signals.py           # label all pending
python label_signals.py --status  # show DB summary
python label_signals.py --dry-run # preview without writing
```

**Logic:**
- For each unlabeled signal, fetch candle data covering the post-signal window
- Compute % price change at 5 and 10 candles after entry
- **WIN** if the move ≥ `win_threshold_pct` in the correct direction
- Writes `label_5`, `label_10`, `outcome_5`, `outcome_10` into `signals.db`

---

### 4.5 `ml_filter.py` — XGBoost Signal Classifier

#### Training (CLI)
```bash
python ml_filter.py --train                      # default: label_5
python ml_filter.py --train --label label_10     # use 10-candle outcome
python ml_filter.py --report                     # DB + model stats
python ml_filter.py --importance                 # feature importance chart
```

Trains a `XGBClassifier` (150 estimators, max_depth=4, learning_rate=0.05) on labeled rows and saves to `ml_model.pkl`.  Column medians are attached to the pickle for NaN imputation at inference time.

#### Inference (runtime)
```python
fired, confidence = ml_filter.should_fire(features, signal_type)
# confidence < threshold → signal suppressed (not sent to Telegram or broker)
```

**Pass-through mode:** if `ml_model.pkl` does not exist, `MLFilter.is_ready()` returns `False` and every signal is allowed through — the bot works fully without a trained model.

---

### 4.6 `telegram.py` — Notifications

Two delivery modes, set via `telegram.mode` in `config.yml`:

| Mode | Transport |
|------|-----------|
| `direct` | Calls `https://api.telegram.org/bot<token>/sendMessage` directly |
| `openalgo` | Routes through OpenAlgo server's `/api/v1/telegram/notify` endpoint |

Message format (Markdown):
```
🟢 *Buy Signal ✅ 72%* — IOC on 5m chart
LTP        : 169.35
ATR Stop   : 166.12
Bar Close  : 168.90
Bar Closed : 2025-05-20 10:25
ML Confidence : 72%
📋 Order    : BUY 1 ✅ (id: 100234)
```

---

## 5. Data Sources

The bot can pull OHLCV history from four sources, selected via `data_source` in `config.yml`:

| Source | Notes |
|--------|-------|
| `openalgo` | Default. Uses OpenAlgo REST + WebSocket. Needed for auto-trading. |
| `yfinance` | Free. NSE symbols auto-suffixed with `.NS`. No live LTP. |
| `tvdatafeed` | TradingView feed. Free tier available. |
| `twelvedata` | API key required. |

> Only `openalgo` supports live LTP streaming and order placement. The other sources work for signal generation and Telegram alerts only.

---

## 6. Multi-Symbol / Multi-Timeframe Threading Model

```
main thread
  ├── Thread: WS-LivePrices          (LivePriceMonitor)
  ├── Thread: Worker-IOC-5m          (TimeframeWorker)
  ├── Thread: Worker-IOC-15m         (TimeframeWorker)
  ├── Thread: Worker-BANKINDIA-5m    (TimeframeWorker)
  ├── Thread: Worker-BANKINDIA-15m   (TimeframeWorker)
  └── ...
```

- All worker threads share a single `stop_event` (threading.Event)
- All workers read from the same `shared_ltp_map` (populated by the WS thread)
- Each worker has its own `_last_fetched_boundary` and `_last_signal_ts` state — no inter-thread locking needed for these
- The SQLite `signals.db` is accessed with `check_same_thread=False`; each write opens and immediately closes its own connection

---

## 7. Auto-Trading (Order Placement)

Controlled by a two-level hierarchy in `config.yml`:

```yaml
trading:
  enabled: true             # master switch
  strategy_name: "UTBot"   # tag sent to OpenAlgo
  equity:
    enabled: true
    quantity: 1
    product: "CNC"          # CNC / MIS / NRML
    price_type: "LIMIT"     # MARKET / LIMIT / SL / SL-M
  options:
    enabled: true
    quantity: 65
    product: "NRML"
    price_type: "LIMIT"
```

For `LIMIT` orders the bot passes the live LTP (from the WS feed) as the order price. If no live price is available it falls back to the bar's close price.

Exchange routing:
- Equity symbols → `exchange` (NSE)
- Option contract symbols → always `NFO` (hardcoded)

---

## 8. ML Workflow (end-to-end)

```
Phase 1 — Data Collection  (days 1–14)
┌──────────────────────────────────────────────┐
│  config.yml: ml.log_signals: true             │
│              ml.enabled: false                │
│  bot runs normally, every signal is logged   │
└──────────────────────────────────────────────┘

Phase 2 — Labeling  (after each trading day)
┌──────────────────────────────────────────────┐
│  python label_signals.py                      │
│  Grades each signal WIN(1) or LOSS(0)         │
│  Needs 5–10 candles of future data            │
└──────────────────────────────────────────────┘

Phase 3 — Training  (once you have 30+ labeled rows)
┌──────────────────────────────────────────────┐
│  python ml_filter.py --train                  │
│  Saves ml_model.pkl                           │
│  python ml_filter.py --report                 │
└──────────────────────────────────────────────┘

Phase 4 — Production  (ongoing)
┌──────────────────────────────────────────────┐
│  config.yml: ml.enabled: true                 │
│  confidence_threshold: 0.60                   │
│  Bot filters: only signals ≥60% confidence    │
│  Keep logging for periodic retraining         │
└──────────────────────────────────────────────┘
```

---

## 9. Safety & Reliability Features

| Feature | Implementation |
|---------|----------------|
| **Single-instance lock** | `.utbot.lock` stores PID; on startup checks if process is alive |
| **Market hours guard** | `_is_market_hours()` — Mon–Fri, 09:15–15:30 IST (configurable; can be disabled) |
| **Signal deduplication** | `_last_signal_ts` + `_last_signal_type` — same bar never fires twice |
| **API error handling** | All broker and HTTP calls wrapped in `try/except` with logged warnings |
| **WebSocket auto-reconnect** | 5-second retry loop in `LivePriceMonitor.run()` |
| **Hot config reload** | Main loop watches `config.yml` mtime and restarts all threads on save |
| **UTF-8 console fix** | Rewraps stdout/stderr on Windows to handle emojis without crashing |
| **Graceful shutdown** | `Ctrl+C` sets `stop_event`; all threads get up to 10 s to finish |

---

## 10. Configuration Reference (`config.yml`)

```yaml
data_source: "openalgo"       # openalgo | yfinance | twelvedata | tvdatafeed

telegram:
  mode: "direct"              # direct | openalgo
  bot_token: "..."
  chat_id: "..."

openalgo:
  apikey: "..."
  base_url: "http://127.0.0.1:5000"
  ws_url:   "ws://127.0.0.1:8765"

exchange: "NSE"
symbols:  [IOC, BANKINDIA, PNB, RPOWER]
timeframes: ["5m", "15m"]

index_exchange: "NSE_INDEX"   # display label only — NFO used internally
index_symbols: []             # option contract names (e.g. NIFTY...PE)
index_timeframes: ["5m"]

strategy:
  key_value: 2                # ATR multiplier
  atr_period: 1               # ATR lookback bars
  use_heikin_ashi: false

data:
  lookback_days: 5            # candles fetched per API call

bot:
  signal_check_interval: 5   # seconds between scans
  market_hours_check: false   # false = run 24/7
  market_open:  "09:15"
  market_close: "15:30"
  log_level: "INFO"

ml:
  log_signals: true           # always keep true
  enabled: false              # true once model is trained
  confidence_threshold: 0.60
  label_lookahead: 5          # candles for WIN/LOSS labeling
  win_threshold_pct: 0.3      # % move required for WIN label

trading:
  enabled: true
  strategy_name: "UTBot"
  equity:
    enabled: true
    quantity: 1
    product: "CNC"
    price_type: "LIMIT"
  options:
    enabled: true
    quantity: 65
    product: "NRML"
    price_type: "LIMIT"
```

---

## 11. Quick-Start Commands

```bash
# Run the bot
python app.py

# Label signals after market close
python label_signals.py
python label_signals.py --status
python label_signals.py --dry-run

# Train ML model
python ml_filter.py --train
python ml_filter.py --report
python ml_filter.py --importance

# Check current signal DB
python label_signals.py --status
```

---

## 12. Key Dependencies

| Package | Purpose |
|---------|---------|
| `openalgo` | Broker REST + WebSocket SDK |
| `pandas`, `numpy` | Data manipulation & ATR computation |
| `pyyaml` | Config file parsing |
| `requests` | Telegram HTTP calls |
| `xgboost` | ML model training (optional) |
| `scikit-learn` | Train/test split + metrics (optional) |
| `matplotlib` | Feature importance plot (optional) |
| `yfinance` | Alternative data source (optional) |
| `tvDatafeed` | TradingView data source (optional) |
| `twelvedata` | TwelveData source (optional) |

---

## 13. Trade Management (SL / Target / Trailing / Profit-Lock / Partial-Exit)

Ported from the sibling Bot-Stocks project's `trade_management/` package and adapted for
options (premium-based percentages, lot size, expiry-aware position metadata). Disabled by
default (`trade_management.enabled: false` in config.yml) — flip it on once you've dry-run
watched the SL/target percentages against real premium behaviour.

### 13.1 Why this hooks in differently than Bot-Stocks

Bot-Stocks places orders through a `/api/order` endpoint that a human triggers by clicking a
signal on the dashboard — trade-management registration happens inside that same request
handler. BOT-Options places orders **autonomously**: `TimeframeWorker._place_order()` (in
app.py) fires the moment a UT Bot signal crosses, from a background thread, with no human in
the loop. So there's no request to hang position registration off — instead,
`position_monitor.open_position(...)` is called **directly, in-process**, right after a
successful order inside `_place_order()`. `position_monitor` is a module-level singleton in
app.py; server.py starts/stops it on FastAPI startup/shutdown, independently of `BotEngine`
(so open positions stay monitored across `/api/bot/restart` config reloads).

### 13.2 Data flow

```
TimeframeWorker._place_order()  (app.py, background thread)
  └─ order succeeds
      └─ position_monitor.open_position(order_result, req, config, timeframe)
          ├─ resolve entry price (LTP/close, already computed pre-order)
          ├─ instrument_master.lookup(symbol) → underlying/strike/type/expiry/lot_size
          ├─ compute initial SL/target from trade_management.stop_loss_pct/target_pct
          │   (percentages of PREMIUM, not the underlying's price)
          └─ trade_db.open_position_db()  →  trades.db

PositionMonitor (WS ticks + HTTP-polling fallback)
  └─ rules_engine.evaluate(pos, ltp, tm_cfg)   — pure, no side effects
      └─ TradeAction(s): EXIT_TARGET / EXIT_SL / TRAILING_SL / PROFIT_LOCK / PARTIAL_EXIT
          └─ executor.dispatch(...)  — places exit/partial orders, updates trades.db,
                                        sends Telegram alerts, updates active_positions
```

### 13.3 Options-specific additions over the Bot-Stocks original

- **`instrument_master.py`** — resolves lot size/strike/expiry/underlying from
  `instruments_cache.pkl` (the OpenAlgo instrument master, one directory above this bot,
  shared with Bot-Stocks). Falls back to regex-parsing the standard
  `UNDERLYING+DDMMMYY+STRIKE+CE/PE` symbol format plus a small static lot-size table when a
  contract isn't in the cache. Lot sizes get revised by NSE periodically — refresh
  `instruments_cache.pkl` rather than trusting the fallback table long-term.
- **Rupee P&L** (`pnl_amount`, alongside the existing `pnl_pct`) — since `quantity` on a
  position is already total contract units (lot_size × lots, matching what OpenAlgo's
  `placeorder()` expects), rupee P&L is `(exit − entry) × quantity`; no extra lot-size
  multiplication needed. A `realized_pnl_amount` accumulator on the position tracks P&L
  correctly across a mix of partial exits followed by a final target/SL exit.
- **Expiry countdown** — `instrument_master.expiry_countdown()` returns a
  days/hours-left label plus an urgency bucket (`safe`/`near`/`today`/`expired`) consumed by
  `GET /api/positions` and rendered as a colour-coded badge on the dashboard's Positions tab.
- **`trading_adapter.py`** is a single-broker (OpenAlgo-only) subset of Bot-Stocks' multi-broker
  version, since BOT-Options only ever trades through OpenAlgo.

### 13.4 New API endpoints (server.py)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/positions` | Open positions enriched with live premium, unrealised P&L (%/₹), lots, expiry countdown |
| `GET /api/positions/closed` | Paginated closed-trade history |
| `GET /api/positions/{id}/events` | Full audit log for one position (opens, SL moves, exits) |
| `POST /api/positions/{id}/close` | Manually square off an open position at current LTP |

### 13.5 Dashboard

New **Positions** tab: KPI row (open count, open unrealised P&L, today's realised P&L,
nearest expiry) plus open/closed positions tables showing contract (underlying/strike/CE-PE),
lots, entry/live premium, SL/target, P&L (% and ₹), expiry countdown badge, and status
(Monitoring / Trailing / Profit Locked).
