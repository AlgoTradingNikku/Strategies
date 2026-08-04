# Options Trading Platform — Implementation Plan

> **Status**: Updated with three-stage signal workflow (including Option Chart Scan as Stage 3) and runtime architecture clarification.

## Background

The existing **Bot-Stocks** platform is a production-grade stock screener with:

- `scanner.py` — core scanning engine (1,232 lines), data fetching, signal orchestration
- `signals.py` — UT Bot ATR + S/R Channel computation engines (1,373 lines)
- `signal_db.py` — SQLite-backed signal logging + outcome tracking
- `trade_db.py` — SQLite position/event store for trade management
- `trading_adapter.py` — broker-agnostic adapter (OpenAlgo, Flattrade, MStock, Shoonya, Dhan)
- `trade_manager.py` — live position monitor with SL, target, trailing, profit-lock
- `telegram.py` — Telegram notification module (direct + OpenAlgo modes)
- `nse_indices.py` — NSE index constituent fetcher with daily cache
- `app.py` — FastAPI server + REST API layer
- `config.yml` — fully externalized YAML config (222 lines)
- `frontend/` — single-page HTML/CSS/JS dashboard

The **Bot-Options** folder is currently empty. The goal is to build a first-class Options Trading Platform inside it.

---

## Architectural Analysis & Decisions

### What I'm Recommending vs. What Was Specified

**1. No shared `core/` extraction (yet)**
Extracting a shared `core/` package would require breaking the existing working Stock platform — risking bugs and deployment disruption. Instead:
- Build `Bot-Options` as a **self-contained module** that *directly imports* from `Bot-Stocks` for truly shared components (signals, trading_adapter, telegram, nse_indices)
- Use Python path manipulation (already done in the stock platform) to cross-import cleanly
- This preserves zero-risk to the existing platform and achieves 80% of the code-sharing benefit

**2. Options-specific `signals.py` additions (not replacements)**
The UTBot and SR signals engines work perfectly for options scanning. I'll import them directly. I will only add options-specific signal wrappers (premium-based scoring, IV proxy, OI scoring).

**3. Premium-based Trade Management (major enhancement over stock platform)**
The stock platform uses price-% for SL/Target. For options, this is wrong — a 20% move in an ATM option is very different from a 20% move in a deep ITM option. The new Trade Manager will use **entry-premium-relative calculations** throughout.

**4. Options Chain as primary interface (replacing Screener)**
The stock platform's "screener" metaphor (scan → list of signals) is appropriate for stocks. For options, professional traders think in terms of:
- Options Chain (strikes, premiums, OI, IV)
- Strategy Cockpit (multi-leg view, payoff diagram)
- Live Position P&L (per-leg greeks, real-time premium decay)

The Options Dashboard will therefore be designed as a **Trading Terminal**, not a screener.

**5. Intelligent Strike Selection Engine (new, no stock equivalent)**
This is entirely new — no equivalent exists in the stock platform.

---

## Proposed Architecture

```
Bot-Options/
├── app.py                        # FastAPI server (port 8001)
├── config.yml                    # Options-specific configuration
├── option_scanner.py             # Main scanner — orchestrates strike selection + signal scan
│
├── core/
│   ├── __init__.py
│   ├── strike_selector.py        # Intelligent Strike Selection Engine
│   ├── option_signals.py         # Options signal wrappers (reuses Bot-Stocks signals)
│   ├── option_filters.py         # Options-specific filters (IV, OI, liquidity)
│   ├── option_risk.py            # Premium-based risk engine
│   └── expiry_manager.py         # Expiry calendar, rollover logic
│
├── execution/
│   ├── __init__.py
│   ├── order_engine.py           # Options order placement + duplicate prevention
│   └── position_monitor.py       # Premium-based position monitoring
│
├── data/
│   ├── __init__.py
│   ├── option_chain.py           # Option chain fetcher (OpenAlgo / NSE)
│   └── instrument_resolver.py    # Resolve option symbol → broker instrument token
│
├── db/
│   ├── __init__.py
│   ├── option_signal_db.py       # Options-specific signal log (CE/PE aware)
│   └── option_trade_db.py        # Options position store (premium-aware)
│
├── notifications/
│   └── notifier.py               # Re-exports Bot-Stocks telegram.py with options formatting
│
├── frontend/
│   ├── index.html                # Options Trading Terminal (single-page app)
│   ├── index.css                 # Premium design system
│   └── index.js                  # Terminal logic, options chain, live P&L
│
├── options.log                   # Application log
├── option_signals.db             # Options signal database
└── option_trades.db              # Options trade database
```

