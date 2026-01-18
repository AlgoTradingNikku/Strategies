# PureOptionsBot: UTBot Options Strategy

A specialized, professional-grade options trading bot for OpenAlgo. This bot uses the UTBot (Upper/Lower Trend) logic to trade specific NIFTY option contracts with institutional-grade speed and risk management.

## 🚀 Key Features

*   **"Speed Demon" Websocket Integration**: Connects directly to the broker's data stream. Reaction speed is reduced from 2,000ms (polling) to **~50ms (ticks)**.
*   **Triple-Thread Architecture (The Bodyguard)**: 
    *   **Worker 1 (Risk)**: Continuous, high-speed monitoring of your SL/TSL.
    *   **Worker 2 (Scanner)**: Analyzes 3m/15m charts for trend signals.
    *   **Worker 3 (Websocket)**: Manages real-time data flow.
*   **Trend Continuation (9:15 AM Entry)**: Automatically identifies and enters trends that started on previous days, with a built-in **Gap Safety Filter**.
*   **Leash Tightener (Dynamic Profit Protection)**: Automatically tightens your Trailing Stop to a narrow level (default 1.5%) if the trend reverses while you are in profit.
*   **Auto Lot-Size Correction**: Fetches exchange rules in real-time and rounds order quantities down to the nearest lot multiple (e.g., 75 becomes 65).
*   **RMA-ATR Accuracy**: Calculations are synchronized with TradingView (RMA/Smoothed Moving Average) and utilize Heikin Ashi values for maximum precision.
*   **Dual Signal Source**: Toggle between `INDEX` (NIFTY) or `OPTION` (the contract) for signal generation.
*   **Non-Repainting Logic**: Entries are strictly generated from **Confirmed Candles** (`iloc[-2]`), ensuring backtest parity.
*   **Optimization Search Map (Deep Scan)**: A dedicated `OPT_MAP` allows running hundreds of miniature backtests to find "Golden Settings" for any timeframe (5m, 15m) without touching your live trading config.
*   **Triple-Mode Risk Management**: Choose your defense style:
    *   **PCT**: Strict scalping steps (e.g., 1.5% trail).
    *   **ATR**: Volatility-based trending (e.g., 1.5x ATR).
    *   **HYBRID**: Starts with strict steps, autoswitches to ATR for "Runners" (>10% profit).
*   **TSL Priority**: Prioritizes the Trailing Stop over trend reversals to avoid getting shaken out of winning trades prematurely.

## ⚙️ Core Configuration (`CONFIG`)

| Parameter | Description |
| :--- | :--- |
| `use_websocket` | **True** = Real-time ticks (~50ms) | **False** = REST polling. |
| `use_threading` | Runs Risk and Scanner in parallel for zero-latency protection. |
| `allow_opening_continuation` | Enables/Disables the 9:15 AM Trend Continuation entry. |
| `max_opening_gap_pct` | The maximum allowable risk gap (percentage) for continuation entries. |
| `use_reversal_leash`| Tightens TSL if trend flips while in profit. |
| `tsl_mode` | `"PCT"` (Steps), `"ATR"` (Volatility), or `"HYBRID"` (Best of Both). |
| `tsl_hybrid_threshold` | Profit % required to trigger HYBRID switch (Default: 10.0). |
| `signal_source` | Set to `"INDEX"` to follow Nifty or `"OPTION"` to follow contract. |
| `OPT_MAP` | Stores the ranges for **Deep Scans** (Sensitivity and ATR ranges). |
| `live_trade` | **True** = Real Orders | **False** = Paper/Simulated Trading. |
| `execute_backtest_orders`| Send API orders during Backtesting (Safety toggle). |

## 🛠️ How to Operate

### 1. Backtesting & Optimization
Run the strategy file to launch the **Deep Scan** engine:
1. Select `1. Run Optimization` to test the full `OPT_MAP` range.
2. Select `2. Run Backtest` to test your current live settings.
```bash
python PureOptionsStrategy.py
```

### 2. Live / Paper Trading
Run the dedicated execution runner to start the triple-thread engine:
```bash
python live_trader.py
```
*   The bot will log `[INFO] Risk Worker (Bodyguard) started` to confirm parallel protection.
*   Websocket connection status will be displayed as `[INFO] Websocket Connected`.

## 📊 HTF Filter & Signal Rules

The bot requires **Strict Synchronization** for entries:
1.  **Fresh Signal**: A new 3m cross (Bearish → Bullish for Calls).
2.  **HTF Confirmation**: HTF must already be favorably aligned at the *exact moment* of the 3m cross.

> [!IMPORTANT]
> **Established Trends & Late Alignment**: If the 3m trend is already established (e.g., Bullish) but HTF was against it, the bot will enter if HTF turns favorable within the `"late_alignment_max_candles"` limit (default: 5). If the trend is older than that, it waits for the next "Fresh" signal.

## ⚠️ Safety & Reliability

> [!TIP]
> **Websocket Fallback**: If the websocket connection blips, the bot **automatically falls back** to the 2-second REST polling safety net. You are never unprotected.

> [!WARNING]
> **Safety Toggles**: 
> *   **`live_trade`**: Controls `live_trader.py`. Keep `False` for paper trading.
> *   **`execute_backtest_orders`**: Controls `PureOptionsStrategy.py`. Keep `False` during testing.

> [!IMPORTANT]
> **1-Candle Lag**: Entries use confirmed candles (non-repainting) to ensure signal validity. TSL/SL use the **live tick** for instant reaction.
