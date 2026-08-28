# Bot-Stocks - Trading Signal Scanner

Multi-strategy trading bot with dynamic engine registry, advanced filtering, and automated order execution.

## Features

### Signal Engines (Modular & Expandable)
- ✅ **UT Bot** - Trend-following signals using ATR-based trailing stops
- ✅ **S/R Channels** - Support/Resistance breakout detection
- ✅ **Momentum Engine** - Multi-factor trend continuation (RSI, Volume, ADX, EMA, BB, ROC)
- ✅ **Mean Reversion Engine** - Oversold/overbought bounce detection (BB, RSI Divergence, Stochastic, Z-Score)

### Smart Filters
- 🔍 **HTF Confirmation** - Multi-timeframe trend alignment
- 📊 **Relative Strength** - Outperformer vs NIFTY50/indices
- ⚖️ **Risk/Reward Calculator** - ATR-based SL/TP with R:R ratio
- 🕯️ **Candle Patterns** - Engulfing, Hammer, Doji detection
- 📈 **Signal History** - Outcome tracking (win/loss/pending)

### Trading Features
- 🤖 **Auto Order Execution** - OpenAlgo integration (Live/Paper/Manual modes)
- 📱 **Telegram Alerts** - Real-time signal notifications
- 📊 **Live Dashboard** - Web UI with auto-refresh
- 💼 **Position Management** - Active trades tracking with P&L

---

## Quick Start

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Configuration
```bash
# Copy example config
cp config.example.yml config.yml

# Edit your API keys
notepad config.yml
```

**Required Settings:**
- `telegram.bot_token` - Your Telegram bot token
- `telegram.chat_id` - Your Telegram chat ID
- `openalgo.api_key` - OpenAlgo API key (for auto-trading)

### 3. Run
```bash
# Start dashboard (opens browser at http://localhost:5000)
python app.py

# Or run scanner once from command line
python scanner.py --once
```

---

## Configuration Guide

### Enable/Disable Signal Engines

**Via Dashboard:**
- Open `http://localhost:5000` → Dashboard sidebar → Toggle engines ON/OFF

**Via config.yml:**
```yaml
strategy:
  ut_enabled: true          # UT Bot signals

sr_channels:
  enabled: true             # S/R Channel signals

momentum:
  enabled: false            # Momentum signals
  rsi_enabled: true         # Toggle individual components
  volume_enabled: true
  adx_enabled: true
  # ... etc

mean_reversion:
  enabled: false            # Mean Reversion signals
```

### Smart Filters

```yaml
filters:
  mtf_filter_enabled: true          # Multi-timeframe confirmation
  mtf_timeframe: "15m"              # Higher timeframe
  
  rs_enabled: true                  # Relative strength filter
  rs_index: "NIFTY50"               # Compare against index
  rs_buy_threshold: 1.05            # Must outperform by 5%
  
  risk_reward_enabled: true         # R:R calculator
  rr_default_ratio: 2.0             # Target = 2× stop loss
  
  candle_patterns_enabled: true     # Candle pattern detection
  signal_history_enabled: true      # Track signal outcomes
```

### Auto Order Execution

```yaml
openalgo:
  order_mode: "manual"      # Options: "live" | "paper" | "manual"
  allowed_actions: "BUY_ONLY"  # "BUY_ONLY" | "SELL_ONLY" | "BOTH"
  order_product: "MIS"      # MIS (intraday) or CNC (delivery)
  order_quantity: 1
```

**Modes:**
- `live` - Executes real orders via OpenAlgo
- `paper` - Logs orders without execution (testing)
- `manual` - Only sends Telegram alerts (default)
