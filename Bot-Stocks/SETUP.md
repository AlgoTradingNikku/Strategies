# Setup Guide for Bot-Stocks

This guide will help you set up Bot-Stocks from scratch.

## Prerequisites

- Python 3.8 or higher
- OpenAlgo server running (for broker integration)
- Telegram Bot Token (optional, for notifications)

---

## Step 1: Install Python Dependencies

```bash
cd Bot-Stocks
pip install -r requirements.txt
```

---

## Step 2: Create Your Configuration File

Copy the example config and customize it:

```bash
cp config.example.yml config.yml
```

**Important:** `config.yml` is gitignored and will NOT be committed. This keeps your secrets safe.

---

## Step 3: Configure Telegram (Optional)

### Get Bot Token
1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow instructions
3. Copy the bot token (format: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Get Your Chat ID
1. Search for [@userinfobot](https://t.me/userinfobot)
2. Send `/start`
3. Copy your numeric ID

### Update config.yml
```yaml
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN_HERE"
  chat_id: "YOUR_CHAT_ID_HERE"
```

---

## Step 4: Configure OpenAlgo Broker

### Get API Credentials
1. Open OpenAlgo dashboard: `http://127.0.0.1:5000`
2. Go to Settings → API Keys
3. Generate a new API key
4. Copy your API key and username

### Update config.yml
```yaml
openalgo:
  apikey: "YOUR_API_KEY_HERE"
  username: "your_username"
  base_url: "http://127.0.0.1:5000"
  ws_url: "ws://127.0.0.1:8765"
  order_mode: "manual"  # Start with manual mode for safety
  allowed_actions: "BUY_ONLY"
  order_product: "MIS"
  order_quantity: 1
```

---

## Step 5: Configure Trading Symbols

Choose stocks to scan:

### Option A: Use Index Segments
```yaml
segment:
  - BANKNIFTY    # Bank Nifty stocks
  - NIFTY50      # Nifty 50 stocks
use_symbols: false
```

### Option B: Use Custom Watchlist
```yaml
segment: []
use_symbols: true
symbols:
  - RELIANCE
  - TCS
  - INFY
  - HDFCBANK
```

---

## Step 6: Choose Signal Engines

Enable the engines you want:

### Trend Following Strategy
```yaml
strategy:
  ut_enabled: true        # UT Bot signals

momentum_engine:
  enabled: true           # Momentum breakouts
  min_momentum_score: 50
```

### Range Trading Strategy
```yaml
sr_channels:
  enabled: true           # S/R bounces

mean_reversion_engine:
  enabled: true           # Oversold/overbought
  min_mean_reversion_score: 50
```

### Hybrid (All Engines)
Enable all 4 engines for maximum coverage.

---

## Step 7: Configure Risk Management

```yaml
risk_limits:
  enabled: true
  max_positions: 3              # Max concurrent positions
  max_exposure_pct: 100.0       # Max capital exposure

trade_management:
  partial_exit:
    enabled: true
    tiers:
      - trigger_pct: 1.0        # At 1% profit
        exit_qty_fraction: 0.33 # Exit 33%
        move_sl_to_be: true     # Move SL to breakeven
  
  trailing_sl:
    enabled: true
    activation_pct: 1.0
    tiers:
      - min_gain_pct: 1.0
        distance_pct: 0.8       # Trail 0.8% behind peak
```

---

## Step 8: Start the Bot

```bash
python app.py
```

You should see:
```
INFO: Started scanner thread
INFO: Uvicorn running on http://0.0.0.0:8080
```

---

## Step 9: Access the Dashboard

Open your browser to:
```
http://localhost:8080
```

### Dashboard Sections
- **Dashboard** - Real-time signals with scores
- **Active Positions** - Track open trades
- **Signal History** - Review past signals
- **Performance Stats** - Win rate, profit factor, etc.
- **Settings** - Edit config live (no restart needed)
- **System Logs** - Real-time log stream

---

## Step 10: Test Your Setup

### 1. Check Signals
- Wait for market hours (9:15 AM - 3:30 PM IST)
- Signals should appear in the dashboard
- Check Telegram for notifications

### 2. Test Manual Order (Optional)
- Click "Place Order" on a signal card
- Verify order appears in OpenAlgo

### 3. Enable Auto-Trading (When Ready)
Edit config.yml:
```yaml
openalgo:
  order_mode: "auto"  # Bot will place orders automatically
```

⚠️ **Warning:** Auto mode places real orders! Start with small quantities.

---

## Common Issues

### Bot won't start
- Check Python version: `python --version` (need 3.8+)
- Install dependencies: `pip install -r requirements.txt`
- Check `scanner.log` for errors

### No signals appearing
- Wait for market hours (9:15 AM - 3:30 PM IST)
- Lower `min_alert_score` threshold
- Enable more engines
- Check if symbols are actively trading

### Telegram not working
- Verify bot token format: `1234567890:ABCdef...`
- Verify chat ID is numeric
- Test manually:
  ```bash
  curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
    -d "chat_id=<CHAT_ID>&text=Test"
  ```

### OpenAlgo connection failed
- Verify OpenAlgo is running: `http://127.0.0.1:5000`
- Check API key in OpenAlgo dashboard
- Verify broker login status

---

## Next Steps

1. **Paper Trade First** - Test with `order_mode: "manual"` for 1-2 weeks
2. **Review History** - Check signal quality in Performance Stats
3. **Tune Parameters** - Adjust engine weights and filters based on results
4. **Enable Auto Trading** - Switch to `order_mode: "auto"` when confident

---

## Security Reminders

✅ **DO:**
- Keep `config.yml` private (it's gitignored)
- Use `.env` files for secrets (optional)
- Regularly rotate API keys

❌ **DON'T:**
- Commit `config.yml` to Git
- Share your API keys or bot tokens
- Run untested configs in auto mode

---

**You're all set! Happy Trading! 📊✨**
