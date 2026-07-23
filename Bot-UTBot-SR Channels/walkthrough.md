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

## Usage

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

