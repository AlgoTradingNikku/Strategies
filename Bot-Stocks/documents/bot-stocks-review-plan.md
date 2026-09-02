# Bot-Stocks — End-to-End Technical & Trading Strategy Review

> **Scope:** Full review of the Bot-Stocks codebase at `C:\Rahul\Trade\Strategies\Bot-Stocks`.
> All findings are grounded in the actual source code; no speculation.
> Reviewed files: `scanner.py`, `signals.py`, `trading_adapter.py`, `trade_db.py`,
> `trade_management/monitor.py`, `trade_management/rules_engine.py`,
> `trade_management/executor.py`, `risk_limits.py`, `signal_grader.py`,
> `regime.py`, `circuit_breaker.py`, `api_rate_limiter.py`, `config.yml`,
> `strategies/momentum_chatgpt/`, `health_check.py`, and all test files.

---

## Part 1 — Architecture & Execution Flow

### 1.1 System Architecture

```
config.yml
    │
    ▼
app.py (FastAPI :8080)
    ├── /api/scan ──► run_scan() in scanner.py
    │                      │
    │         ┌────────────┼────────────┐
    │         ▼            ▼            ▼
    │   fetch_history  classify_      nse_indices
    │   (yfinance /    regime()       (symbol list)
    │   openalgo /     regime.py
    │   tvdatafeed /
    │   twelvedata)
    │         │
    │         ▼  (ThreadPoolExecutor × 10 workers)
    │   scan_symbol() per symbol
    │         ├── compute_utbot_signals()
    │         ├── compute_sr_signals()
    │         ├── compute_momentum_signals()   [if enabled]
    │         ├── compute_mean_reversion_signals() [if enabled]
    │         ├── compute_momentum_chatgpt_signals() [if enabled]
    │         ├── evaluate_composite_signals()
    │         ├── check_mtf_confirmation()
    │         ├── calculate_risk_reward()
    │         ├── signal_grader.grade_signal()
    │         └── risk/regime gates → auto-order or alert
    │
    └── /api/order ──► trading_adapter.place_order()
                            └── PositionMonitor.open_position()
                                    ├── trade_db.open_position_db()
                                    ├── WS subscribe (OpenAlgo)
                                    └── Telegram alert

trade_management/monitor.py
    ├── _ws_connect_loop() [daemon thread]
    ├── _monitoring_loop() [daemon thread — fallback HTTP poll]
    └── _process_tick(pos, ltp)
            ├── rules_engine.evaluate(pos, ltp, tm_cfg)
            └── executor.dispatch(action, pos, ltp, config, ...)
```

---

## Part 2 — Trading Strategy Review

### 2.1 Strategy Overview

The bot implements **4 active signal engines** (UT Bot, S/R Channels, Momentum, Mean Reversion)
plus a 5th optional Momentum-ChatGPT swing engine. The primary production config
has only UT Bot + S/R Channels active. The strategy is intraday (product=MIS, timeframe=5m),
trading NSE equities from the NIFTY and BANKNIFTY constituents.

### 2.2 UT Bot Engine — Findings

**Logic:** Faithful Python port of LonesomeTheBlue's Pine Script UT Bot Alerts.
ATR period = 2, key_value = 1.0. Uses Wilder's EWM smoothing. Iterative trail
calculation correctly mirrors Pine's bar-by-bar logic.

**Issues found:**

- **[CRITICAL] Very short ATR period (2) causes extreme whipsaw in volatile stocks.**
  The Pine Script default of `key_value=1.0, atr_period=2` was designed for specific
  liquid instruments. For NSE equities on 5m bars, this generates excessive false signals.
  No validation that `atr_period >= 5` is enforced. Signals on illiquid stocks (e.g., RPOWER
  in the config) with ATR(2) will be extremely noisy.

- **[HIGH] UT buy/sell signal definition is over-broad.** In `signals.py:310-311`,
  `ut_buy` is defined as:
  `((src > xATR) & above) | ((pos_s == 1) & (pos_s.shift(1) != 1))`
  The second clause fires buy on EVERY bar that first transitions to a long position,
  including very early bars where ATR is still warming up. This is more permissive than
  the original Pine Script intent.

- **[MEDIUM] `signal_on_closed_bar` config key is never used** in `compute_utbot_signals()`.
  The config has `signal_on_closed_bar: true` but `signals.py` ignores it — the signal
  always evaluates on the last bar whether it is closed or not. `_is_last_candle_incomplete()`
  exists but is never called from `compute_utbot_signals`. The composite evaluator
  references `config.get("strategy", {}).get("signal_on_closed_bar")` but the composite
  is evaluated AFTER all engines run, so the running-bar exclusion never happens.

- **[MEDIUM] Heikin Ashi implementation is approximate.** Uses only `(O+H+L+C)/4` for
  the HA close without computing HA open iteratively. This differs from standard HA
  calculation and produces a different signal set.