---

## Module Descriptions

### `core/strike_selector.py` — Strike Selection Engine

Plugin architecture with a `BaseStrikeSelector` interface. Concrete selectors:

| Method | Logic |
|--------|-------|
| `ATMSelector` | Closest strike to current underlying price |
| `ITMSelector` | N strikes in-the-money from ATM |
| `OTMSelector` | N strikes out-of-the-money from ATM |
| `PremiumRangeSelector` | Strikes with premium in configured min/max band |
| `TrendSelector` | CE if bullish trend, PE if bearish trend (uses UTBot direction) |
| `LiquiditySelector` | Strikes ranked by OI × Volume score |
| `OISelector` | Strikes with OI above threshold and max pain analysis |

Future-ready slots: `GreeksSelector`, `IVSelector`, `AISelector`.

### `core/expiry_manager.py` — Expiry Calendar

- Supports `WEEKLY`, `MONTHLY`, `NEXT_WEEKLY`, `NEXT_MONTHLY` expiry configurations
- Auto-rolls to next expiry when current expiry < N days away
- Parses NSE option symbol naming conventions
- Validates expiry dates against market calendar

### `core/option_signals.py` — Signal Integration (Three-Stage Engine)

This is the heart of the Options Bot. Implements three independent confirmation stages:

**Stage 1 — Underlying Scan** (reuses `Bot-Stocks/signals.py` directly):
- Fetches NIFTY/BANKNIFTY OHLCV and runs UTBot + SR Lines
- Applies all configured filters: MTF, EMA, RSI, ADX, Volume
- Produces a directional signal (BUY/SELL) with a composite score
- Gate: only proceeds if score ≥ `min_underlying_score`

**Stage 2 — Strike Selection & Option Filters** (new, options-specific):
- Fetches live option chain for configured expiry
- Applies the configured Strike Selector method (ATM/OTM/Premium/Liquidity)
- Applies hard filters: OI threshold, premium range, volume, days-to-expiry
- Gate: only proceeds if at least one strike passes all filters

**Stage 3 — Option Chart Scan** (new, the key enhancement):
- Fetches OHLCV for the shortlisted option symbol itself (e.g., NIFTY2581523450CE)
- Runs the same UTBot + SR engines on the option's premium chart
- Uses separately configurable `key_value` and `atr_period` (options charts are noisier)
- Two modes: `strict` (must confirm) or `score_only` (adds bonus to combined score)
- Prevents buying already-extended premiums that the underlying scan alone cannot detect
- Detects option-specific SR zones on the premium chart

**Why Stage 3 is critical:**
A NIFTY BUY signal does not guarantee CE premium appreciation. The premium may already be exhausted, extended by 40% at open, or actively being sold by institutions. Stage 3 catches exactly this — it only generates a final signal when the option's own premium chart independently confirms the move has started.

**Combined Score:**
```
Final Score = Stage 1 (underlying score, 0-100)
            + Stage 3 bonus (option chart confirmation, 0-20)
            - Stage 3 penalty (option chart contradicts, -15)
            - Time decay penalty (< 3 days to expiry)
            - IV penalty (if IV unusually high for buying)
```

Also adds options-specific scoring components:
- **IV Proxy Score**: High IV → penalize buying (expensive), reward selling
- **OI Momentum Score**: Rising OI with rising premium = conviction confirmation
- **Time Decay Score**: Penalizes options with < 3 days to expiry

### `core/option_risk.py` — Premium-Based Risk Engine

All calculations relative to **entry premium**, not underlying price:

```
SL = entry_premium × (1 - sl_pct/100)
Target = entry_premium × (1 + target_pct/100)
Trail = peak_premium × (1 - trail_distance_pct/100)
Profit Lock = min(trail_level, entry_premium + lock_fraction × (peak - entry_premium))
```

Includes:
- Maximum daily loss in premium terms
- Maximum capital exposure per leg
- Circuit breakers: consecutive losses, daily drawdown limit
- Cool-down period enforcement

### `execution/order_engine.py` — Options Order Engine

- Deduplication by `(underlying, expiry, strike, option_type, direction)`
- Retry with exponential backoff (3 attempts, 2s/4s/8s delays)
- Supports NFO exchange routing (not NSE)
- Quantity = lot_size × num_lots (lots are options-specific concept)
- Both MIS and NRML product types

