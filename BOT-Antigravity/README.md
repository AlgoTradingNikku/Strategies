# OpenAlgo Trading Bot v2 — with Web Dashboard

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Make sure OpenAlgo server is running at http://127.0.0.1:5000

# 3. Run the bot + dashboard together
python trading_bot.py

# 4. Open your browser
#    http://127.0.0.1:8080
```

## Dashboard Features

| Section         | What it shows |
|-----------------|---------------|
| Metrics bar     | Cash, P&L, open trades, win rate, signals today |
| AI Watchlist    | Live RSI + MACD values and BUY/SELL/HOLD signal per symbol |
| P&L Chart       | Live rolling session P&L updated every scan |
| Closed Trades   | Full history of exits with entry/exit price and P&L |
| Open Positions  | Real-time unrealised P&L per position with SL/TP levels |
| Bot Controls    | Configure symbols, strategy, capital, SL%, TP% and start/stop |
| Activity Log    | Real-time event stream from the bot engine |

## Architecture

```
trading_bot.py
├── OpenAlgoClient     REST calls to OpenAlgo at :5000
├── SignalEngine       RSI + MACD indicator logic
├── PositionManager    Tracks open trades + SL/TP exits
├── TradingBot         Main scan loop (every 5 min via schedule)
├── Flask app          REST API for dashboard at :8080
└── SocketIO           Pushes live updates to browser
```

The bot loop runs in a **background thread** — the Flask/SocketIO server
stays responsive on the main thread. All state is shared via `BotState`.

## REST API Endpoints

| Method | Path                | Description              |
|--------|---------------------|--------------------------|
| GET    | /                   | Dashboard UI             |
| GET    | /api/summary        | Bot stats snapshot       |
| GET    | /api/positions      | Open positions           |
| GET    | /api/watchlist      | Current signals          |
| GET    | /api/log            | Activity log (last 100)  |
| GET    | /api/closed_trades  | Closed trade history     |
| POST   | /api/bot/start      | Start bot (JSON config)  |
| POST   | /api/bot/stop       | Stop bot                 |
| POST   | /api/bot/squareoff  | Square off all positions |

## ⚠ Disclaimer

Always test in OpenAlgo **Analyzer mode** before live trading.
Toggle: `POST http://127.0.0.1:5000/api/v1/analyzertoggle`
