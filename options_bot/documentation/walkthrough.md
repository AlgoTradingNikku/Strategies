# OpenAlgo Options Bot - Project Summary & User Guide

**Version:** 1.0.0 (Foundation Complete)
**Status:** Verified & Ready for Simulation

## 🚀 Project Overview
We have successfully built a professional-grade **Automated Options Trading Bot** tailored for NIFTY/BANKNIFTY. The bot features a modular architecture, sophisticated risk management, and a flexible strategy engine.

## ✨ Key Features Implemented

### 1. 🧠 Intelligent Strategy Engine
- **4-Filter Logic:** Ensures high-probability entries by aligning:
    - **HTF Trend (15m):** EMA Crossover / UT Bot.
    - **LTF Alignment (5m):** EMA / UT Bot.
    - **Momentum:** StochRSI / UT Bot Signal Flip.
    - **Strength:** RSI Thresholds (>55 Buy, <45 Sell).
- **Dual Entry Modes:** Supports Initial Breakout Entry and Pullback Re-Entry.
- **Substitution:** Ability to swap StochRSI for **UT Bot (QuantNomad)** via config.

### 2. 🛡️ "Highest Wins" Risk Management
- **Tri-Level Protection:** The bot constantly calculates the safest exit price using the MAX of:
    - **Line A:** Fixed Hard Stop Loss (e.g., 30%).
    - **Line B:** Trailing Stop Loss (e.g., Peak - 5%).
    - **Line C:** Profit Lock (Breakeven after 3% profit).
- **Circuit Breakers:** Daily Loss Limit (auto-stop) and Gap Timers (cool-down between trades).

### 3. ⚡ Dynamic Execution
- **Strike Selection:** 
    - **ATM_OFFSET:** Choose strikes by distance (0=ATM, +1=OTM).
    - **PREMIUM:** Target a specific premium (e.g., ₹100), bot finds closest match.
- **Smart Time Units:** Input times as `30s`, `5m`, `2h` in config.
- **Live/Paper Switch:** Simple `live_trading: true/false` flag handles everything.

### 4. 🎮 Runtime Control Interface
- **Command Line:** Inputs are percentage-based by default.
- **Commands:** `sl 30` (30%), `target 50` (50%), `trailing 10` (10%).

---

## 📂 Project Structure

| File | Purpose |
| :--- | :--- |
| `main.py` | **Entry Point.** Auto-switches between Live (Real Data) and Paper (Mock). |
| `config.json` | **The Brain.** Control Risk, Expiry, Strikes, and Mode here. |
| `order_manager.py` | **Execution.** Handles strike selection logic (Premium/Offset). |
| `risk_manager.py` | **Bodyguard.** Daily Acceptable Loss & Highest Wins TSL. |

---

## 🕹️ Quick Start Guide

### 1. Run the Bot
```bash
python main.py
```
- **Paper Mode:** Simulates trades with mock data.
- **Live Mode:** Connects to API and monitors market for real setup.

### 2. Runtime Commands
Type these in the terminal while the bot is running:
- `sl 40` : Change Stop Loss to 40%.
- `target 60` : Change Target Profit to 60%.
- `trailing 10` : Change Trailing Stop to 10%.
- `pause` / `resume` : Control new entries.
- `positions` : Show active trades.

### 3. Configuration Highlights
- **Daily Limit:** Set `max_daily_acceptable_loss` to your risk cap (e.g., 1000). Set to `0` to disable.
- **Strike Mode:** Switch between `ATM_OFFSET` and `PREMIUM`.
- **Live Mode:** Set `"live_trading": true` to go real.

---

## ⚠️ Going Live (Real Money)

1.  **Dependencies:** Ensure `openalgo` library is installed.
2.  **Config:** 
    - Set `"live_trading": true` in `config.json`.
    - Set `"paper_trading": false`.
    - Add your API Key.
3.  **Run:** `python main.py`
    - Verify log says: `"🚀 Bot is LIVE with REAL DATA"`
    - Bot will now wait for REAL market data (no random signals).

---

**Current State:** The bot is feature-complete V1.0. Config keys and command inputs are optimized for usability.