### `execution/position_monitor.py` — Premium-Based Monitor

Extends the stock platform's PositionMonitor concept with:
- Premium polling (WebSocket preferred, fallback REST LTP)
- Multi-level profit lock (configurable milestones, not just one)
- Expiry-aware exit (auto-square-off on expiry day before 3:20 PM IST)
- Partial exit support with lot-aware quantity reduction

### `data/option_chain.py` — Option Chain Data

Priority fetch order:
1. OpenAlgo option chain endpoint (if available)
2. NSE India option chain API (direct, no auth needed for read)
3. yfinance for index options (NIFTY ^NSEI, BANKNIFTY ^NSEBANK)

Returns normalized schema: `{strike, ce_ltp, pe_ltp, ce_oi, pe_oi, ce_iv, pe_iv, ce_volume, pe_volume}`

---

## Runtime Architecture — Your Questions Answered

**Bot-Options is a completely independent application** — separate config, separate dashboard, separate server process, separate port.

### Own Config File?

**Yes.** `Bot-Options/config.yml` is entirely separate from `Bot-Stocks/config.yml`. They share no config. Options-specific parameters (strike selection, expiry, lot size, premium-based SL/Target) exist only in the Options config. You can run both bots simultaneously with different configurations.

### Own Dashboard?

**Yes.** `Bot-Options/frontend/` contains a completely different **Options Trading Terminal** — not a copy of the Stock Bot screener. It has its own design: options chain view, strike selection panel, live premium P&L, risk gauge, and the three-stage signal feed. It's a separate HTML/CSS/JS application with a dark terminal aesthetic suited to professional options traders.

### Running the Bot — Exact Same Pattern as Bot-Stocks

```
Bot-Stocks                         Bot-Options
─────────────────────────          ─────────────────────────
python Bot-Stocks/app.py           python Bot-Options/app.py
    │                                  │
    ▼                                  ▼
FastAPI server                     FastAPI server
Port: 8000                         Port: 8001
    │                                  │
    ▼                                  ▼
Serves frontend/index.html         Serves frontend/index.html
(Stock Screener Dashboard)         (Options Trading Terminal)
    │                                  │
    ▼                                  ▼
http://127.0.0.1:8000              http://127.0.0.1:8001
```

You can run **both simultaneously** — they are completely independent processes on different ports. The Options Bot imports shared computation logic (signals, trading_adapter, telegram) from Bot-Stocks via Python path, but runs as its own process with its own database, config, and frontend.

### Summary Table

| Item | Bot-Stocks | Bot-Options |
|---|---|---|
| Entry point | `Bot-Stocks/app.py` | `Bot-Options/app.py` |
| Port | 8000 | 8001 |
| Config | `Bot-Stocks/config.yml` | `Bot-Options/config.yml` |
| Dashboard | `Bot-Stocks/frontend/` | `Bot-Options/frontend/` |
| Signal DB | `Bot-Stocks/signals.db` | `Bot-Options/option_signals.db` |
| Trade DB | `Bot-Stocks/trades.db` | `Bot-Options/option_trades.db` |
| Log file | `Bot-Stocks/scanner.log` | `Bot-Options/options.log` |
| Exchange | NSE | NFO |
| Shared code | signals.py, trading_adapter.py, telegram.py | (imported from Bot-Stocks) |

---

## Database Schema

### `option_signals.db` — Signal Log

```sql
CREATE TABLE option_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    underlying TEXT NOT NULL,        -- NIFTY, BANKNIFTY, etc.
    symbol TEXT NOT NULL,            -- Full broker symbol (NIFTY2581523450CE)
    exchange TEXT NOT NULL,          -- NFO
    expiry TEXT NOT NULL,            -- 2025-08-15
    strike REAL NOT NULL,
    option_type TEXT NOT NULL,       -- CE or PE
    direction TEXT NOT NULL,         -- BUY or SELL
    strategy_name TEXT NOT NULL,     -- UTBot, SR, UTBot+SR
    entry_premium REAL NOT NULL,
    current_premium REAL,
    confidence_score REAL,
    score_reasons TEXT DEFAULT '[]',
    filter_status TEXT DEFAULT '{}', -- {ema: pass, volume: fail, ...}
    iv_proxy REAL,
    oi_at_signal INTEGER,
    underlying_price REAL,
    timeframe TEXT,
    status TEXT DEFAULT 'SIGNAL',    -- SIGNAL, EXECUTED, EXPIRED, CANCELLED
    outcome_pnl_pct REAL,
    outcome_checked INTEGER DEFAULT 0
);
```

