# Bot-Options — Platform Walkthrough

**Version 2.0** — Updated after full review, bug-fix, and feature implementation pass.

This document describes the current state of **Bot-Options**, a production-grade Options Trading Platform running on **port 8001**, fully independent of Bot-Stocks, and connected to the broker via the OpenAlgo API.

---

## How to Run

```bash
# From the Strategies root directory
python Bot-Options/app.py
```

Dashboard → [http://127.0.0.1:8001](http://127.0.0.1:8001)

Bot-Stocks continues to run independently on port 8000 with no conflicts.

---

## Directory Map

```
Bot-Options/
├── app.py                        # FastAPI server (port 8001), all API endpoints
├── config.yml                    # Full platform configuration (no hardcoded values)
├── option_scanner.py             # Three-stage scanner + execution orchestrator
│
├── core/
│   ├── expiry_manager.py         # Weekly/Monthly expiry selection & auto-roll
│   ├── strike_selector.py        # ATM / OTM / ITM / PREMIUM / LIQUIDITY / TREND / DELTA
│   ├── option_filters.py         # IV score, OI momentum, theta decay, candle patterns
│   ├── option_signals.py         # Stage 1 (underlying) & Stage 3 (option chart) scans
│   └── option_risk.py            # Circuit breakers, capital limits, cooldown
│
├── data/
│   ├── option_chain.py           # Live option chain fetcher (OpenAlgo)
│   └── instrument_resolver.py    # NSE symbol parser & token resolver
│
├── db/
│   ├── option_signal_db.py       # SQLite — signal log with outcome tracking
│   └── option_trade_db.py        # SQLite — positions + full audit event log
│
├── execution/
│   ├── order_engine.py           # Order placement, deduplication, fill poller
│   └── position_monitor.py       # Background thread: SL/target/trailing/expiry mgmt
│
├── notifications/
│   └── notifier.py               # Telegram + WhatsApp alerts
│
├── frontend/
│   ├── index.html                # Bloomberg-style Trading Terminal UI
│   ├── index.css                 # Dark theme design system
│   └── index.js                  # Live data binding, scan controls, quick trade
│
├── option_signals.db             # Auto-created on first run (relative path)
├── option_trades.db              # Auto-created on first run (relative path)
└── options.log                   # Rolling application log
```

---

## Architecture: The Three-Stage Signal Pipeline

Every option signal passes through three sequential gates before it is accepted or executed. Failure at any gate terminates the signal for that cycle.

```
┌──────────────────────────────────────────────────────┐
│  STAGE 1 — Underlying Index Scan                     │
│  • Fetches NIFTY / BANKNIFTY OHLCV (yfinance or OA)  │
│  • Runs UTBot + SR Lines on the index chart          │
│  • Computes composite score; applies MTF/EMA filters │
│  • Gate 1: score ≥ min_underlying_score (default 60) │
│  • Output: direction (BUY→CE, SELL→PE), score,       │
│            DataFrame, SR zones for downstream use    │
└──────────────────┬───────────────────────────────────┘
                   │ pass Gate 1
┌──────────────────▼───────────────────────────────────┐
│  STAGE 2 — Strike Selection & Option Filters         │
│  • Resolves target expiry via expiry_manager.py      │
│  • Fetches live option chain from OpenAlgo           │
│  • Selects contract: ATM / OTM / ITM / PREMIUM /     │
│                      LIQUIDITY / TREND / DELTA       │
│  • Applies hard OI + volume filters                  │
│  • Scores: IV penalty, OI momentum, theta decay,     │
│            candle pattern (new)                      │
│  • OI momentum uses real prev-cycle snapshot (fixed) │
│  • Candle patterns use Stage 1 DF (no re-fetch)      │
└──────────────────┬───────────────────────────────────┘
                   │ strike selected
┌──────────────────▼───────────────────────────────────┐
│  STAGE 3 — Option Premium Chart Confirmation         │
│  • Fetches OHLCV of the selected NFO option symbol   │
│  • Runs UTBot on the premium chart                   │
│  • Confirmed → +bonus pts; Contradicted → −penalty   │
│  • Mode: "strict" (hard reject) or "score_only"      │
└──────────────────┬───────────────────────────────────┘
                   │ final score computed
┌──────────────────▼───────────────────────────────────┐
│  GATE 2 — Final Score + Deduplication                │
│  • final_score ≥ min_alert_score (default 60)        │
│  • Signal dedup: same (symbol, direction) suppressed │
│    within scan_dedup_window_seconds (default 15 min) │
│  • Passes → saved to DB, Telegram alert sent         │
└──────────────────┬───────────────────────────────────┘
                   │ if auto mode + risk checks pass
┌──────────────────▼───────────────────────────────────┐
│  EXECUTION — Order + Fill Confirmation               │
│  • Places BUY order via placeorder()                 │
│  • Polls orderstatus() until COMPLETE or timeout     │
│  • Uses actual fill price as entry_premium           │
│  • Opens DB position record only after fill confirm  │
└──────────────────────────────────────────────────────┘
```

---

## Strike Selection Methods

| Method | Behaviour |
|---|---|
| `ATM` | Strike closest to the current underlying spot price |
| `OTM` | N strikes out-of-the-money (`otm_strikes` in config) |
| `ITM` | N strikes in-the-money (`itm_strikes` in config) |
| `PREMIUM` | Strike whose LTP falls within `premium_min`–`premium_max` range, closest to ATM |
| `LIQUIDITY` | Highest `OI × Volume` within ±5 strikes of ATM |
| `TREND` | Biases 1 strike ITM for CE/PE (higher delta for trend trades); configurable via `trend_itm_offset` |
| `DELTA` | Strike whose option delta is closest to `target_delta` (default 0.40); requires delta in chain data |

---

## Score Composition

Each signal receives a composite score built from:

| Component | Source | Range |
|---|---|---|
| Underlying trend score | UTBot + SR (Stage 1) | 0–100 |
| IV penalty | High IV penalises option buyers | −15 to +5 |
| OI momentum | Real prev-cycle OI delta × LTP change | −10 to +10 |
| Theta decay penalty | Days to expiry | −100 to 0 |
| Candle pattern | Hammer / Engulfing near S/R zone | −5 to +8 |
| Stage 3 confirmation | UTBot on option premium chart | −15 to +15 |

Final score is clamped to `[0, 100]`. Only signals ≥ `min_alert_score` proceed.

---

## Trade Management Engine

All calculations are relative to **entry premium** — never the underlying price.

| Feature | Config Keys | Behaviour |
|---|---|---|
| **Stop Loss** | `stop_loss_pct` | Exit if premium drops N% from entry |
| **Target** | `target_pct` | Exit if premium rises N% from entry |
| **Partial Exit** | `partial_exit.target1_pct`, `exit_qty_fraction` | Exit 50% of position at +30%, move SL to breakeven |
| **Profit Lock** | `profit_lock.levels[]` | Three-level ratchet: 30% → lock 50%, 60% → lock 70%, 100% → lock 85% |
| **Trailing SL** | `trailing_sl.activation_pct`, `distance_pct` | Activates after +25% gain; trails 15% behind peak |
| **Expiry Auto-Exit** | `exit_minutes_before_close` | Squares off 10 minutes before 15:30 on expiry day |

---

## Risk Circuit Breakers

All breakers are checked before any order is placed:

| Breaker | Config Key | Action |
|---|---|---|
| Max simultaneous positions | `max_simultaneous_positions` | Block new trades |
| Max trades per day | `max_trades_per_day` | Block new trades |
| Daily loss limit (₹) | `max_daily_loss_amount` | Block new trades |
| Daily loss limit (%) | `max_daily_loss_pct` | Block new trades |
| Consecutive loss cooldown | `consecutive_loss_limit` + `cooldown_minutes` | Pause for N minutes |
| Capital per trade | `max_capital_per_trade` | Block oversized trades |
| Total capital exposure | `capital_allocation` | Block if aggregate cost would exceed total allocation |

---

## API Endpoints

### Core Platform

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Load current config.yml |
| `POST` | `/api/config` | Save config (preserves YAML comments via ruamel) |
| `POST` | `/api/scan` | Trigger manual three-stage scan |
| `GET` | `/api/signals?limit=50&offset=0` | Paginated signal history |
| `GET` | `/api/statistics?days=30` | Win rate, avg PnL, executed count |
| `GET` | `/api/logs?lines=150` | Last N lines of options.log |

### Option Chain

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/option-chain?underlying=NIFTY&strike_count=15` | Live chain with greeks |
| `GET` | `/api/max-pain?underlying=NIFTY` | Max Pain strike + full pain map |

### Positions

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/positions` | All open positions |
| `GET` | `/api/positions/closed?limit=50` | Closed position history |
| `GET` | `/api/positions/{id}/events` | Full audit event log for a position |
| `POST` | `/api/positions/{id}/close` | Manual square-off (race-condition safe) |
| `POST` | `/api/order` | Place direct manual order |

### Live Market Data *(new)*

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/market-pulse` | Live spot prices + India VIX + real PCR per underlying |
| `GET` | `/api/greeks` | Portfolio net Delta/Gamma/Theta(₹)/Vega across all open positions |

### Safety *(new)*

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/emergency-exit` | Kill switch — exits all positions at market, sends critical alert |

---

## Background Threads

Three daemon threads run alongside the FastAPI server:

| Thread | Starts in | Purpose |
|---|---|---|
| **Position Monitor** | `startup_event()` | Polls LTP every 5s, manages SL/target/trailing/expiry exits |
| **Outcome Tracker** | `startup_event()` | Every `outcome_check_hours` (default 4h), stamps `outcome_pnl_pct` back into signal records from matching closed positions |
| **Auto-Scan Loop** | `index.js` client | Triggered from the frontend when Auto-Scan toggle is ON; calls `/api/scan` on `scan_interval_seconds` cadence |

---

## Startup Sequence

When `python app.py` is executed:

1. FastAPI server binds to `http://127.0.0.1:8001`
2. `config.yml` is read
3. OpenAlgo client is initialised and cached
4. SQLite databases (`option_signals.db`, `option_trades.db`) are auto-created if absent
5. Open positions are loaded from DB into the position monitor's in-memory cache
6. **Broker reconciliation runs** — any position open in the DB but absent from the broker's `positionbook()` is marked `RECONCILED_CLOSE` and a Telegram alert is sent
7. Position monitor background thread starts
8. Signal outcome tracker background thread starts
9. Frontend is served at `/`

---

## Key Design Decisions & Fixes Applied (v2.0)

### Bugs Fixed

| Bug | Fix |
|---|---|
| `from typing import dict, list, tuple` — invalid imports causing `ImportError` on all Python versions | Replaced with `Dict`, `List`, `Tuple` from `typing` module across all 11 files |
| `DB_PATH` hardcoded to `c:/Rahul/Trade/Strategies/...` — breaks on any other machine or path | Changed to `Path(__file__).resolve().parents[1]` in both DB files |
| OI momentum always received `prev_oi == current_oi` — filter never produced output | Added `_oi_snapshot` dict in scanner, carrying `(oi, ltp)` between scan cycles |
| Manual close endpoint had a race condition with the monitoring loop — could place double exit orders | Added `is_closing` flag set under lock before `_execute_exit`; monitor loop skips flagged positions |

### Features Added

| Feature | Where |
|---|---|
| Fill confirmation poller — waits for `orderstatus()` before opening position | `order_engine.py` → `poll_order_fill()` |
| Broker reconciliation on startup | `position_monitor.py` → `_reconcile_with_broker()` |
| Signal deduplication — same contract suppressed for 15 min across scan cycles | `option_scanner.py` → `_signal_dedup` cache |
| Portfolio Greeks API | `app.py` → `GET /api/greeks` |
| Live VIX + real PCR | `app.py` → `GET /api/market-pulse` |
| Max Pain calculation | `app.py` → `GET /api/max-pain` |
| TREND strike selection (ITM-biased for direction) | `strike_selector.py` |
| DELTA strike selection (target-delta based) | `strike_selector.py` |
| Candle pattern detection (Hammer, Engulfing, Shooting Star) | `option_filters.py` → `calculate_candle_pattern_score()` |
| Signal outcome tracking background job | `app.py` → `_run_outcome_tracker()` thread |
| Emergency kill switch | `app.py` → `POST /api/emergency-exit` |

---

## Configuration Reference (Key Sections)

### Underlyings
```yaml
underlyings:
  - name: "NIFTY"
    lot_size: 75
    strike_step: 50
    enabled: true
```

### Strike Selection
```yaml
strike_selection:
  method: "ATM"           # ATM / OTM / ITM / PREMIUM / LIQUIDITY / TREND / DELTA
  trend_itm_offset: 1     # TREND method: strikes ITM
  target_delta: 0.40      # DELTA method: target option delta
  expiry_preference: "WEEKLY"
  auto_roll_days: 1
```

### Signal Gates
```yaml
min_underlying_score: 60          # Gate 1
filters:
  min_alert_score: 60             # Gate 2 (final combined score)
scan_dedup_window_seconds: 900    # Gate 3 (deduplication window = 15 min)
```

### Execution
```yaml
execution:
  order_mode: "manual"            # "manual" | "auto"
  order_type: "LIMIT"
  order_product: "MIS"
  num_lots: 1
  fill_timeout_seconds: 30        # How long to wait for order fill before aborting
```

### Trade Management
```yaml
trade_management:
  stop_loss_pct: 30.0
  target_pct: 50.0
  profit_lock:
    levels:
      - threshold_pct: 30.0 / lock_fraction: 0.50
      - threshold_pct: 60.0 / lock_fraction: 0.70
      - threshold_pct: 100.0 / lock_fraction: 0.85
  trailing_sl:
    activation_pct: 25.0
    distance_pct: 15.0
  partial_exit:
    target1_pct: 30.0
    exit_qty_fraction: 0.5
    move_sl_to_breakeven: true
```

---

## Notification Events

| Event | Priority | Silent |
|---|---|---|
| New signal generated | 8 | No |
| Order executed (position opened) | 7 | No |
| Position closed (SL / Target / Expiry / Manual) | 7 | No |
| Partial exit executed | 6 | Yes |
| Profit lock triggered | 6 | Yes |
| Broker reconciliation discrepancy | 9 | No |
| Emergency exit executed | 10 | No |

---

## Database Schema

### `option_signals.db` — `option_signals` table

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key |
| `timestamp` | TEXT | Signal generation time |
| `underlying` | TEXT | NIFTY / BANKNIFTY |
| `symbol` | TEXT | Full NFO symbol e.g. `NIFTY28OCT2526150CE` |
| `expiry` | TEXT | Expiry in OA format |
| `strike` | REAL | Strike price |
| `option_type` | TEXT | CE / PE |
| `direction` | TEXT | BUY / SELL |
| `entry_premium` | REAL | Option LTP at signal time |
| `confidence_score` | REAL | 0–100 composite score |
| `score_reasons` | TEXT | JSON array of scoring reasons |
| `filter_status` | TEXT | JSON: iv/decay/candle/stage3 pass/warn |
| `iv_proxy` | REAL | Implied volatility at signal time |
| `oi_at_signal` | INTEGER | Open interest at signal time |
| `status` | TEXT | SIGNAL → EXECUTED → FILL_TIMEOUT |
| `outcome_pnl_pct` | REAL | Actual P&L% stamped by outcome tracker |
| `outcome_checked` | INTEGER | 0 = pending, 1 = checked |

### `option_trades.db` — `option_positions` table (key columns)

| Column | Description |
|---|---|
| `entry_premium` | Actual fill price (from `poll_order_fill`) |
| `current_sl_premium` | Live SL floor (updated by trailing/profit-lock) |
| `initial_sl_premium` | Original SL at entry (for reference) |
| `target_premium` | Original target at entry |
| `peak_premium` | Highest premium seen since entry |
| `profit_locked` | Lock level reached (0–3) |
| `trailing_active` | 1 if trailing SL is active |
| `partial_exit_done` | 1 if first partial exit has fired |
| `close_reason` | SL / TARGET / EXPIRY / MANUAL / EMERGENCY / RECONCILED_CLOSE |
| `pnl_amount` | Realised P&L in ₹ |

### `option_trades.db` — `option_position_events` table

Every state change is logged: OPEN, SL_UPDATE, TRAILING_SL, PROFIT_LOCK, PARTIAL_EXIT, CLOSE, EXIT_FAILED, RECONCILED_CLOSE.

---

## Cross-Import from Bot-Stocks

To avoid code duplication, the following are imported directly from Bot-Stocks:

- `compute_utbot_signals()` — UTBot indicator engine
- `compute_sr_signals()` — Support/Resistance channel engine
- `evaluate_composite_signals()` — Multi-strategy composite scorer
- `fetch_history()` — OHLCV fetcher (yfinance + OpenAlgo)
- `send_telegram_alert()` — Telegram notification sender

The path injection (`sys.path.insert`) is done once at module import time using `Path(__file__).resolve().parents[2] / "Bot-Stocks"`.

---

## What Is Not Yet Implemented

| Item | Reason Deferred |
|---|---|
| WebSocket LTP subscription | Future architecture — polling is stable and functional |
| Multi-leg strategies (Iron Condor, Straddle, etc.) | Requires DB schema extension (`option_strategies` table) |
| Greeks-based signal scoring | Requires live greeks from OpenAlgo during scan, not just at execution |
| AI/ML signal ranking | Future phase |
| NSE holiday calendar | Deliberately excluded per project scope |
