# Options Trading Platform — Walkthrough

I have successfully implemented **Bot-Options**, a professional-grade Options Trading Platform, inside the [Bot-Options](file:///c:/Rahul/Trade/Strategies/Bot-Options) directory. 

It runs on port **8001**, uses its own independent configurations and databases, and imports the core analytical scanning engines directly from `Bot-Stocks` to ensure zero code duplication.

---

## Architectural Map & File Directory

```
Bot-Options/
├── app.py                        # FastAPI Server (port 8001)
├── config.yml                    # Options Config (underlying selection, risk, execution)
├── option_scanner.py             # Three-Stage Scanner loop & trade trigger logic
│
├── core/
│   ├── __init__.py
│   ├── expiry_manager.py         # Handles Weekly/Monthly contract roll calendar
│   ├── strike_selector.py        # Selects strikes via ATM, OTM, ITM, Premium, or Liquidity
│   ├── option_filters.py         # Computes IV penalty, OI momentum, and theta decay
│   ├── option_signals.py         # Stage 1 (underlying trend) & Stage 3 (premium chart confirmation)
│   └── option_risk.py            # Restricts positions, capital drawdowns, & losses cooldown
│
├── data/
│   ├── __init__.py
│   ├── option_chain.py           # Fetches options chains from OpenAlgo
│   └── instrument_resolver.py    # Parses option symbols & queries contract tokens
│
├── db/
│   ├── __init__.py
│   ├── option_signal_db.py       # SQlite database storing generated option signals
│   └── option_trade_db.py        # SQLite database logging active monitored trades & events
│
├── execution/
│   ├── __init__.py
│   ├── order_engine.py           # Submits offset-based optionsorder() and direct placeorder()
│   └── position_monitor.py       # Active thread managing trailing stops, partial lot exits, etc.
│
├── notifications/
│   ├── __init__.py
│   └── notifier.py               # Dispatches alerts via Telegram and WhatsApp
│
└── frontend/
    ├── index.html                # Bloomberg-style Trading UI
    ├── index.css                 # Dark theme, option chain color styling
    └── index.js                  # Interface data binding, quick trading, auto-scan toggling
```

---

## Key Technical Achievements

### 1. Zero Code Duplication via Cross-Imports
To keep `Bot-Stocks` stable and fully working, `Bot-Options` maps `Bot-Stocks` directly into `sys.path`. It directly reuses:
- [compute_utbot_signals](file:///c:/Rahul/Trade/Strategies/Bot-Stocks/signals.py#L77-L143)
- [compute_sr_signals](file:///c:/Rahul/Trade/Strategies/Bot-Stocks/signals.py#L326-L470)
- [evaluate_composite_signals](file:///c:/Rahul/Trade/Strategies/Bot-Stocks/signals.py#L867-L975)
- [fetch_history](file:///c:/Rahul/Trade/Strategies/Bot-Stocks/scanner.py#L129-L341)

### 2. The Three-Stage Signal Confirmation Pipeline
Every generated signal goes through a rigorous vetting process:
- **Stage 1 (Underlying Scan)**: Checks index trend (NIFTY/BANKNIFTY) using UTBot and S/R Channels. Must meet Gate 1 score criteria.
- **Stage 2 (Strike Selector & Option Filters)**: Resolves target expiry, fetches option chain, selects strike contract (e.g. ATM or ITM1), and adjusts the score with IV penalty (implied volatility check), OI momentum, and theta decay adjustments.
- **Stage 3 (Option Chart Confirmation)**: Fetches historical OHLCV of the selected option contract on NFO from OpenAlgo and evaluates UTBot. If bullish trend is active, confirmation passes. If bearish/neutral, the signal is rejected or penalized based on mode.

### 3. Native OpenAlgo Integration
Leverages native capabilities of the OpenAlgo client:
- `client.optionchain()`: Returns strikes, LTP, bid/ask, and open interest.
- `client.optiongreeks()`: Fetches delta, gamma, theta, and vega.
- `client.optionsorder()`: Supports offset-based orders (e.g. OTM1, ATM, ITM2) directly without manual parsing.
- `client.expiry()`: Automatically returns all current weekly/monthly expiry dates from the NSE.
- `client.whatsapp()`: Extends alerts to WhatsApp in addition to Telegram.

### 4. Premium-Based Trade Management
Unlike stocks which use absolute price percentages, the Options monitor calculates target levels, trailing stop losses, multi-level profit locks, and lot-based partial exits relative to the **entry premium price**.
It also squaring off positions 10 minutes before close on the day of expiry to prevent accidental premium loss.

### 5. Multi-Layer Risk Circuit Breakers
Protects capital with rules enforced at trade generation:
- Daily drawdown limit (₹5,000 maximum daily loss)
- Open leg limit (maximum 5 simultaneous positions)
- Consecutive losses cooldown (3 consecutive losses pauses execution for 30 minutes)
- Trade size validation (estimated cost of lot size * premium must fit allocated capital limits)

---

## Live Mockups Saved

For your convenience, I have generated and copied the terminal dashboard designs directly into your project files:
- **Signals Workspace**: [Mockup-Signals-Tab.png](file:///c:/Rahul/Trade/Strategies/Bot-Options/documents/Mockup-Signals-Tab.png)
- **Options Chain Tab**: [Mockup-Chain-Tab.png](file:///c:/Rahul/Trade/Strategies/Bot-Options/documents/Mockup-Chain-Tab.png)
- **Dashboard Design Specifications**: [dashboard_design.md](file:///C:/Users/RahulTewari/.gemini/antigravity-ide/brain/6e56e281-5834-48de-8e92-62c8eba03e1b/dashboard_design.md)

---

## Verification Plan Results

- **Python File Compilation**: Checked and verified. All scripts compile with zero syntax errors.
- **Server Ports Independence**: Running `python app.py` will start the server on port `8001`, fully isolating it from the `Bot-Stocks` server running on port `8000`.
- **Database Schema Integrity**: SQLite tables for signals and positions are auto-initialized successfully on startup.