### `option_trades.db` — Position Store

```sql
CREATE TABLE option_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    underlying TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'NFO',
    expiry TEXT NOT NULL,
    strike REAL NOT NULL,
    option_type TEXT NOT NULL,       -- CE or PE
    direction TEXT NOT NULL,         -- BUY or SELL
    lot_size INTEGER NOT NULL,
    num_lots INTEGER NOT NULL,
    quantity INTEGER NOT NULL,       -- lot_size × num_lots
    entry_premium REAL NOT NULL,
    entry_time TEXT NOT NULL,
    underlying_price_at_entry REAL,
    current_premium REAL,
    current_sl_premium REAL NOT NULL,
    initial_sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    peak_premium REAL NOT NULL,      -- High-water mark
    profit_locked INTEGER DEFAULT 0,
    trailing_active INTEGER DEFAULT 0,
    partial_exit_done INTEGER DEFAULT 0,
    expiry_exit_triggered INTEGER DEFAULT 0,
    status TEXT DEFAULT 'OPEN',      -- OPEN, CLOSED
    close_reason TEXT,               -- TARGET, SL, TRAILING, PROFIT_LOCK, MANUAL, EXPIRY
    close_premium REAL,
    close_time TEXT,
    pnl_premium REAL,               -- Per-unit P&L in premium points
    pnl_pct REAL,                   -- % return on entry premium
    pnl_amount REAL,                -- Total P&L = pnl_premium × quantity
    timeframe TEXT
);
```

---

## Configuration Structure (`config.yml`)