### 2.3 S/R Channels Engine — Findings

**Logic:** Port of the Pine Script SR Channel indicator. Finds pivot H/L, clusters
into zones by distance, scores by touch count + VPVR POC boost, greedy selects
top-N non-overlapping zones.

**Issues found:**

- **[HIGH] VPVR POC strength multiplier of 2.0× is non-configurable and extreme.**
  In `signals.py:462`, `z[0] *= 2.0`. This doubles the strength of any zone that
  contains the volume POC. There is no guard: a zone that touches the POC jumps to
  twice the strength of any other zone, unconditionally. This can over-ride a zone
  with many structural touches in favour of a high-volume zone with fewer touches.
  The multiplier should be configurable.

- **[MEDIUM] `proximity_pct` is applied as `close * proximity_pct / 100`** (line 620),
  which means a `proximity_pct: 0.2` config setting creates a proximity band of only
  0.2% of price. For stocks at ₹500, this is ₹1 — smaller than a typical tick cluster.
  Many legitimate S/R touches will be missed. The docstring says this is "% from boundary"
  which matches the implementation, but the effective threshold is very tight.

- **[LOW] Pivot detection uses symmetric rolling window**, which requires `prd` bars
  *after* the pivot to confirm it. This introduces a structural look-ahead by `pivot_period`
  (default 10) bars. While this is identical to the Pine Script source behavior (Pine's
  `pivothigh()` also looks ahead), it means the S/R zones are built with data the bot
  would not have had at the time in live trading. The zones are historical structural
  levels which is the correct interpretation, but calling these "live" signals is
  technically a form of look-ahead in that zone selection uses future data to confirm past pivots.

### 2.4 Momentum Engine — Findings

- **[HIGH] min_score threshold inconsistency.** Config has `min_score: 60` but the
  code in `signals.py:965` reads `cfg.get("min_momentum_score", 70)` — the key name
  is `min_momentum_score` in code but `min_score` in config. The threshold always
  defaults to 70 regardless of the config value. Same issue in mean reversion:
  `signals.py:1105` reads `cfg.get("min_mr_score", 70)` but config uses `min_score`.

- **[MEDIUM] Volume component gives identical points to both buy and sell signals.**
  In `signals.py:898-900`, `vol_pts` is added to both `buy_score` and `sell_score`
  when volume surges. A volume surge is directionally ambiguous (it can be buying or
  selling pressure) — both directions should not receive equal credit.

- **[MEDIUM] BB component buy signal fires when close > bb_upper**, which is a
  *breakout* signal, not a continuation signal. This contradicts the "trend continuation"
  intent. A breakout above BB is typically mean-reverting unless combined with tight
  consolidation (squeeze). Without a squeeze filter, this creates false signals.

### 2.5 Mean Reversion Engine — Findings

- **[HIGH] RSI divergence is computed with a simple price shift, not true pivot-based
  divergence.** In `signals.py:1091-1100`, "bearish divergence" is defined as
  `price > price[lookback_bars_ago] AND rsi < rsi[lookback_bars_ago]`.
  This is a very loose definition that fires on almost any pullback in a rising market.
  True divergence requires price making a *higher high* at a *significant swing point*
  while RSI makes a *lower high* at the corresponding swing point. The current
  implementation will generate excessive false signals.

- **[MEDIUM] min_mr_score key mismatch** — same as Momentum engine (see above).

### 2.6 Composite Signal Evaluation — Findings

**Review of `evaluate_composite_signals()` in `signals.py`:**

- **[HIGH] When no engines are enabled, `composite["buy"]` and `composite["sell"]`
  remain False** but the function returns a valid dict, and `scan_symbol()` will
  produce no results — this is correct behavior, but there is no warning to the operator.
  Misconfiguration (all engines disabled) silently produces zero signals.

- **[MEDIUM] MTF score adjustment in `_build_result` adds +15 for confirmation and
  -10 for counter-trend.** These adjustments are asymmetric and applied to `setup_score`
  (a different metric from `grade`). A counter-trend score is reduced by -10 but still
  passed through unless `mtf_filter_enabled=True`. The operator may not realize that
  counter-trend signals are still being generated and alerted.

### 2.7 Risk/Reward Calculation — Findings

- **[CRITICAL] Entry price used for R:R is `last_row["close"]` from the last candle
  in the historical fetch.** This is the close price of the most recently completed
  5m bar, NOT the live market price. The actual entry fill price from a MARKET order
  will differ. In high-volatility conditions or gap openings, the difference can be 1-3%,
  completely distorting R:R calculations and making the SL/target levels meaningless.

- **[HIGH] Stop-loss and target in auto-order block fall back to `close_price * 0.99`
  and `close_price * 1.02`** (scanner.py:1323-1324) when no S/R or ATR-based levels
  are computed. This flat percentage SL takes no account of the stock's actual volatility
  (ATR). For a stock with 0.5% daily range, a 1% SL is 2× ATR and will almost never
  be hit. For a stock with 3% daily range, a 1% SL is hit routinely.

