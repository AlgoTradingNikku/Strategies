# Bot-Stocks - Multi-Engine Trading Scanner

Professional trading scanner for NSE with 4 signal engines, advanced filters, and automated order placement.

## Features

- **4 Signal Engines**: UT Bot, S/R Channels, Momentum, Mean Reversion
- **Smart Filters**: MTF confirmation, candlestick patterns, R:R ratio, signal history
- **Position Management**: Partial exits, trailing SL, profit lock
- **Broker Integration**: OpenAlgo (Flattrade, Shoonya, Dhan, etc.)
- **Modern Dashboard**: Real-time signals, positions, history, stats
- **Telegram Alerts**: Instant notifications

## Quick Start

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp config.example.yml config.yml
# Edit config.yml with your credentials
```

### 3. Run
```bash
python app.py
# Open http://localhost:8080
```

## Configuration

**Telegram Setup:**
```yaml
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN"  # Get from @BotFather
  chat_id: "YOUR_CHAT_ID"      # Get from @userinfobot
```

**OpenAlgo Setup:**
```yaml
openalgo:
  apikey: "YOUR_API_KEY"
  username: "your_username"
  base_url: "http://127.0.0.1:5000"
  order_mode: "manual"  # or "auto"
```

**Engine Selection:**
```yaml
# Trend Following
strategy:
  ut_enabled: true
momentum_engine:
  enabled: true

# Range Trading
sr_channels:
  enabled: true
mean_reversion_engine:
  enabled: true
```

## Security

**NEVER commit these files:**
- `config.yml` (contains secrets)
- `config.local.yml` (local copy)
- `*.db` (databases)
- `*.log` (logs)

These are already in `.gitignore`.

## Project Structure

```
Bot-Stocks/
├── app.py              # FastAPI server
├── scanner.py          # Scanning logic
├── signals.py          # Signal engines
├── momentum_engine.py  # Momentum scoring
├── regime.py           # Market regime
├── trade_manager.py    # Position tracking
├── trading_adapter.py  # Order placement
├── telegram.py         # Notifications
├── frontend/           # Web dashboard
│   ├── index.html
│   ├── index.js
│   └── index.css
└── config.example.yml  # Config template
```

## Troubleshooting

**No signals?**
- Check if engines are enabled
- Lower `min_alert_score` in config
- Verify market hours (9:15 AM - 3:30 PM IST)

**Orders not placing?**
- Set `order_mode: "auto"` in config
- Verify OpenAlgo is running
- Check API credentials

**Telegram not working?**
- Verify bot token and chat ID
- Test: `/start` to @userinfobot for chat ID

## Disclaimer

⚠️ **For educational purposes only.** Trading involves risk. Not responsible for losses. Test in paper trading first.

## License

MIT License

---
**Happy Trading! 📊**