```yaml
# ─── Identity ─────────────────────────────────────────────────
platform: "options"
trading_api_source: "openalgo"
exchange: "NFO"

# ─── Strike Selection ─────────────────────────────────────────
strike_selection:
  underlyings: ["NIFTY", "BANKNIFTY"]  # Which indices to scan
  expiry_preference: "WEEKLY"           # WEEKLY / MONTHLY / NEXT_WEEKLY
  auto_roll_days: 1                     # Roll when < N days to expiry
  method: "ATM"                         # ATM / ITM / OTM / PREMIUM / TREND / LIQUIDITY
  otm_strikes: 1                        # Strikes away from ATM (for OTM/ITM methods)
  premium_min: 50                       # Min premium filter (PREMIUM method)
  premium_max: 500                      # Max premium filter
  oi_min_threshold: 100000              # Min OI to qualify
  liquidity_min_volume: 5000            # Min volume
  scan_both_sides: true                 # Scan CE and PE simultaneously

# ─── Stage 1: Underlying Signal Generation ────────────────────
# (Runs on NIFTY/BANKNIFTY OHLCV chart — same engines as Stock Bot)
strategy:
  ut_enabled: true
  key_value: 1.0
  atr_period: 2
  use_heikin_ashi: false

sr_channels:
  enabled: true
  pivot_period: 10
  source: "High/Low"
  channel_width_pct: 5.0
  min_strength: 1
  max_num_sr: 6
  loopback: 290
  proximity_pct: 0.2

scan_timeframe: "5m"
scan_interval_seconds: 60
signal_lookback_candles: 2
min_underlying_score: 60              # Stage 1 gate: min score to proceed to Stage 2

# ─── Stage 3: Option Chart Confirmation ───────────────────────
# Runs UTBot + SR on the shortlisted option symbol's own OHLCV chart
# Only executes if Stage 1 + Stage 2 both pass
option_chart_confirmation:
  enabled: true
  mode: "score_only"                  # "strict" = must confirm | "score_only" = adds bonus points
  key_value: 1.5                      # Slightly higher than underlying (option charts are noisier)
  atr_period: 3                       # Slightly smoother for option premium charts
  require_sr_proximity: false         # Also require option premium near SR support?
  confirmation_bonus_pts: 15          # Added to score when option chart confirms (score_only mode)
  contradiction_penalty_pts: 15       # Subtracted when option chart contradicts

# ─── Filters (applied in Stage 1 on the underlying chart) ─────
filters:
  ema_filter_enabled: false
  ema_period: 200
  volume_filter_enabled: false
  volume_sma_period: 20
  volume_min_pct: 60
  mtf_filter_enabled: true
  mtf_timeframe: "15m"
  mtf_neutral_pct: 0.3
  mtf_atr_period: 10
  adx_filter_enabled: false
  adx_min_threshold: 20.0
  rsi_filter_enabled: false
  rsi_period: 14
  min_alert_score: 60
  # Options-specific filters (applied in Stage 2 on the option chain)
  iv_score_enabled: true            # Score based on IV proxy
  oi_score_enabled: true            # Score based on Open Interest
  oi_momentum_score_enabled: true   # Rising OI + rising premium = conviction bonus
  time_decay_penalty_enabled: true  # Penalize options < 3 days to expiry
  time_decay_threshold_days: 3      # Days-to-expiry below which penalty applies

# ─── Execution ────────────────────────────────────────────────
execution:
  order_mode: "manual"             # "manual" | "auto"
  order_type: "LIMIT"              # "MARKET" | "LIMIT"
  order_product: "MIS"             # "MIS" | "NRML"
  lot_size: 25                     # Lot size for the underlying (NIFTY=25)
  num_lots: 1                      # Number of lots per trade
  slippage_pts: 1.0                # Max acceptable slippage in premium points

# ─── Trade Management ─────────────────────────────────────────
trade_management:
  enabled: true
  poll_interval_seconds: 5

  # All % values are relative to ENTRY PREMIUM
  stop_loss_pct: 30.0              # Exit if premium falls to 70% of entry (for BUY)
  target_pct: 50.0                 # Exit if premium rises to 150% of entry (for BUY)

  # Multi-level Profit Lock
  profit_lock:
    enabled: true
    levels:
      - threshold_pct: 30.0        # Lock when premium up 30%
        lock_fraction: 0.50        # Protect 50% of peak gain
      - threshold_pct: 60.0        # Lock when premium up 60%
        lock_fraction: 0.70        # Protect 70% of peak gain
      - threshold_pct: 100.0       # Lock when premium doubles
        lock_fraction: 0.85        # Protect 85% of peak gain

  trailing_sl:
    enabled: true
    activation_pct: 25.0          # Start trailing after 25% gain
    distance_pct: 15.0            # Trail 15% behind peak premium

  partial_exit:
    enabled: true
    target1_pct: 30.0
    exit_qty_fraction: 0.5        # Exit 50% at target1
    move_sl_to_breakeven: true

  expiry_management:
    auto_exit_on_expiry: true
    exit_time_before_close: 10    # Exit N minutes before market close on expiry day

  notifications:
    on_signal: true
    on_execution: true
    on_sl_move: false
    on_profit_lock: true
    on_exit: true

# ─── Risk Management ──────────────────────────────────────────
risk_management:
  max_capital_per_trade: 50000    # Max premium outlay per trade (BUY side)
  max_simultaneous_positions: 5
  max_trades_per_day: 10
  max_daily_loss_amount: 5000     # Hard stop on daily loss (₹)
  max_daily_loss_pct: 5.0         # Hard stop as % of capital
  consecutive_loss_limit: 3       # Pause after N consecutive losses
  cooldown_minutes: 30            # Pause duration after limit hit
  capital_allocation: 100000      # Total allocated capital for options

# ─── Broker / API ─────────────────────────────────────────────
openalgo:
  apikey: ""
  username: ""
  base_url: "http://127.0.0.1:5000"
  ws_url: "ws://127.0.0.1:8765"

telegram:
  enabled: true
  mode: "direct"
  bot_token: ""
  chat_id: ""

# ─── Data & System ────────────────────────────────────────────
data:
  lookback_days: 30

bot:
  log_level: "INFO"
  market_hours_check: true
  market_open: "09:15"
  market_close: "15:30"
  auto_refresh_enabled: true
```

---

## Dashboard Design — Options Trading Terminal

The dashboard is **not** a screener clone. It is designed as a professional options trading terminal inspired by Bloomberg Options Monitor, Sensibull, and Opstra.