### 2.8 Market Regime Classification — Findings

- **[MEDIUM] Regime gate is disabled by default** (`gate_enabled: false`). The regime
  is classified and tagged but has no effect on order execution unless the operator
  explicitly enables it. This means signals in "chop" and "high_vol_chop" regimes
  are still executed by default — potentially the worst times to trade a trend-following
  system.

- **[LOW] Regime is classified from NIFTY50 index data**, not from the stock itself.
  A stock can be trending strongly while NIFTY50 is in chop, and vice versa. A
  per-stock regime check would be more accurate but computationally heavier.

### 2.9 Position Sizing — Findings

- **[CRITICAL] Default sizing mode is "legacy"**, which routes to `compute_quantity()`
  using `openalgo.capital_per_trade`. If this is not set, it falls back to
  `openalgo.order_quantity: 1`. This means the bot defaults to trading **exactly 1 share**
  regardless of capital. A 1-share trade on a ₹2000 stock risks ₹20 on SL (1%) —
  negligible. On a ₹50 stock, 1 share is ₹0.50 at stake. This is not viable for real
  capital deployment.

- **[HIGH] Risk-based sizing (`sizing_mode: risk_based`) is the correct mode but
  is not the default.** When it IS enabled, the formula is correct:
  `qty = floor((capital × risk_pct) / |entry - sl|)`. However, the `stop_loss` passed
  to the sizer in `scanner.py:1323` is `float(r.get("stop_loss") or close_price * 0.99)` —
  the fallback flat SL causes risk-based sizing to misbehave when no technical SL exists.

- **[MEDIUM] Portfolio exposure cap of 300% of per-trade capital** effectively means
  the bot can have up to 3 full-size positions open simultaneously. With capital=₹1L,
  that is ₹3L of open notional. This feels conservative by intent but the naming
  ("300%") is counterintuitive — most risk managers express this as "max 3 concurrent
  full positions."

### 2.10 Look-Ahead Bias Assessment

- **S/R zones use `pivot_period` bars of future data to confirm each pivot.**
  This is inherent in the Pine Script design and is correct for *structural level
  identification* — the bot is not predicting future prices, it is identifying where
  price has historically reacted. This is NOT a look-ahead bias in the traditional
  backtesting sense, but it must be understood: zone quality improves with more data.

- **Historical win-rate backtest in `calculate_historical_win_rate()`** uses future
  bars (`high_v[i + 1:]`) to determine if TP/SL was hit — this IS look-ahead bias
  in a backtest context. However, this function is only used for display/annotation,
  not for signal generation. Not a live-trading issue.

- **No survivorship bias safeguard.** The segment lists (NIFTY, BANKNIFTY) contain
  current constituents. Historical analysis on these will be biased because companies
  that were delisted or kicked out of the index are not included.

---

## Part 3 — Code Quality Review

### 3.1 Critical Code Issues

**[CRITICAL] Race condition between risk gate check and order placement (scanner.py:1204-1317)**

The risk limits gate reads `open_positions` from DB at the start of the auto-order loop
(scanner.py:1204) and then reuses this snapshot for ALL candidates in the same scan.
If the scan processes 5 BUY signals and all 5 pass the risk gate (max_concurrent=5),
all 5 orders are placed even though only the first should pass. The in-memory snapshot
is updated (line 1472) but this is too late — the risk gate has already approved all 5
before the first order is placed.

**Critically**, the `idx_unique_open_position` partial index in SQLite and the
`open_position_db()` upsert logic (trade_db.py:143-183) will catch some duplicates,
but only at the DB layer *after* the orders are already placed with the broker.
**Duplicate live orders will be sent to the broker in a fast scan.**

**[CRITICAL] Entry price is the historical candle close, not live market price.**

In `scanner.py:1322`, `close_price = float(r.get("close") or 0.0)` is the close
of the last historical bar. MARKET orders are placed at this price in the order request
(`req.price = close_price`). For a MARKET order, the broker ignores the price field
and fills at market. However, `trade_db.open_position_db()` records this stale
`close_price` as the `entry_price`. The position monitor then computes SL and target
relative to this stale entry. For fast-moving stocks, the actual fill may be 0.5-2%
away, making the SL/target calculations wrong from the start.

**[CRITICAL] `open_position_db()` upsert logic can modify an existing position instead
of rejecting a duplicate order (trade_db.py:149-183).**

If a second order for the same symbol arrives (e.g., two scan cycles both signal BUY
for RELIANCE before the first order settles), `open_position_db()` *adds quantities
together* and computes an average entry price. This means the bot can silently pyramid
into a position — increasing size beyond `order_quantity` — without any gate checking
whether this is intentional. The `check_can_open_new` gate would have already passed
because the position wasn't in the DB yet when the second signal was evaluated.

