# UTBot + SR Channels Scanner — Implementation Walkthrough

## Files Created

All files are in [`c:\Rahul\Trade\Strategies\Bot-UTBot-SR Channels\`](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/)

| File | Purpose |
|------|---------|
| [config.yml](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/config.yml) | All configurable parameters |
| [nse_indices.py](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/nse_indices.py) | NSE segment constituent fetcher (25 indices) |
| [signals.py](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/signals.py) | UTBot + SR Channels signal engines |
| [telegram.py](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/telegram.py) | Telegram alerting (direct or via OpenAlgo) |
| [scanner.py](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/scanner.py) | Main orchestrator + CLI entry point |
| [app.py](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/app.py) | FastAPI backend web server for the dashboard |
| [frontend/index.html](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/frontend/index.html) | Dashboard UI structure |
| [frontend/index.css](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/frontend/index.css) | Custom styling for premium dark-theme dashboard |
| [frontend/index.js](file:///c:/Rahul/Trade/Strategies/Bot-UTBot-SR%20Channels/frontend/index.js) | Frontend interactive logic & TV Charting |

---

## Signal Modes

Controlled by `signal_mode` in `config.yml`:

| Mode | Behaviour |
|------|-----------|
| `UTBot` | UTBot buy/sell across last N closed candles only |
| `SR` | SR Channel proximity/inside check on last candle only |
| `UTBot+SR` | Both conditions must trigger simultaneously (**default**) |

---

## Smoke Test Results (RELIANCE.NS, 15m, 60 days)

```
Data rows  : 1409
UT Trail   : 1285.91
SR Zones   : 3 zones found
BUY signal : False   triggered = []
SELL signal: True    triggered = ['UT Bot', 'S/R Resistance']
Smoke test PASSED ✅
```

Both engines computed correctly and the composite evaluator returned results consistent with the Pine Script logic.

---

## Local Web Dashboard

An interactive browser-based dashboard is provided to monitor signals, edit configurations, view system logs, and analyze dynamic charts.

### Launching the Dashboard
Install backend web dependencies:
```bash
pip install fastapi uvicorn
```

Start the local web server:
```bash
python app.py
```

Open your browser and navigate to:
```
http://127.0.0.1:8000
```

### Dashboard Features
- **Metrics Grid**: Displays total stocks scanned, live count of BUY/SELL signals, and the timestamp of the last scan cycle.
- **Searchable Signal Tables**: Clean filters to search through active BUY/SELL signals quickly.
- **Interactive Charting**: Select any scanned ticker to view an interactive candle chart with S/R support/resistance zones (horizontal dotted bands), UT Trail overlays, and Buy/Sell markers powered by TradingView's Lightweight Charts library.
- **Settings Editor**: Dynamically modify indicators coefficients (`key_value`, `atr_period`, S/R settings, alert parameters) and save directly to `config.yml`.
- **System Logs Console**: View python server logs directly in the UI.

---

## Usage (CLI Mode)

```bash
# Single scan with default config
python scanner.py --once

# Scan BankNifty on 5-minute bars, UTBot-only mode
python scanner.py --once --segment BANKNIFTY --tf 5m --mode UTBot

# Continuous scan (every 300s by default)
python scanner.py --segment NIFTY50 --tf 15m

# SR Channels only on daily chart
python scanner.py --once --tf 1d --mode SR

# List all 25 supported segments
python scanner.py --list-segments
```

---

## Key Design Points

- **Pine Script defaults faithfully replicated**: `key_value=1.0`, `atr_period=2` (not the reference bot's 2/1).
- **Lookback default = 2** (current + 1 prior candle), per the requirement.
- **SR zone logic** matches Pine Script exactly: price inside zone → both buy/sell; price near support top → buy; price near resistance bottom → sell.
- **Telegram** includes SR zone price ranges in the message for quick reference.
- **Data source is pluggable**: yfinance (default), tvdatafeed, twelvedata, openalgo — switchable via `config.yml` without code changes.
- **Thread-safe parallel scan**: 10 concurrent threads via `ThreadPoolExecutor`.
- **Multiple Segments & Custom Stock List Scanning**:
  - `segment` in `config.yml` can now be a list of segments (e.g. `segment: ["NIFTY50", "BANKNIFTY"]`) to scan multiple segments at once.
  - Setting `use_symbols: true` in `config.yml` will combine your custom `symbols` list (at the bottom of the config) with the selected segment(s) to scan them all together.
  - If `segment` is empty (`""` or `[]`), the scanner runs exclusively on the custom `symbols` list.
- **Daily symbol cache**: Segments symbol lists are cached to `segment_cache.json` for the day to minimize redundant index calls.

---

## Phase 2 Upgrades (Scoring & UI)

### 1. Engine Upgrades (`signals.py`)
- **Volatility Squeeze (TTM Squeeze):** Added `compute_squeeze()` to calculate Bollinger Bands and Keltner Channels. The engine awards `+15.0 pts` if a signal fires exactly as a volatility squeeze releases. You can toggle `squeeze_filter_enabled` in `config.yml` to filter out signals that are not part of a squeeze.
- **Volume Profile (VPVR) Hybrid Zones:** Added `compute_vpvr_poc()` to calculate the Volume Point of Control. If an S/R Zone aligns perfectly with the Volume Point of Control, its strength score is doubled, creating high-probability Hybrid Zones.
- **RSI Divergence Detection:** Added `detect_rsi_divergence()` which scans the last 15 candles. If price makes a Lower Low but RSI makes a Higher Low at a support zone, it flags as Bullish Divergence (+15.0 pts).
- **Dynamic Volatility Targets (ATR Trailing):** Updated `calculate_risk_reward()` so S/R targets are dynamically bounded by current market volatility (ATR). If the next S/R zone is too close or far, the target is clamped between `1.0 * ATR` and `5.0 * ATR`.

### 2. Mini-Backtester (`scanner.py`)
- Added `calculate_historical_win_rate()` which runs a fast proxy backtest (1.5 R:R) on the last 30 days of data for the specific symbol that triggered.
- Calculates how often the UT Bot signal hit its target before stop-loss. This is exposed in the UI as the **Win Rate** column.

### 3. Dashboard UI Enhancements
- **Visual Confidence Gauge:** The raw score (e.g., 72.5) has been upgraded into a tier badge: **`A+`** (Premium), **`A`** (High), **`B`** (Medium), and **`C`** (Low).
- **Confluence Matrix:** The text "Confluence" column has been replaced with a row of 5 indicator icons: `[Trend] [Mom] [Vol] [S/R] [Sqz]`. These light up Green for Buy signals, Red for Sell signals, and remain Grey if inactive.
