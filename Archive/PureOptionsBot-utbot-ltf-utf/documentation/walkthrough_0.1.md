# Walkthrough: Still Bullish (Pullback) Logic

I have successfully implemented the **"Still Bullish" (Pullback)** logic, giving you professional re-entry capabilities and mid-trend scanning.

## Changes Overview

### 1. Configuration Toggles
Added a new `entry_logic` block in [config.yaml](file:///c:/Rahul/06_Nikku/Strategies/PureOptionsBot/config.yaml):
```yaml
entry_logic:
  allow_index_pullback: True      # Detect mid-trend bounces on Nifty
  allow_option_pullback: True     # Detect mid-trend bounces on Option Premium
  pullback_warmup_candles: 3      # Prevents "chasing" too early after trend change
```

### 2. Core Indicator Update
Enhanced the `calculate_utbot` function in [live_trader.py](file:///c:/Rahul/06_Nikku/Strategies/PureOptionsBot/live_trader.py) to detect Signal Codes:
*   `1/-1`: Fresh Crossover (Original Signal).
*   `2/-2`: **Still State** (Pullback Pivot).
*   **Logic:** Detects a [Red-to-Green] candle bounce while the price is safely above the ATR line.

### 3. Dual-Gated Execution
The bot now handles two distinct phases:
*   **Scanner:** Uses `allow_index_pullback` to start observing an option mid-trend.
*   **Observer:** Uses `allow_option_pullback` to trigger the entry even if the premium already crossed up earlier.

## How to Test
1.  **Restart the Bot:** Run `uv run live_trader.py`.
2.  **Watch the Logs:** Look for the new `[PULLBACK]` and `[FRESH]` labels in the signals.
3.  **Tweak Toggles:** Try turning `index_pullback` OFF if you only want to trade the 9:15 AM open.

---

## Verification Results
- [x] Dual toggles added and reloadable.
- [x] Signal Code 2 correctly identifies Red-to-Green transitions.
- [x] HTF filters still applied to Pullback entries for safety.
- [x] Warmup candles prevent immediate re-entry after a trend flip.

> [!TIP]
> Use `allow_index_pullback: True` on strong trending days to ensure you get back into the move even if stops are hit.