**[HIGH] No fill confirmation for order placement — entry price is always the signal price.**

In `scanner.py:1419-1452`, the code attempts to verify the filled quantity by querying
`oa_client.orderbook()` 2 seconds after placement, but this is only for QUANTITY verification.
The PRICE at which the order filled is never queried and never stored. The position is
always registered at `close_price` (historical bar close), not the actual fill price.

**[HIGH] The `_process_tick()` method in `monitor.py:242-272` acquires no lock for
WS ticks.** The comment says "This method is called under lock for WS ticks" but the
WS callback `_on_ltp_tick()` (lines 181-187) releases the lock before calling
`_process_tick`. The lock is only held for the `matching = [...]` list comprehension.
After that, `_process_tick` is called without the lock — meaning position state
(`pos["current_sl"]`, `pos["quantity"]`, etc.) can be mutated by both WS tick and
HTTP polling simultaneously.

**[HIGH] `executor.dispatch()` modifies `pos` dict (via `_apply_sl_update`,
`_apply_profit_lock`, `execute_partial_exit`) while the same `pos` reference is used
by the monitoring loop without synchronization.**

The `pos` dict is shared between `active_positions[pos_id]` and the tick processor.
Multiple concurrent ticks for the same symbol (e.g., WebSocket flooding) can cause
`pos["current_sl"]` to be updated multiple times in the same tick cycle, potentially
causing duplicate SL modification orders to the broker.

### 3.2 High-Severity Code Issues

**[HIGH] `_timeout_wrapper()` in scanner.py creates a new ThreadPoolExecutor per call.**

`_timeout_wrapper` (scanner.py:115-128) creates a `ThreadPoolExecutor(max_workers=1)`
for every single yfinance download call. With 50+ symbols scanning in parallel, this
creates 50 threadpools of 1 thread each. Threadpool creation is expensive. This should
be implemented with a shared executor or `concurrent.futures.wait()` with a timeout.

**[HIGH] The auto-order loop (scanner.py:1233) uses `buy_results + sell_results`
regardless of `allowed_actions`.** The SELL signal filtering only happens inside the
loop. For a large universe (200+ stocks), iterating all sell signals when
`allowed_actions=BUY_ONLY` wastes time and increases the window for the race condition.

**[HIGH] The `get_open_positions()` call at scanner.py:1204 does not lock.**
`trade_db.py:282-291` has no locking on the SELECT query. While SQLite WAL mode
provides read concurrency, the snapshot is taken without holding any application-level
lock, and positions added by the `trade_management/monitor.py` thread between the
snapshot and order placement are invisible to the risk gate.

**[HIGH] YFinance data used as entry price for MARKET orders on NSE stocks.**
`yfinance` data for NSE 5m candles has a typical delay of 5-15 minutes. A signal
generated from a yfinance candle at 14:45 might be acting on price data from 14:30.
With `data_source: openalgo` (the current config) this is less of an issue, but the
architecture allows switching to yfinance for live trading without any safeguard.

**[HIGH] No market hours check.** The scan runs at the configured interval regardless
of whether NSE is open. The config notes "Market hours check disabled by default".
A scan running outside market hours (9:00-15:30 IST) will use stale end-of-day data
for signal generation and may attempt to place orders that will be rejected or queued.

**[MEDIUM] `segment_cache.json` persists segment data between runs.** If the NSE
updates index constituents (quarterly rebalancing), the bot will continue to scan
removed stocks until the cache is manually cleared or TTL expires. There is no TTL
mechanism visible in `nse_indices.py` based on the file list; the segment cache may
be stale.

**[MEDIUM] Config loading in `run_scan()` uses `copy.deepcopy(config)` and re-assigns
`config = copy.deepcopy(config)` at line 965.** This means the CLI mode overrides applied
at lines 954-963 are applied to `strat` and `sr_cfg` but then `config` is deep-copied again,
potentially losing those override values if `config["strategy"]` wasn't updated yet at
the deepcopy point. Code review suggests this works correctly because `config["strategy"] = strat`
happens at line 966, before the second deepcopy — but the ordering is fragile and non-obvious.

**[MEDIUM] Telegram bot token and chat ID are stored in plaintext in `config.yml`.**
The git repository contains the actual token (`8654539518:AAEzx...`) and chat ID.
If this repository is ever pushed to a public or shared remote, the Telegram bot is
fully compromised. This is visible in `config.yml:bot_token` and `config.yml:chat_id`.

**[MEDIUM] API keys for OpenAlgo, Flattrade, Shoonya, Dhan, and MStock are in `config.yml`
in plaintext.** No `.env` loading, no secrets manager, no environment variable support.

### 3.3 Medium-Severity Code Issues

