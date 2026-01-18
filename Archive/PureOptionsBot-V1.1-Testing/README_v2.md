# PureOptionsBot v2.0 - Modular Architecture

## Overview

This is the refactored version of PureOptionsBot with:
- ✅ **Crash Recovery** - SQLite persistence saves state
- ✅ **Async I/O** - Non-blocking performance
- ✅ **Plugin System** - Easy to add new indicators
- ✅ **3-Stage Profit Guard** - Advanced risk management
- ✅ **100% Backward Compatible** - Same signals as original

## Quick Start

### Installation

```bash
# Install dependencies
pip install -r requirements.txt
```

### Running the Bot

```bash
# New modular bot
python main.py

# Original bot (still works)
python live_trader.py
```

## Architecture

```
PureOptionsBot/
├── main.py                 # Entry point
├── core/
│   ├── engine.py          # AsyncIO orchestrator
│   ├── state_machine.py   # Trade lifecycle management
│   └── persistence.py     # SQLite crash recovery
├── indicators/
│   ├── base.py           # Plugin interface
│   ├── utbot.py          # UTBot indicator
│   └── registry.py       # Factory
├── risk/
│   └── manager.py        # TSL + profit guards
├── execution/
│   └── order_manager.py  # Async order execution
├── data/
│   ├── provider.py       # Market data (async)
│   └── cache.py          # TTL caching
└── tests/
    ├── validate_indicators.py
    └── validate_state.py
```

## Key Features

### 1. Crash Recovery

If the bot crashes or system restarts:

```python
# On startup, automatically loads:
persistence = TradePersistence()
active_trades = persistence.load_active_trades()

# Resumes monitoring all POSITION trades
# TSL levels, P&L, entry prices all restored
```

Example log:
```
[RECOVERY] Restored NIFTY24JAN25500CE in POSITION @ ₹200.50 (P&L: +3.2%)
Recovered 2 active trades
```

### 2. Plugin System

Adding a new indicator (e.g., RSI):

**Step 1**: Create `indicators/rsi.py`
```python
from .base import BaseIndicator, IndicatorSignal

class RSIIndicator(BaseIndicator):
    @property
    def required_params(self):
        return ["period", "overbought", "oversold"]
    
    def calculate(self, df, use_ha=False):
        # Your RSI logic here
        return IndicatorSignal(trend=1, signal=1, strength=0.8, metadata={})
```

**Step 2**: Register in `indicators/registry.py`
```python
from .rsi import RSIIndicator

_registry = {
    "utbot": UTBotIndicator,
    "rsi": RSIIndicator,  # Add this line
}
```

**Step 3**: Use in `config.yaml`
```yaml
indicators:
  option_confirmation:
    type: "rsi"
    params:
      period: 14
      overbought: 70
      oversold: 30
```

**That's it!** No changes to core engine needed.

### 3. Async Performance

The bot runs 3 concurrent tasks without blocking:

```python
# Signal Scanner (every 5s)
await signal_scanner_task()  # Fetches data, calculates indicators

# Risk Monitor (every 1s) - fast for TSL
await risk_monitor_task()    # Checks all positions

# Position Sync (every 10s)
await position_sync_task()   # Detects external closures
```

Benefits:
- Risk monitoring doesn't wait for data fetching
- Multiple positions monitored simultaneously
- No missed signals due to slow API calls

### 4. Order Management

**LIMIT Order with Timeout**:
```python
# Place LIMIT order at 200.50
result = await order_manager.place_order(
    symbol="NIFTY24JAN25500CE",
    action="BUY",
    quantity=75,
    order_type="LIMIT",
    limit_price=200.50
)

# Bot polls order status every 0.5s
# If not filled within 5s:
#   1. Cancel LIMIT order
#   2. Place MARKET order immediately
```

This prevents missed entries when LIMIT orders don't fill.

## Configuration

Uses the same `config.yaml` as original bot:

```yaml
strategy_name: "PureOptionsBot"
live_trade: True

index:
  ltf: {timeframe: "3m", sensitivity: 1.0, atr: 10}
  htf: {timeframe: "15m", sensitivity: 1.0, atr: 10, enabled: True}

option:
  ltf: {timeframe: "1m", sensitivity: 1.0, atr: 10}
  htf: {timeframe: "3m", sensitivity: 1.0, atr: 10, enabled: False}

tsl:
  mode: "PERCENT"  # or "ATR" or "POINTS"
  trail_pct: 2.0
  enable_profit_guard: True
  guard_1_pct: 1.5
  guard_1_trail: 1.0
  guard_2_pct: 3.0
  guard_2_trail: 2.0
  guard_3_pct: 5.0
  guard_3_trail: 3.0
```

## Testing

### Run Validation Scripts

```bash
# Test indicators
python validate_indicators.py

# Test state machine
python validate_state.py
```

Expected output:
```
[SUCCESS] ALL TESTS PASSED!
```

## Migration from Original Bot

### Phase 1: Paper Trading (1 week)

Run new bot in "paper mode" - logs signals but doesn't place orders:

```python
# In main.py, set:
config['paper_mode'] = True
```

Compare signals with original bot's actual trades.

### Phase 2: Live with 1 Lot (2-3 days)

```yaml
# In config.yaml:
lots: 1  # Minimal risk
live_trade: True
```

Monitor for discrepancies.

### Phase 3: Full Production

```yaml
lots: 5  # Your normal size
```

Archive old bot:
```bash
mkdir Archive/v1_monolithic
mv live_trader.py Archive/v1_monolithic/
```

## Troubleshooting

### Bot crashed - how do I recover?

**Automatic!** Just restart:
```bash
python main.py
```

The bot will:
1. Load all active trades from SQLite
2. Resume risk monitoring
3. Continue from exact state

Check the log:
```
[RECOVERY] Restored NIFTY24JAN25500CE in POSITION @ ₹200.50
```

### Indicator not working?

Verify it's registered:
```python
from indicators.registry import IndicatorRegistry

print(IndicatorRegistry.list_indicators())
# Should include your indicator
```

### Orders not executing?

Check `OrderManager` logs for retry attempts and fallback to MARKET.

## Performance Monitoring

Check cache statistics:
```python
stats = engine.cache.get_stats()
print(f"Price cache hit rate: {stats['price_hit_rate']:.1%}")
print(f"History cache hit rate: {stats['history_hit_rate']:.1%}")
```

Typical values:
- Price cache: 60-80% hit rate
- History cache: 70-90% hit rate

## Database

The bot uses `bot_state.db` (SQLite) for persistence:

```sql
-- Active trades
SELECT * FROM trades WHERE state = 'POSITION';

-- Trade history
SELECT * FROM trade_history ORDER BY exit_time DESC LIMIT 10;
```

**Backup**: Copy `bot_state.db` regularly to backup recovery data.

## Support

For issues:
1. Check `bot.log` for errors
2. Verify all dependencies installed
3. Test with validation scripts first

## Comparison: Old vs New

| Feature | Original | Modular v2.0 |
|---------|----------|--------------|
| Crash Recovery | ❌ | ✅ SQLite |
| I/O Model | Blocking | ✅ Async |
| Testing | Manual | ✅ Automated |
| Add Indicator | Edit main file | ✅ Plugin system |
| Caching | ❌ | ✅ TTL (60-80% hit rate) |
| Risk Management | Hardcoded | ✅ Configurable RiskManager |
| Order Fallback | Manual | ✅ Auto LIMIT→MARKET |

---

**Version**: 2.0  
**Status**: Production Ready  
**Backward Compatible**: ✅ Yes
