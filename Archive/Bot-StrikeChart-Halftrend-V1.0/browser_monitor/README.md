# TradingView Browser Monitor 📊

Watches your TradingView chart's **Strategy Tester** panel for new HalfTrend
Buy/Sell signals using browser automation (Playwright).

**No TradingView paid plan required!**

## How It Works

```
┌───────────────────┐      ┌──────────────────┐      ┌──────────────┐
│  TradingView      │      │  Browser Monitor │      │  OpenAlgo    │
│  Chart with       │─────▶│  (Playwright)    │─────▶│  Broker API  │
│  HalfTrend        │      │                  │      │              │
│  STRATEGY         │      │  Reads "List of  │      │  Places      │
│                   │      │  Trades" table   │      │  orders      │
└───────────────────┘      └──────────────────┘      └──────────────┘
```

## Setup

### Step 1: Install dependencies

```bash
# From the Bot-StrikeChart-Halftrend folder, with venv active:
pip install -r requirements.txt
playwright install chromium
```

### Step 2: Add the HalfTrend Strategy to TradingView

1. Open TradingView → Pine Script Editor (bottom of chart)
2. Paste the code from `HalfTrend_Strategy.pine`
3. Click **"Add to chart"**
4. You should see:
   - The HalfTrend line and arrows on the chart (same as before)
   - A new **"Strategy Tester"** panel at the bottom
5. Copy the chart URL from the browser address bar

### Step 3: Configure

Edit `browser_monitor/config.yaml`:

```yaml
tradingview:
  chart_url: "https://www.tradingview.com/chart/PASTE_YOUR_ID/"
```

### Step 4: Run

```bash
python browser_monitor/monitor.py
```

## First Run

1. Browser window opens → TradingView loads
2. **Log in** if prompted (session is saved for next time)
3. Strategy Tester opens automatically
4. Monitor starts watching for **new** trades
5. When HalfTrend makes a new entry → signal is printed

## Example Output

```
18:30:01 │ INFO    │ 🟢 MONITORING STARTED — watching for new trades
18:30:01 │ INFO    │    Press Ctrl+C to stop
18:35:12 │ INFO    │
18:35:12 │ INFO    │ 🚨══════════════════════════════════════════════════
18:35:12 │ INFO    │ 🚨  NEW SIGNAL: BUY
18:35:12 │ INFO    │ 🚨  Type      : Long Entry
18:35:12 │ INFO    │ 🚨  Signal    : Long
18:35:12 │ INFO    │ 🚨  Date      : 2026-02-20 15:35
18:35:12 │ INFO    │ 🚨  Price     : 22450.50
18:35:12 │ INFO    │ 🚨  Detected  : 2026-02-20T18:35:12
18:35:12 │ INFO    │ 🚨══════════════════════════════════════════════════
```

## Enabling Order Placement (Phase 2)

Edit `config.yaml`:

```yaml
openalgo:
  enabled: true
  host: "http://127.0.0.1:5000"
  api_key: "your_openalgo_api_key_here"
```

## Files

```
browser_monitor/
├── monitor.py                 # Main monitoring script
├── config.yaml                # Configuration
├── HalfTrend_Strategy.pine    # Pine Script (paste into TradingView)
├── README.md                  # This file
├── signals.jsonl              # Signal log (auto-created)
├── browser_monitor.log        # App log (auto-created)
└── browser_data/              # Browser session (auto-created)
```

## Command Line Options

```bash
python browser_monitor/monitor.py                # Normal mode
python browser_monitor/monitor.py --headless      # No browser window
python browser_monitor/monitor.py --debug         # Save DOM + screenshot
```