**[MEDIUM] `_openalgo_place_order()` in `trading_adapter.py:166` attempts to import from `app`:**
```python
from app import _get_oa_client
```
This creates a circular import dependency (`trading_adapter` → `app` → `trading_adapter`).
It works only because `app` is the FastAPI entry point and has already been fully loaded.
If `trading_adapter` is imported standalone (e.g., in tests or scripts), this import fails.

**[MEDIUM] `compute_quantity_risk_based()` in `risk_limits.py:482-491` caps notional
against capital but the cap is only applied to `risk_based` mode.**
In `capital_pct` mode, `qty = floor(capital / entry)` with no notional cap.
If `capital = ₹1L` and `entry = ₹50` (low-priced stock), `qty = 2000 shares`.
For 2000 shares of RPOWER at ₹50 = ₹1,00,000 full notional in one trade —
this is within the capital limit, but it is 100% concentration in one stock.

**[MEDIUM] `check_can_open_new()` in `risk_limits.py` compares the daily loss
using `pnl_pct` summed across trades.** The `pnl_pct` stored per trade is percentage
PnL relative to the entry price of THAT trade, not relative to total capital.
Summing `pnl_pct` across trades gives a meaningless composite:
a -2% loss on a ₹10K position sums identically with a -2% loss on a ₹1L position.
The daily loss gate should use rupee PnL (`daily_loss_stop_rupees`) or normalize
each trade's PnL to capital. The `daily_loss_stop_rupees` alternative exists but is
optional. The default `daily_loss_stop_pct` check is mathematically incorrect.

**[MEDIUM] EOD square-off check in `rules_engine.py:186-198` fires on EVERY tick
after the cutoff time.** Once 15:15 IST passes, EVERY LTP update for EVERY position
triggers `ACTION_EXIT_EOD`. The first dispatch calls `execute_full_exit()` which
places an exit order and removes the position from `active_positions`. But if the
exit order fails (`res.get("status") != "success"`), the position is NOT removed
from `active_positions`, and every subsequent tick will try to place another exit order.
This can result in duplicate exit orders flooding the broker.

**[MEDIUM] `execute_full_exit()` in `executor.py:168` sets status to "ERROR" on
order failure but does NOT remove the position from `active_positions`.** The
position remains in the active dict and will retry the exit on every subsequent tick.
The "ERROR" status in the DB is also not the final "CLOSED" status, so `get_open_positions()`
will still return this position, contributing to the open position count and
potentially blocking new trades via the concurrent-position cap.

**[MEDIUM] The `_monitoring_loop` syncs `active_positions` from DB on every poll cycle
(monitor.py:199-210).** If a position is set to "ERROR" status (from a failed exit),
it will be removed from the DB sync because `get_open_positions()` returns only
`status = 'OPEN'` rows. But the "ERROR" status in the DB means the actual broker
position may still be open. This creates a divergence between DB state and broker state.

**[MEDIUM] `signals.py:310-311` — UT Buy signal fires on the bar that first crosses
into a long position AND on any subsequent EMA crossover.** The `(pos_s == 1) & (pos_s.shift(1) != 1)`
clause means every time the POSITION TRANSITIONS to +1, a buy signal fires. Since
`pos` can oscillate between +1 and 0 without the EMA crossing (just bouncing around
the trail), this generates repeated buy signals for the same trend. In the 5-minute
lookback window (`signal_lookback_candles=2`), only the last 2 bars are checked, so
this is partially mitigated but not fully.

### 3.4 Architecture & Design Observations

**[LOW] `ENGINE_REGISTRY` in `signals.py` is a well-designed extensibility point**
that allows new engines to be added by appending to the list. However, the `scan_symbol()`
dispatch in `scanner.py:730-767` uses a chain of `if/elif` blocks keyed on `engine["key"]`.
Adding a new engine requires modifying `scanner.py` AND `signals.py` — the registry
doesn't fully encapsulate the dispatch logic.

**[LOW] `trade_manager.py` is a backward-compatible shim** that simply re-exports
from `trade_management/`. This is correct pattern for incremental refactoring.

**[LOW] The `strategies/momentum_chatgpt/` package evaluates stocks individually**
but portfolio-level rules (sector exposure, correlation filtering, max 8 positions)
are in `portfolio.py`. These portfolio rules are NOT enforced in `run_scan()`
or the risk gates — they appear to be design intent that is not yet wired into
the main execution path.

---

## Part 4 — Trading System Reliability Issues

### 4.1 Order Execution Reliability

| Issue | Severity | Status | Detail |
|-------|----------|--------|--------|
| Entry price is historical close, not fill price | Critical | ⚠️ **Partial** | Fill quantity is now verified via orderbook; fill price is still `close_price` (`scanner.py:1461`) |
| Race condition: multiple signals can bypass concurrent-position cap | Critical | ✅ **Fixed** | `open_positions` list updated after each order; risk gate sees consistent snapshot |
| Upsert logic pyramids into existing positions silently | Critical | ✅ **Fixed** | UNIQUE partial index + `IntegrityError` handler now rejects duplicates (`trade_db.py:217`) |
| No fill confirmation / actual fill price recorded | High | ⚠️ **Partial** | Filled quantity confirmed; fill price still not captured from orderbook |
| Failed exit order causes infinite retry | Medium | ❌ **Open** | No retry counter in `executor.py`; position stays in `active_positions` and retries on every tick |
| MARKET order price field = historical close | Medium | ⚠️ **Partial** | Quantity now reflects broker fill; price anchor remains wrong |