### Layout: 5-Panel Terminal

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  HEADER: Underlying Ticker Bar (NIFTY | BANKNIFTY | FINNIFTY | live prices)  │
├────────────────────────────┬───────────────────────────────┬─────────────────┤
│  LEFT PANEL (25%)          │  CENTER PANEL (50%)            │  RIGHT PANEL    │
│  ─────────────────         │  ──────────────────────        │  (25%)          │
│  Strike Selection Config   │  Live Signals Feed             │  Active         │
│  ─────────────────         │  (card-based, real-time)       │  Positions P&L  │
│  Expiry Selector           │                                │                 │
│  Method Selector           │  Options Chain View            │  Risk Gauge     │
│  Premium Range             │  (CE | Strike | PE table)      │                 │
│  Filter Toggles            │                                │  Daily Stats    │
│  Strategy Selector         │  Mini Chart (underlying)       │                 │
│                            │                                │  Trade History  │
│  [SCAN NOW]                │                                │                 │
│  [AUTO MODE: ON/OFF]       │                                │                 │
├────────────────────────────┴───────────────────────────────┴─────────────────┤
│  BOTTOM TAB BAR: [Signals] [Positions] [Options Chain] [Analytics] [Logs]    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Why This Is Superior to a Stock Screener

| Screener Approach | Terminal Approach |
|---|---|
| Flat list of symbols | Options chain with strike ladder |
| Single scan result | Live streaming signal feed |
| No option type context | CE/PE clearly separated |
| No premium tracking | Real-time premium P&L |
| Static configuration | Live adjustable parameters |
| No expiry awareness | Expiry countdown + auto-roll |
| No lot-size logic | Lot-aware position sizing |
| No greeks display | IV proxy + OI heatmap |

### Key Widgets

1. **Underlying Ticker Bar** — Live price for NIFTY/BANKNIFTY/FINNIFTY with % change, PCR (Put-Call Ratio proxy)
2. **Options Chain Table** — Strike ladder with CE LTP, CE OI, Strike, PE OI, PE LTP. Color-coded by ITM/ATM/OTM. Highlighted strikes matching current selection
3. **Signal Cards** — Each signal shown as a card: Underlying | Strike | CE/PE | BUY/SELL | Strategy | Score | Premium | Entry | Expiry | Filters | Action button
4. **Position Monitor Panel** — Live P&L per position in premium terms + ₹ amount. Color-coded P&L. SL/Target progress bars. Manual exit buttons
5. **Risk Gauge** — Real-time daily loss meter, positions used counter, drawdown visualization
6. **Analytics Tab** — Win rate by strategy/expiry/underlying, premium decay analysis, score vs outcome scatter

---

## Implementation Roadmap

### Milestone 1 — Foundation & Configuration *(Week 1)*
- [ ] `Bot-Options/` folder structure
- [ ] `config.yml` with full options configuration (including `option_chart_confirmation` block)
- [ ] `app.py` FastAPI server on port 8001
- [ ] Basic logging and config loading
- [ ] Shared import wiring from Bot-Stocks

### Milestone 2 — Option Chain & Strike Selection *(Week 1-2)*
- [ ] `data/option_chain.py` — NSE option chain fetcher
- [ ] `data/instrument_resolver.py` — symbol → NFO token resolver
- [ ] `core/expiry_manager.py` — expiry calendar
- [ ] `core/strike_selector.py` — all selector methods (Stage 2)
- [ ] API endpoints: `/api/option-chain`, `/api/strikes`

### Milestone 3 — Three-Stage Signal Generation *(Week 2)*
- [ ] `core/option_signals.py` — Stage 1 (underlying scan, wrapping Bot-Stocks signals.py)
- [ ] Stage 2 integration: strike selection + option chain filters (OI, premium, liquidity)
- [ ] Stage 3: Option chart OHLCV scan with UTBot + SR on the option symbol itself
- [ ] Combined score computation (Stage 1 score + Stage 3 bonus/penalty)
- [ ] `core/option_filters.py` — IV proxy, OI momentum, time decay
- [ ] `option_scanner.py` — full three-stage orchestration
- [ ] API endpoints: `/api/scan`, `/api/signals`

### Milestone 4 — Database Layer *(Week 2)*
- [ ] `db/option_signal_db.py`
- [ ] `db/option_trade_db.py`

### Milestone 5 — Execution Engine *(Week 3)*
- [ ] `execution/order_engine.py` — options order placement
- [ ] Integration with trading_adapter (NFO exchange routing)
- [ ] API endpoints: `/api/order`, `/api/ltp`

### Milestone 6 — Trade Management *(Week 3)*
- [ ] `execution/position_monitor.py` — premium-based monitoring
- [ ] Multi-level profit lock
- [ ] Expiry-aware auto-exit
- [ ] Trailing stop (premium-based)
- [ ] API endpoints: `/api/positions/*`

