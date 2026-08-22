# Bot-NSE-Options — Automated NSE Options Trading Bot

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![Status: Active](https://img.shields.io/badge/status-active-brightgreen.svg)]()

An end-to-end automated **NSE Index Options** (Nifty, Bank Nifty, Fin Nifty, Sensex)
trading bot that combines the **UT Bot** trend-following signal, **Support/Resistance
Channels**, multi-timeframe confirmation, and rich risk guardrails — with a live
FastAPI + web dashboard and OpenAlgo broker integration.

> **⚠️ Live trading involves financial risk. Test in paper mode. Never trade with
> money you can’t afford to lose. All defaults are conservative; you own every
> live order this bot places.**

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌───────────────────┐
│   Web Dashboard │◄───►│   FastAPI (app) │◄───►│   OpenAlgo Broker │
│  (HTML/JS/CSS)  │     │                 │     │      (REST)       │
└─────────────────┘     └────────┬────────┘     └───────────────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
             ┌──────────┐  ┌──────────┐  ┌──────────┐
             │ scanner  │  │ risk_mgr │  │ trade_db │
             │ (signals)│  │ (gates)  │  │ (SQLite) │
             └────┬─────┘  └──────────┘  └──────────┘
                  │
                  ▼
             ┌──────────────────────────┐
             │  trade_management/       │
             │   monitor · rules_engine │
             │   executor · alerts      │
             └──────────────────────────┘
```

**Key modules:**

| File                              | Purpose                                                              |
|-----------------------------------|----------------------------------------------------------------------|
| `app.py`                          | FastAPI server, REST endpoints, dashboard hosting                    |
| `scanner.py`                      | Scan loop; UT-Bot + S/R + filters; auto-orders                       |
| `signals.py`                      | UT-Bot & S/R signal computation                                      |
| `risk_manager.py` ✨ **[Sprint-1]**| Centralized pre-trade risk gates + daily-loss circuit breaker         |
| `options_grid.py`                 | Strike grid (ATM ± N strikes, CE & PE)                               |
| `trading_adapter.py`              | OpenAlgo REST wrapper                                                |
| `trade_db.py` / `signal_db.py`    | SQLite persistence                                                   |
| `trade_management/`               | Position monitor, trailing-SL, auto-exit                              |
| `telegram.py`                     | Telegram alerts                                                      |
| `frontend/`                       | Dashboard UI                                                         |


---

## Feature Matrix

| Feature                          | Config Key                                    | Dashboard Toggle                     | Sprint |
|----------------------------------|-----------------------------------------------|--------------------------------------|--------|
| UT Bot signals                   | `strategy.ut_enabled`                         | Quick Filters → UT Bot Engine        | Core   |
| S/R Channels                     | `sr_channels.enabled`                         | Quick Filters → S/R Zones Engine     | Core   |
| **Kill switch**                  | `risk.kill_switch`                            | Header → KILL SWITCH button          | **1**  |
| **Duplicate-entry guard**        | `trading.dedup.enabled`                       | Quick Filters → Duplicate Entry Guard| **1**  |
| **Cool-down after exit**         | `trading.dedup.cooldown_minutes`              | Settings → Cool-Down (min)           | **1**  |
| **Directional gate (spot trend)**| `trading.directional_gate.enabled`            | Quick Filters → Directional Gate     | **1**  |
| **Min-Grade / Min-Score gate**   | `trading.min_grade` / `trading.min_score`     | Settings → Min Grade / Min Score     | **1**  |
| **Market-hours enforcement**     | `bot.market_hours_check`                      | Quick Filters → Market Hours Check   | **1**  |
| **Entry cutoff time**            | `bot.entry_cutoff_time`                       | Settings → No New Entries After      | **1**  |
| **Daily loss circuit breaker**   | `risk.daily_loss_limit.enabled` / `max_loss_pct` | Quick Filters → Daily Loss Limit  | **1**  |
| **Auto square-off on breach**    | `risk.daily_loss_limit.auto_square_off`       | Settings → Auto Square-Off           | **1**  |
| Trade management / trailing SL   | `trade_management.*`                          | Positions tab                        | Core   |
| Telegram alerts                  | `telegram.enabled`                            | Config only                          | Core   |

---

## Installation

```bash
git clone <repo>
cd Bot-NSE-Options
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:9000 in a browser.

---

## Configuration (`config.yml`)

Every feature is toggleable. Key sections:

- **`openalgo`** — API host & key. **Rotate & never commit real keys.**
- **`options`** — underlying, expiry, strike-gap.
- **`strategy`** — UT-Bot key value, ATR period, Heikin-Ashi.
- **`sr_channels`** — S/R zone width & lookback.
- **`bot`** — Port, auto-scan interval, market hours, entry cutoff.
- **`trading`** — Auto/manual mode, min grade/score, dedup, directional gate.
- **`trade_management`** — Trailing SL rules, break-even shift, monitor interval.
- **`risk`** — Kill switch, account equity, daily-loss limit + auto-square-off.
- **`telegram`** — Bot token, chat id, alert toggles.

Inline docs live in `config.yml` itself.

---

## Dashboard Walkthrough

### Top Header
- **LTF / HTF**: Timeframe selectors.
- **Order Mode**: `Manual` / `Auto`.
- **Auto-refresh**: 60-second re-scan.
- **Run Scanner**: Immediate scan.

### Sprint-1 Risk Status Strip
- **Trading:** `ALLOWED` (green) or blocked reason (red pulsing banner).
- **Day P&L:** Realized + unrealized, colored by sign.
- **Realized / Unrealized:** Breakdown.
- **Market:** OPEN / PRE_MARKET / POST_MARKET / WEEKEND / cutoff.
- **KILL SWITCH button:** Instant halt of new auto-orders (with confirm).

### Dashboard Tab
- **Left:** BUY / SELL signal tables (click to trade).
- **Right — Quick Filter Controls:** Core toggles + Risk Guardrails section
  (Duplicate Entry Guard, Directional Gate, Market Hours, Daily Loss Limit).

### Positions Tab
Live open trades with LTP, unrealized P&L, SL, target, manual close.

### Settings Tab
- Scanner & Option Grid Settings.
- **Risk Management & Guardrails (Sprint-1):** Equity, daily-loss %, auto-square-off,
  min grade, min score, cool-down, market open/close, entry cutoff.


---

## Strategy Logic

1. **Universe** — Each scan builds a strike grid ATM ± N × strike-gap around the
   underlying spot; both CE and PE.
2. **Signal** — UT-Bot (ATR-trailing stop) on each option OHLC. Optional Heikin-Ashi.
3. **S/R zones** — Optional filter requiring price near support (buys) / resistance (sells).
4. **Filters** — EMA trend, volume, MTF (HTF UT alignment), squeeze.
5. **Grade & Score** — 0–100 score, mapped to A/B/C/D grade.
6. **Risk gates (Sprint-1)** — `risk_manager.can_place_order()` runs six checks;
   first failure blocks the trade with a logged reason.
7. **Auto-execution** — OpenAlgo order → `trade_db` row → `PositionMonitor` starts
   trailing SL / target management.
8. **Exit** — SL / target / opposite signal / manual close.
   `risk_manager.record_exit()` starts the cool-down clock.

---

## Risk Management & Guardrails

Six gates run in order on every candidate order:

| # | Gate                     | Fails when                                                      | Reason string                       |
|---|--------------------------|-----------------------------------------------------------------|-------------------------------------|
| 1 | Kill switch              | `risk.kill_switch: true`                                        | `KILL_SWITCH_ON`                    |
| 2 | Market hours             | Weekend / pre / post / after entry cutoff                       | `WEEKEND` / `PRE_MARKET` / `POST_MARKET` / `AFTER_ENTRY_CUTOFF(HH:MM)` |
| 3 | Daily loss limit         | Day P&L % ≤ `-max_loss_pct` of `account_equity`                 | `DAILY_LOSS_BREACH(-X%<=-Y%)`       |
| 4 | Min grade / min score    | Signal below `min_grade` OR below `min_score`                   | `GRADE_X_BELOW_Y` / `SCORE_X_BELOW_Y` |
| 5 | Directional gate         | CE-BUY on down-trend OR PE-BUY on up-trend                      | `SPOT_DOWNTREND_BLOCKS_CE` / `SPOT_UPTREND_BLOCKS_PE` |
| 6 | Duplicate-entry / cool-down | Same-symbol OPEN OR exited within cool-down                  | `DUP_OPEN_POSITION(sym)` / `COOLDOWN(Nm_left)` |

`PositionMonitor` also re-checks the daily-loss limit every poll cycle and, if
`risk.daily_loss_limit.auto_square_off` is true, flat-closes every OPEN position
when the limit is breached.

### Safe defaults (Sprint-1)

| Key                                        | Default  |
|--------------------------------------------|----------|
| `risk.kill_switch`                         | `false`  |
| `risk.account_equity`                      | `100000` |
| `risk.daily_loss_limit.enabled`            | `true`   |
| `risk.daily_loss_limit.max_loss_pct`       | `3.0`    |
| `risk.daily_loss_limit.auto_square_off`    | `true`   |
| `trading.min_grade`                        | `"B"`    |
| `trading.min_score`                        | `60`     |
| `trading.dedup.enabled`                    | `true`   |
| `trading.dedup.cooldown_minutes`           | `5`      |
| `trading.directional_gate.enabled`         | `true`   |
| `bot.market_hours_check`                   | `true`   |
| `bot.market_open` / `market_close`         | `09:15` / `15:30` |
| `bot.entry_cutoff_time`                    | `14:45`  |

Fully backwards compatible via `.get(..., default)` everywhere.

---

## Sprint Roadmap

| Sprint | Theme                    | Status         |
|--------|--------------------------|----------------|
| 1      | Loss-Stoppers            | ✅ **Complete** |
| 2      | Signal-Quality           | ✅ **Complete** |
| 3      | Position Sizing          | ✅ **Complete** |
| 4      | Alpha Enhancements       | ✅ **Complete** |
| 5      | Production Hardening     | ✅ **Complete** |
| 6      | Observability & Resilience | ✅ **Complete** |

---

## Dynamic Position Sizing (Sprint-3)

Sprint-3 replaces the static `trading.options.quantity: 65` with **risk-based
dynamic sizing**. Every trade risks a controlled fraction of account equity
computed from the entry↔stop-loss distance, and all quantities snap DOWN to
lot-size. Two new **portfolio-level gates** cap total exposure and
concurrent positions.

### Sizing formula (fixed-fractional)

```
risk_budget    = account_equity × risk_per_trade_pct × grade_multiplier
raw_quantity   = risk_budget / |entry_price − stop_loss|
final_quantity = floor_to_lot_size(raw_quantity)   # 0 if below one lot
```

**Example:** ₹1,00,000 equity · 1% risk · Entry ₹200 · SL ₹190 · NIFTY lot 75
→ budget ₹1,000 → raw qty 100 → floored to **75** → risk actually taken ₹750.

### Sizing modes

| Mode              | Behaviour                                                              |
|-------------------|------------------------------------------------------------------------|
| `fixed_fractional`| Constant % of equity risked per trade. Default. Predictable, safe.     |
| `kelly`           | Rolling win-rate + payoff ratio from last N closed trades → fractional-Kelly. Falls back to fixed-fractional until sample ≥ `kelly_min_trades`. |

### New portfolio gates (in `risk_manager.can_place_order`)

| Gate                        | Rejects when                                              |
|-----------------------------|-----------------------------------------------------------|
| Max concurrent positions    | Open positions ≥ `max_concurrent_positions` (default 3)   |
| Max portfolio exposure %    | Open premium + this trade's premium > `max_portfolio_exposure_pct` × equity (default 15%) |

Reason strings:
`MAX_CONCURRENT_POSITIONS(N>=cap) / PORTFOLIO_EXPOSURE_CAP(X.X%>Y.Y%) / SIZING_BELOW_ONE_LOT / SIZING_INVALID_STOP / SIZING_ZERO_EQUITY`

### Grade multiplier (opt-in)

When `grade_multiplier_enabled: true`, `risk_per_trade_pct` is scaled by signal grade:

| Grade | Multiplier | Effective risk on 1% base |
|-------|-----------:|--------------------------:|
| A     | 1.00       | 1.00%                     |
| B     | 0.75       | 0.75%                     |
| C     | 0.50       | 0.50%                     |

Deploys more capital on higher-conviction setups. Off by default (opt-in).

### Safe defaults (Sprint-3)

| Key                                              | Default            |
|--------------------------------------------------|--------------------|
| `position_sizing.enabled`                        | `true`             |
| `position_sizing.mode`                           | `fixed_fractional` |
| `position_sizing.risk_per_trade_pct`             | `1.0`              |
| `position_sizing.max_risk_per_trade_pct`         | `3.0` (hard cap)   |
| `position_sizing.max_portfolio_exposure_pct`     | `15.0`             |
| `position_sizing.max_concurrent_positions`       | `3`                |
| `position_sizing.grade_multiplier_enabled`       | `false` (opt-in)   |
| `position_sizing.kelly_fraction`                 | `0.25` (quarter)   |
| `position_sizing.kelly_min_trades`               | `20`               |

### Dashboard additions

- **Sidebar toggles:** "Dynamic Position Sizing" and "Grade Multiplier"
- **Risk-strip pills:** live `Exposure: 3.2% / 15%` and `Positions: 1 / 3` — colour-coded (green→yellow→red as you approach the cap)
- **Settings tab card:** all 7 sizing thresholds editable
- **Scan rows:** each result shows computed `Qty NN · ₹XXX risk (Y.YY%)` under the grade badge, with a hover tooltip explaining the mode/reason. QTY input auto-prefills with the sized quantity.

### New API endpoint

| Method | Path                              | Purpose                                     |
|--------|-----------------------------------|---------------------------------------------|
| POST   | `/api/position-sizing/settings`   | Persist sizing thresholds (mode, risk%, caps, Kelly params) |

Extended: `POST /api/filters` now accepts `position_sizing_enabled`,
`grade_multiplier_enabled`.
`GET /api/risk/status` now includes a `position_sizing` block with live
`open_positions`, `total_premium`, `exposure_pct`.

---

## Signal Quality & Scoring (Sprint-2)

Sprint-2 adds **three pre-trade filters** and **one intraday circuit breaker** on top
of Sprint-1's six risk gates. Together they reject low-probability signals BEFORE
the Sprint-1 min-grade gate even sees them.

### New pre-trade filters

| Filter                    | Config key                              | Rejects when                                              |
|---------------------------|-----------------------------------------|-----------------------------------------------------------|
| ATR% floor / ceiling      | `signal_quality.atr_pct_min / _max`     | Option ATR% < 0.5% (dead) or > 8% (post-news chaos)       |
| ADX trend-strength        | `signal_quality.adx_min`                | Underlying ADX < 20 (chop zone — #1 options-buying killer)|
| Spread & liquidity        | `signal_quality.max_spread_pct` + `min_open_interest` | Bid-ask > 1.5% of LTP OR OI < 500              |
| Consecutive-loss breaker  | `risk.consecutive_loss_breaker`         | 3+ losing trades today → halt 30 min                      |

Reason strings:
`ATR_TOO_LOW / ATR_TOO_HIGH / ADX_CHOP / WIDE_SPREAD / LOW_OI / CONSECUTIVE_LOSSES / CIRCUIT_BREAKER_ACTIVE(Xm_left)`

### Transparent weighted scoring

The legacy 0–100 `setup_score` is replaced (when `signal_quality.scoring_enabled: true`)
with a fully-transparent weighted score:

| Factor        | Weight |
|---------------|-------:|
| UT-Bot signal | 25     |
| S/R proximity | 15     |
| MTF alignment | 20     |
| Volume vs SMA | 10     |
| ADX strength  | 15     |
| ATR% in-band  | 10     |
| Spread OK     |  5     |
| **Total**     |**100** |

Score → grade: **A ≥ 75 · B ≥ 60 · C ≥ 45 · D < 45**

Every scan row exposes:
- `atr_pct`, `adx` — displayed under the grade badge
- `score_breakdown` — per-factor `{weight, earned, pass, detail}` visible as a
  tooltip on hover of the grade badge
- `sq_reject_reason` — shown when a signal was blocked by SQ filters

### Safe defaults (Sprint-2)

| Key                                          | Default |
|----------------------------------------------|---------|
| `signal_quality.enabled`                     | `true`  |
| `signal_quality.scoring_enabled`             | `true`  |
| `signal_quality.atr_pct_min / max`           | `0.5 / 8.0` |
| `signal_quality.adx_min`                     | `20.0`  |
| `signal_quality.max_spread_pct`              | `1.5`   |
| `signal_quality.min_open_interest`           | `500`   |
| `risk.consecutive_loss_breaker.enabled`      | `true`  |
| `risk.consecutive_loss_breaker.max_losses`   | `3`     |
| `risk.consecutive_loss_breaker.cooldown_minutes` | `30` |

### New API endpoint

| Method | Path                              | Purpose                                     |
|--------|-----------------------------------|---------------------------------------------|
| POST   | `/api/signal-quality/settings`    | Persist ATR / ADX / spread / OI thresholds  |

Extended: `POST /api/filters` now accepts `atr_filter_enabled`,
`adx_filter_enabled`, `spread_filter_enabled`, `consecutive_loss_breaker_enabled`.
Extended: `POST /api/risk/settings` accepts `consecutive_loss_max`,
`consecutive_loss_cooldown_min`.
`GET /api/risk/status` now includes a `circuit_breaker` block with live streak.

---

## API Reference

Selected endpoints (see `app.py`):

| Method | Path                       | Purpose                                    |
|--------|----------------------------|--------------------------------------------|
| GET    | `/api/scan`                | Latest scan results                        |
| POST   | `/api/order`               | Place order manually                       |
| GET    | `/api/positions`           | Open positions                             |
| POST   | `/api/positions/close-all` | Close all open positions                   |
| GET    | `/api/config`              | Full `config.yml` as JSON                  |
| POST   | `/api/config`              | Persist config                             |
| POST   | `/api/filters`             | Update Quick Filter toggles                |
| **GET**| **`/api/risk/status`**     | **[S1] Live risk snapshot**                |
| **POST**| **`/api/kill-switch`**    | **[S1] Toggle kill switch**                |
| **POST**| **`/api/risk/settings`**  | **[S1] Persist risk settings**             |

---

## Alpha Enhancements (Sprint-4)

Sprint-4 layers **market-context alpha filters** on top of the Sprint-1/2/3 risk
foundation. These are *edge finders*, not loss-stoppers — they upgrade signal
quality by using VIX regime, session time, volume profile, and options greeks.
All filters **fail-open** on missing data (no VIX quote / no greeks / no volume
history → signal passes through unchanged).

### Five alpha layers

1. **VIX-Regime Adaptive Risk** — Reads INDIA VIX and classifies market into
   `LOW` (<15), `NORMAL` (15–22), or `HIGH` (>22). The
   `position_sizing.risk_per_trade_pct` is multiplied by the regime factor:
   LOW × 1.10 (boost when calm), NORMAL × 1.00, HIGH × 0.60 (cut 40% during
   volatility spikes).

2. **Session Weighting** — Adds a score bonus/malus by intraday session:
   opening 30 min = −5 (chop), prime mid-session = +5 (trending), closing
   30 min = −10 (fade-only). Applied to the weighted score, potentially
   downgrading the letter grade.

3. **Volume-Profile POC** — Computes today's Point of Control (price level
   with highest volume-at-price from intraday bars). Rejects entries more
   than `max_poc_distance_pct` (default 1.5%) away from POC — poor mean-
   reversion odds.

4. **Greeks Filter** — Reads delta/theta from OpenAlgo `/quotes` payload.
   Rejects deep-OTM strikes (`|delta| < 0.20` — lottery tickets) and
   high-theta-burn contracts (`theta > 5% of LTP` — extreme decay on 0DTE).

5. **Strict Multi-Timeframe** *(opt-in, off by default)* — Hard-gates on
   agreement of ALL listed timeframes (e.g. `[5m, 15m]`). The tightest
   filter in the system; enable when you want maximum precision at the
   cost of frequency.

### Order of operations

```
Scanner produces candidate  →  compute atr/adx/spread  →  compute score/grade
   →  compute VIX regime, session, POC, greeks
   →  session bonus adjusts score/grade
   →  [S4] alpha reject   →  [S2] signal-quality reject
   →  [S1] risk-manager gates  →  [S3] position sizer  →  [S3] portfolio caps
   →  place order
```

### Dashboard integration

- **Risk-strip pills:** two new live pills — `Regime: NORMAL · VIX 16.3` and
  `Session: prime (+5)` — colour-coded (HIGH regime → red, LOW → yellow;
  opening/closing → yellow, off → red).
- **Sidebar toggles:** six new master/sub toggles — Alpha Enhancers (master),
  VIX Regime, Session Weighting, Volume Profile, Greeks Filter, Strict MTF.
- **Scan-row mini-badges:** each result shows a compact
  `HIGH · s:opening · POC 0.42% · Δ 0.35` line, turning red with the reject
  reason (e.g. `LOW_DELTA(0.15<0.20)`) when the row is filtered out.
- **Settings tab:** new "Alpha Enhancers" card with all 14 thresholds
  editable and pre-loaded from `config.yml`.

### Config keys

| Key                                                | Default          |
|----------------------------------------------------|------------------|
| `alpha_enhancers.enabled`                          | `true`           |
| `alpha_enhancers.vix_regime.enabled`               | `true`           |
| `alpha_enhancers.vix_regime.low_threshold`         | `15.0`           |
| `alpha_enhancers.vix_regime.high_threshold`        | `22.0`           |
| `alpha_enhancers.vix_regime.risk_multipliers.LOW`  | `1.10`           |
| `alpha_enhancers.vix_regime.risk_multipliers.HIGH` | `0.60`           |
| `alpha_enhancers.session_weighting.opening_minutes`| `30`             |
| `alpha_enhancers.session_weighting.bonuses.prime`  | `+5.0`           |
| `alpha_enhancers.session_weighting.bonuses.closing`| `-10.0`          |
| `alpha_enhancers.volume_profile.max_poc_distance_pct` | `1.5`         |
| `alpha_enhancers.greeks.min_abs_delta`             | `0.20`           |
| `alpha_enhancers.greeks.max_theta_pct`             | `5.0`            |
| `alpha_enhancers.strict_mtf.enabled`               | `false` (opt-in) |

### New API endpoint

| Method | Path                     | Purpose                                         |
|--------|--------------------------|-------------------------------------------------|
| POST   | `/api/alpha/settings`    | Persist all alpha-enhancer thresholds           |

Extended: `POST /api/filters` now accepts six new toggles
(`alpha_enhancers_enabled`, `vix_regime_enabled`, `session_weighting_enabled`,
`volume_profile_enabled`, `greeks_filter_enabled`, `strict_mtf_enabled`).
`GET /api/risk/status` now includes an `alpha_enhancers` block with live
regime, VIX, session bucket, and per-layer enabled flags.

---

## Troubleshooting

- **`market_close: 930` bug** — Legacy configs had no colon. Sprint-1 fixes to `'15:30'`.
- **All orders `AFTER_ENTRY_CUTOFF`** — Past 14:45 IST by design. Change
  `bot.entry_cutoff_time` or disable `bot.market_hours_check`.
- **`SPOT_DOWNTREND_BLOCKS_CE`** — Directional gate. Disable via Quick Filters.
- **Kill switch stuck ON** — Toggle button again or set `risk.kill_switch: false`.
- **`COOLDOWN(Xm_left)`** — Reduce `dedup.cooldown_minutes` or disable the toggle.

---

## Security

- **NEVER commit real API keys or Telegram bot tokens.** Rotate any exposed secret
  in `config.yml` immediately.
- Dashboard binds to `0.0.0.0:9000` — do not expose to the public internet without
  an auth reverse-proxy.
- `trade_db.sqlite` stores every trade — treat as sensitive.

---

---

## Production Hardening (Sprint-5)

Sprint-5 bolts on the **operational plumbing** required to run the bot as an
always-on service: rotating logs, resilient broker calls, secret hygiene, a
composite health endpoint, an admin reconcile endpoint, per-IP rate limiting,
a startup self-check, and a dashboard **System Health & Maintenance** card.

### New modules

| Module               | Responsibility                                                          |
|----------------------|-------------------------------------------------------------------------|
| `logging_setup.py`   | Idempotent `RotatingFileHandler` on logger `UTBotSRChannelsScanner`     |
| `broker_retry.py`    | `with_retry(...)` — exponential backoff + jitter for network/IO calls   |
| `secrets_loader.py`  | Loads `.env` / OS env, overlays over `config.yml`, tracks source        |
| `db_maintenance.py`  | `reconcile_stale_positions(cutoff_hours, dry_run)` + `db_health()`      |
| `rate_limiter.py`    | FastAPI middleware, per-IP token bucket, fail-open on internal error    |
| `health_check.py`    | Composite `build_health_report()` — broker/db/disk/logs/config          |

### New config keys (`config.yml → bot`)

| Key                              | Default   | Purpose                                     |
|----------------------------------|-----------|---------------------------------------------|
| `log_file`                       | `bot.log` | Rotating log target                         |
| `log_max_bytes`                  | `10485760`| 10 MB rollover threshold                    |
| `log_backup_count`               | `5`       | Number of rotated files to keep             |
| `retry.enabled`                  | `true`    | Master switch for broker retry              |
| `retry.max_attempts`             | `3`       | Total attempts including the first          |
| `retry.backoff_base_sec`         | `0.5`     | Base of `base * 2^n + jitter` sleep         |
| `rate_limit.enabled`             | `true`    | Master switch for per-IP limiter            |
| `rate_limit.per_minute`          | `120`     | Requests per IP per minute                  |
| `stale_position_cutoff_hours`    | `24`      | Rows older than this considered stale       |

### New endpoints

| Method | Path                     | Purpose                                                 |
|--------|--------------------------|---------------------------------------------------------|
| GET    | `/api/health`            | Composite health report (broker, db, disk, logs, cfg)   |
| GET    | `/api/admin/system`      | Uptime, versions, secret sources, feature flags         |
| POST   | `/api/admin/reconcile`   | Close stale OPEN rows — `{cutoff_hours, dry_run}`       |

### `.env` setup

Copy secrets **out of `config.yml`** to keep them out of git. Create a `.env`
file next to `app.py`:

```
OPENALGO_API_KEY=your_real_key_here
OPENALGO_HOST=http://127.0.0.1:5000
TELEGRAM_BOT_TOKEN=1234:AABBCC...
TELEGRAM_CHAT_ID=-1001234567890
```

At boot, `secrets_loader.apply_env_overrides()` merges these over the YAML
values. `/api/health.checks.config.secret_sources` reports which of `env` vs
`config` each secret came from — surface this on the **Settings → System
Health** card.

### Dashboard: System Health & Maintenance card

Located in **Settings** tab. Eight live stats (overall status pill, uptime,
broker latency, db + open positions, stale count, log size, disk free, secret
sources) plus three admin buttons (**Refresh Health**, **Preview Stale
Cleanup**, **Run Reconcile**) and a six-input settings form that PATCHes
`/api/config`.

### Failure semantics (fail-open by design)

- Bootstrap logging falls back to `basicConfig` if the rotating handler cannot
  be installed.
- Rate limiter allows the request on any internal error.
- `with_retry` catches only **network/IO** exceptions (`ConnectionError`,
  `Timeout`, `ChunkedEncodingError`, `OSError`), **never** programming errors
  like `ValueError`/`KeyError`.
- `/api/health` returns `degraded` (not `down`) on disk warnings, config
  warnings, or stale-position count > 0; only broker OR db unreachable
  triggers `down`.

---

## Observability & Resilience (Sprint-6)

Sprint-6 adds the **eyes and ears** for production operation: an in-process
metrics registry with a Prometheus scrape endpoint, a background broker
watchdog that detects OpenAlgo disconnects, an optional JSON log stream
for ingestion into Loki / ELK, and a Live Metrics dashboard card.

### New modules

| Module               | Responsibility                                                      |
|----------------------|---------------------------------------------------------------------|
| `metrics.py`         | Thread-safe counter / gauge / histogram-lite registry + Prometheus renderer (zero external deps) |
| `broker_watchdog.py` | Daemon thread: periodic LTP probe, threshold-triggered UP↔DOWN state, disconnect/recovery events |
| `log_json.py`        | Optional second rotating handler emitting one JSON object per record |

### New / modified config keys (`bot.*`)

| Key                          | Default            | Purpose                                            |
|------------------------------|--------------------|----------------------------------------------------|
| `watchdog.enabled`           | `true`             | Master switch for the broker watchdog thread       |
| `watchdog.interval_sec`      | `30`               | Seconds between probes (min 5)                     |
| `watchdog.failure_threshold` | `3`                | Consecutive failures required to flag DOWN         |
| `log_json`                   | `false`            | Turn on JSON log stream                            |
| `log_json_file`              | `logs/bot.jsonl`   | JSON log path (uses same rotation size + backups)  |

### New endpoints

| Method / Path             | Purpose                                                                 |
|---------------------------|-------------------------------------------------------------------------|
| `GET /api/metrics`         | Prometheus text-exposition of all counters/gauges/histograms (rate-limit exempt) |
| `GET /api/metrics/snapshot`| JSON snapshot of the metrics registry + current watchdog state          |

`/api/health` now includes a `checks.watchdog` block:

```json
"watchdog": {
  "state": "up",                 // "up" | "down" | "unknown"
  "consecutive_failures": 0,
  "last_check_ts": 1755765432.1,
  "last_change_ts": 1755761100.0,
  "last_error": "",
  "last_latency_ms": 42
}
```

### Metric catalogue

| Metric                             | Type      | Labels          | Meaning                                   |
|------------------------------------|-----------|-----------------|-------------------------------------------|
| `bot_uptime_seconds`               | gauge     | —               | Seconds since metrics module first loaded |
| `orders_total`                     | counter   | action, outcome | Broker order attempts by BUY/SELL and ok/fail |
| `signals_total`                    | counter   | side, outcome   | Signals generated, accepted vs rejected   |
| `signals_rejected_by_reason_total` | counter   | reason          | Rejection breakdown by reason code        |
| `retry_attempts`                   | summary   | op              | Attempts consumed per retryable op (sum+count) |
| `retry_exhausted_total`            | counter   | op              | Ops that hit `max_attempts` and re-raised |
| `ratelimit_blocks_total`           | counter   | —               | Requests denied with HTTP 429             |
| `broker_up`                        | gauge     | —               | 1 if watchdog last-check succeeded, else 0 |
| `broker_watchdog_events_total`     | counter   | kind            | `kind=disconnect` \| `kind=recovery`        |

### Example Prometheus queries

```promql
# Order failure rate over the last 5 minutes
sum(rate(orders_total{outcome="fail"}[5m])) / sum(rate(orders_total[5m]))

# Average retry attempts per broker call (last 5 min)
rate(retry_attempts_sum[5m]) / rate(retry_attempts_count[5m])

# Was the broker down at any point in the last hour?
min_over_time(broker_up[1h]) == 0

# Disconnect events per hour
increase(broker_watchdog_events_total{kind="disconnect"}[1h])
```

### Failure semantics (Sprint-6 additions)

- **Watchdog is fail-open**: any internal error inside the probe loop is
  logged at DEBUG and treated as "no state change" — a bug in the watchdog
  can never crash the app or wrongly report DOWN.
- **Metrics are best-effort**: every `inc()` / `set_gauge()` / `observe()`
  call is wrapped so a metrics failure never propagates into trade paths.
- **Type conflicts are silently ignored**: if two call sites disagree on a
  metric's type, the first-wins and a WARNING is logged.
- **JSON logs are additive**: enabling `log_json` does not disable the plain
  text log — both handlers run in parallel and rotate independently.

### Live Metrics dashboard card

Settings tab → **Live Metrics & Watchdog** shows:
watchdog state (color-coded green/red), last broker probe latency,
consecutive-failure streak, orders ok/fail, signals accepted, avg retry
attempts, exhausted retries, and 429 counter. Piggy-backs on the existing
System Health refresh so both cards update together.

---

*Last updated: Sprint-6 (Observability & Resilience) complete. Version 1.6.0.*