### 4.2 Data Freshness & Staleness

| Issue | Severity | Status | Detail |
|-------|----------|--------|--------|
| No market hours gate | High | ✅ **Fixed** | `_is_market_hours()` added (`scanner.py:1678`); checks IST time + weekends; disabled by default via `bot.market_hours_check` |
| yfinance data has 5-15 min delay for NSE | High | ⚠️ Open | Architectural risk when `data_source` is switched to yfinance; no safeguard |
| Signal freshness check uses `r.get("timestamp")` | Medium | ⚠️ Open | `signal_time` is bar open time, not wall-clock |
| Segment cache may be stale after index rebalancing | Medium | ⚠️ Open | No TTL on `segment_cache.json` |

### 4.3 Position Reconciliation

| Issue | Severity | Status | Detail |
|-------|----------|--------|--------|
| No reconciliation with broker position book on startup | High | ❌ **Open** | `monitor.start()` still loads from SQLite only; no broker orderbook cross-check |
| No broker order-status polling for PENDING orders | Medium | ✅ **Fixed** | Pending orders are now detected and skipped (`scanner.py:1439-1441`) |
| Partial fill path skips position registration if still PENDING | Medium | ✅ **Fixed** | `continue` on PENDING status prevents ghost position records |

### 4.4 Brokerage & Charges

- **[MEDIUM] No STT, brokerage, exchange charges, or SEBI fees are accounted for**
  in PnL calculations. For intraday equity MIS trades on NSE:
  - STT: 0.025% on sell side
  - Brokerage: typically ₹20 flat or 0.03% per side
  - Exchange transaction charges: ~0.003%
  - SEBI charges, stamp duty, GST on brokerage
  For a 2% target trade with 1% SL, real charges can reduce the net gain by
  0.1-0.2% — not trivial when targeting 2% moves.
  The `pnl_pct` in the DB and shown in Telegram alerts does NOT include these charges.

### 4.5 Slippage

- **[MEDIUM] No slippage model is applied.** MARKET orders on NSE 5m bars can have
  significant slippage, especially for mid/small cap NIFTY stocks in the first and last
  30 minutes of the session. A 5m bar close price to next-bar open spread of 0.2-0.5%
  is common. The bot's R:R calculations do not account for this.

---

## Part 5 — Security Review

| Issue | Severity | Status | Fix |
|-------|----------|--------|-----|
| Telegram bot token in plaintext config.yml | High | N/A | Personal local use — accepted as-is |
| OpenAlgo API key in plaintext config.yml | High | N/A | Personal local use — accepted as-is |
| Broker session tokens in plaintext config.yml | High | N/A | Personal local use — accepted as-is |
| Circular import trading_adapter → app | Medium | ⚠️ Open | Create a separate OA client factory module |
| No authentication on FastAPI endpoints | Medium | N/A | Personal local use — not required |
| Scanner.log may contain sensitive data | Low | ⚠️ Open | Verify log level doesn't log request payloads |

---

## Part 6 — Scores & Recommendations

### Overall Scores (Post-Fix Revision)

| Dimension | Before Fixes | After Fixes | Rationale |
|-----------|-------------|-------------|-----------|
| **Trading Strategy** | 5.5 / 10 | **6.5 / 10** | `signal_on_closed_bar` now works; `min_score` keys still mismatched; entry price still wrong |
| **Code Quality** | 6.5 / 10 | **7.5 / 10** | Race condition fixed, duplicate guard fixed, market hours added, locking improved; failed-exit retry still open |
| **Risk Management** | 5.0 / 10 | **6.0 / 10** | Duplicate and race-condition risks closed; daily loss pct math still wrong; no charges model |
| **Production Readiness** | 4.5 / 10 | **6.0 / 10** | Most critical path bugs fixed; fill price, failed-exit retry, broker reconciliation, and secrets remain |

### Remaining Issues (Open / Partial)

1. **[CRITICAL — Partial]** Entry price is still `close_price` (historical bar close), not actual broker fill price.
   Fill *quantity* is now confirmed from orderbook (`scanner.py:1438`) but `entry_price` on line 1461 is unchanged.
   SL and target levels in the position monitor remain anchored to a potentially stale price.

2. **[HIGH — Open]** Failed exit order causes infinite retry.
   `executor.py:166-169` sets status to `"ERROR"` but leaves the position in `active_positions`.
   Every subsequent LTP tick will attempt another exit order.