### Milestone 7 — Risk Management *(Week 3-4)*
- [ ] `core/option_risk.py` — full risk engine
- [ ] Circuit breakers, daily loss limits, cool-down

### Milestone 8 — Dashboard & Frontend *(Week 4)*
- [ ] `frontend/index.html` — Options Trading Terminal
- [ ] `frontend/index.css` — Professional dark-theme design system
- [ ] `frontend/index.js` — Full terminal logic, chain view, live P&L

### Milestone 9 — Integration & Testing *(Week 4-5)*
- [ ] End-to-end signal → execution → monitoring flow
- [ ] Configuration via dashboard
- [ ] Telegram notifications
- [ ] Log streaming
- [ ] Error recovery testing

---

## Open Questions

Please answer these before implementation begins:

1. **Underlying instruments**: Which underlyings to support first? NIFTY + BANKNIFTY only, or also FINNIFTY, MIDCPNIFTY, individual stocks?

2. **Option Chain Data Source**: Do you have access to the OpenAlgo option chain API, or should I use the public NSE India API endpoint directly?

3. **Lot sizes**: NIFTY=25, BANKNIFTY=15, FINNIFTY=40. Should these be auto-detected from the instrument cache (`instruments_cache.pkl`) or hardcoded in config?

4. **Phase 3 (Dashboard) approach**: Should I design + show you the dashboard mockup *before* writing any backend code (strict phase order), or can I build both in parallel?

5. **Data source for option OHLCV** (critical for Stage 3): Option symbols (e.g., NIFTY2581523450CE) are not available on yfinance. Should I use:
   - OpenAlgo historical data endpoint (preferred — already integrated)
   - NSE India historical data (public, no auth, but rate-limited)
   - Both with fallback chain

**Resolved Questions** (no longer open):
- *Signal approach*: Confirmed as three-stage — underlying scan (Stage 1) + strike selection (Stage 2) + option chart scan (Stage 3)
- *Separate config*: Yes — `Bot-Options/config.yml` is fully independent
- *Separate dashboard*: Yes — `Bot-Options/frontend/` is a dedicated Options Trading Terminal
- *Port*: Bot-Options runs on port **8001** (Bot-Stocks stays on 8000)
- *Run model*: `python Bot-Options/app.py` — identical pattern to Bot-Stocks

---

## Verification Plan

### Automated
- Import all modules without errors
- Config loading / saving round-trip
- Strike selector unit tests (ATM/OTM/ITM with mock chain data)
- Three-stage pipeline unit tests with mock data:
  - Stage 1: underlying UTBot signal correctly computed
  - Stage 2: strike shortlisting with OI/premium filters
  - Stage 3: option chart UTBot + combined score calculation
- Risk engine calculations (SL/target/trail computation correctness, premium-based)
- Database CRUD operations

### Manual
- `python Bot-Options/app.py` → server starts on port 8001 (independently of Bot-Stocks on 8000)
- Options Trading Terminal loads at `http://127.0.0.1:8001`
- Config save/load via dashboard
- Trigger manual scan → three-stage pipeline runs → signal cards appear
- Confirm Stage 3 mode toggle (strict vs. score_only) changes signal output
- Simulate position → verify premium-based SL/Target/Trail updates
- Run Bot-Stocks and Bot-Options simultaneously → verify no port conflict

---

## Key Architectural Improvements (Proactive Additions)

Beyond what was specified, these are added for production readiness:

| Addition | Reason |
|---|---|
| **Expiry auto-roll** | Without it, positions would expire worthlessly with no warning |
| **Max pain analysis** | Informs strike selection; highly relevant for NSE index options |
| **Put-Call Ratio (PCR) display** | Standard options market breadth indicator |
| **Lot-size awareness** | Options trade in lots, not individual units — critical for position sizing |
| **Expiry-day auto-square-off** | Prevents accidental option expiry and full premium loss |
| **NFO exchange routing** | Options are on NFO, not NSE — critical for order routing |
| **IV proxy scoring** | Options-specific signal quality metric not available in stock platform |
| **Circuit breaker on consecutive losses** | Prevents strategy blow-up during market regime changes |
| **Multi-level profit lock** | More sophisticated than single profit-lock threshold in stock platform |
| **Three-stage signal confirmation** | Underlying + Strike + Option chart — highest quality signals |