3. **[HIGH — Open]** Broker position reconciliation on startup.
   `PositionMonitor.start()` loads only from SQLite; no comparison with broker's live position book.

4. **[HIGH — Open]** Fix `min_score` config key mismatch in Momentum and Mean Reversion engines.
   `signals.py:965` reads `cfg.get("min_momentum_score", 70)` and `signals.py:1105` reads
   `cfg.get("min_mr_score", 70)` — the config key is `min_score` under both sections.
   Threshold changes in `config.yml` have no effect.

5. **[HIGH — Open]** Fix daily loss stop gate to use rupee-normalized PnL.
   `risk_limits.py:137` sums per-trade `pnl_pct` values which are not capital-normalized.

6. **[MEDIUM — Open]** `market_hours_check` is disabled by default (`bot.market_hours_check: false`).
   The function exists but does nothing until the config flag is enabled. Recommend enabling it by default.

### Recommended Improvements (Remaining)

1. Increase default `atr_period` from 2 to 10 for NSE 5m equities to reduce whipsaw.
   (Recommendation comment already added to `config.yml`.)

2. Implement correct ATR-based SL (use `ut_trail` or 1.5× ATR below entry) as the
   default SL instead of the flat `stop_loss_pct` fallback.

3. Add STT + brokerage + exchange charges to PnL calculations for accurate net returns.
   Create a `charges_model.py` that takes entry price, exit price, quantity, product,
   and returns total charges per-side.

4. Enable the market regime gate by default (`gate_enabled: true`) for the UT Bot engine
   in `chop` and `high_vol_chop` regimes. This is the biggest single improvement to
   signal quality.

5. Enable `market_hours_check: true` by default in the `bot` config section. The function
   is implemented; the flag just needs to be flipped.

6. Replace the `_timeout_wrapper` per-call ThreadPoolExecutor with a shared global
   executor or use `requests`-level timeout for yfinance.

7. Wire the `strategies/momentum_chatgpt/portfolio.py` sector exposure and correlation
   checks into the main risk gate pipeline so these portfolio constraints are actually
   enforced when the Momentum-ChatGPT engine is enabled.

8. Add integration tests that mock the broker API and verify:
   - No duplicate orders on concurrent scan completion
   - Correct SL/target registration after fill
   - EOD square-off does not retry after success

---

## Part 7 — Production Readiness Assessment (Updated)

### Paper Trading
✅ **Suitable now.** Race condition, duplicate guard, and locking fixes make the scanner
safe for paper trading with `order_mode: manual`. Recommend enabling the regime gate and
collecting 4–6 weeks of grade/engine win-rate data before going live.

### Small-Capital Live Trading (≤ ₹1 lakh)
⚠️ **Conditionally suitable** after completing the following remaining items:
- Fix entry price to use actual broker fill price (Remaining Issue #1).
- Fix failed exit retry (Remaining Issue #2).
- Enable `risk_limits.enabled: true` with conservative settings.
- Enable `market_hours_check: true` in config.
- Set `sizing_mode: risk_based` with `risk_per_trade_pct: 0.5–1.0`.
- Set `atr_period: 10` and test signal quality for 2+ weeks in paper mode first.

### Full-Scale Live Trading
❌ **Not recommended** until the following are additionally addressed:
- Broker position reconciliation on startup (Remaining Issue #4).
- STT/brokerage charges model (so reported PnL is net of costs).
- Stress-tested under 200+ symbol concurrent scanning.
- At least 3 months of paper trading data showing positive expectancy net of charges.

---

## Sub-Tasks for Implementation

Each sub-task below is self-contained and can be implemented independently.

### Sub-Task 1: Fix Entry Price & Fill Confirmation
**Status:** ⚠️ Partial — fill quantity confirmed; fill price not yet captured
**What was done:** `scanner.py:1418-1452` queries the orderbook after placement
and uses `filled_quantity` from the broker response as the registered quantity.
Pending orders are correctly skipped.
**What remains:** `entry_price` on line 1461 is still `close_price` (the last
historical bar close). The `average_price` (or equivalent) field from the filled
order is not extracted.
**Todo List:**
1. In the existing orderbook query block (`scanner.py:1435-1438`), also extract `avg_price` / `average_price` / `price` from `our_order`.
2. Assign it to `fill_price` (fall back to `close_price` if absent or zero).
3. Replace `"entry_price": close_price` at line 1461 with `"entry_price": fill_price`.
4. Update the Telegram message (line 1490) to show the fill price, not `close_price`.
5. Apply the same fix to `PositionMonitor.open_position()` (`monitor.py:308`) which also uses a passed-in price.
**Relevant Context:** `scanner.py:1435-1461`, `monitor.py:308`

---

### Sub-Task 2: Fix Race Condition in Auto-Order Loop
**Status:** ✅ Fixed
`open_positions` is refreshed from DB at the start of the auto-order loop and the
in-memory list is appended after each successful order (`scanner.py:1472-1481`).
The risk gate now sees consistent state across all candidates within a scan.

---

### Sub-Task 3: Fix `open_position_db()` Upsert Behavior
**Status:** ✅ Fixed
The UNIQUE partial index (`idx_unique_open_position`) and the `IntegrityError` handler
in `trade_db.py:217-228` now reject duplicate OPEN positions and return the existing
position ID with a warning log, rather than silently pyramiding quantities.

---

### Sub-Task 4: Add Market Hours Gate
**Status:** ✅ Fixed (gate disabled by default — one remaining action)
`_is_market_hours()` is implemented at `scanner.py:1678` and checks IST time,
weekends, and configurable `market_open`/`market_close` times from the `bot` config section.
**Remaining:** The flag `bot.market_hours_check` defaults to `false`. Enable it:
```yaml
bot:
  market_hours_check: true
  market_open: "09:15"
  market_close: "15:20"
```

---

### Sub-Task 5 (N/A): Secrets Management
**Status:** N/A — Personal local use; plaintext credentials in `config.yml` accepted as-is.

---

### Sub-Task 6: Fix min_score Config Key Mismatch
**Status:** [ ] pending
**Intent:** Align config key names with code expectations for Momentum and Mean Reversion engines.
**Expected Outcomes:**
- `config.yml: min_score` under `momentum` and `mean_reversion` is correctly read.
- Threshold changes in config take effect without code changes.
**Todo List:**
1. In `compute_momentum_signals()` (signals.py:965): change `cfg.get("min_momentum_score", 70)` to `cfg.get("min_score", 60)`.
2. In `compute_mean_reversion_signals()` (signals.py:1105): change `cfg.get("min_mr_score", 70)` to `cfg.get("min_score", 60)`.
3. Update the `config.yml` comments to document the key name correctly.
4. Add unit test asserting that a config with `min_score: 40` generates more signals than `min_score: 80`.
**Relevant Context:** `signals.py:965`, `signals.py:1105`, `config.yml:momentum.min_score`

---

### Sub-Task 7: Fix Daily Loss Stop Calculation
**Status:** [ ] pending
**Intent:** The `daily_loss_stop_pct` check sums per-trade `pnl_pct` which is mathematically incorrect.
**Expected Outcomes:**
- Daily loss gate correctly measures capital-normalized daily loss.
- Operators can set a meaningful `daily_loss_stop_pct` like `-2.0` meaning "stop if down 2% on total capital".
**Todo List:**
1. In `risk_limits.py:check_can_open_new()`, replace the `get_realized_pnl_pct_since()` call with `get_realized_pnl_rupees_since()`.
2. Normalize rupee loss to capital: `loss_pct = realized_pnl_rupees / capital * 100`.
3. Compare against `daily_loss_stop_pct`.
4. Update documentation in `risk_limits.py` to clarify the semantics.
5. Add a unit test verifying the correct calculation with multiple trades of different sizes.
**Relevant Context:** `risk_limits.py:126-144`, `trade_db.py:308-363`

---

### Sub-Task 8: Fix Failed Exit Retry Loop
**Status:** [ ] pending
**Intent:** Prevent infinite exit order spam when a single exit fails.
**Expected Outcomes:**
- Failed exit sets a retry counter on the position.
- After 3 failed attempts, position is flagged "EXIT_FAILED" and removed from active monitoring.
- Alert sent to Telegram requiring manual intervention.
**Todo List:**
1. Add `exit_retry_count` field to the in-memory position dict (not DB).
2. In `executor.execute_full_exit()`, on failure, increment `pos.get("exit_retry_count", 0)`.
3. When retry count >= 3, remove from `active_positions` and send a critical Telegram alert.
4. Add "EXIT_FAILED_MAX_RETRIES" as a valid `close_reason` in DB for audit.
**Relevant Context:** `executor.py:103-172`, `monitor.py:391-404`

---

### Sub-Task 9: Add Brokerage & Charges Model
**Status:** ❌ Open
**Intent:** Include all real trading costs in PnL calculations so reported returns are net of charges.
**Expected Outcomes:**
- `pnl_pct` and `pnl_rupees` stored in DB are net of all charges.
- Telegram alerts show gross and net PnL.
**Todo List:**
1. Create `charges.py` with `compute_charges(entry, exit, quantity, product, exchange)` function.
2. Implement NSE equity intraday (MIS) charges: STT 0.025% sell-side, exchange charges ~0.003% per side, SEBI charges, stamp duty, GST (18% on brokerage).
3. Make brokerage configurable (flat ₹20 or 0.03% per side) via `config.yml`.
4. Call `compute_charges()` in `execute_full_exit()` and subtract from PnL.
5. Store both `gross_pnl_pct` and `net_pnl_pct` in positions table.
**Relevant Context:** `executor.py:126-136`, `trade_db.py:schema`

---

