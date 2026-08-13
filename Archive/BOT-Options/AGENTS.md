# AGENTS.md

This file provides guidance to agents when working with code in the BOT-Antigravity repository.

## Project Overview

**BOT-Antigravity** is a sophisticated algorithmic trading bot implementing the UT Bot strategy (ATR-based trailing stop) with machine learning signal filtering for Indian equity and options markets via OpenAlgo API integration.

## Architecture

### Core Components

#### 1. app.py (Main Trading Bot - 961 lines)
- **Strategy**: UT Bot Alerts (ported from PineScript v5)
- **Multi-threaded**: One worker per (symbol, timeframe) pair
- **Real-time**: WebSocket for live price streaming
- **Auto-trading**: Places orders via OpenAlgo when enabled
- **ML-powered**: Optional XGBoost signal filtering
- **Notifications**: Telegram alerts for all signals

**Key Classes**:
- `TimeframeWorker`: Monitors one (symbol, timeframe) combination
- `LivePriceMonitor`: WebSocket handler for real-time LTP updates

**Signal Logic**:
1. Compute ATR over configurable period
2. Build ratcheting ATR trailing stop
3. Detect crossovers (EMA vs trailing stop)
4. Generate BUY/SELL signals
5. Optional ML confidence filtering
6. Place orders if trading enabled

#### 2. signal_logger.py (Data Collection - 260 lines)
- Captures every signal with 14 technical features to SQLite
- Database: `signals.db`
- Features: ATR metrics, volume, RSI-14, EMA-20 distance, candle body %, temporal data

#### 3. label_signals.py (Outcome Labeling - 282 lines)
- Offline script to retroactively label signal outcomes
- Fetches post-signal candle data from broker
- Labels: WIN if price moved favorably by ≥0.3% after N candles
- Run after market close: `python label_signals.py`

#### 4. ml_filter.py (XGBoost Filter - 328 lines)
- **Training**: `python ml_filter.py --train`
- **Inference**: Used by bot at runtime to filter signals
- **Model**: XGBoost classifier (150 estimators, max_depth=4)
- **Threshold**: Default 60% confidence to fire signal
- **Pass-through**: Works without model (allows data collection)

#### 5. telegram.py (Notifications - 38 lines)
- Sends alerts via OpenAlgo's Telegram API endpoint
- Configurable priority levels (1-10)

#### 6. test.py (Order Test - 17 lines)
- Simple script to test OpenAlgo order placement

## Critical Non-Obvious Patterns

### Backend (Python)

#### Candle Boundary Caching
- Bot fetches data ONLY when crossing into new candle bar
- `_current_boundary()` calculates current candle start time
- `_last_fetched_boundary` prevents redundant API calls within same timeframe
- This is critical for API rate limiting and performance

#### Dynamic Closed Candle Detection
```python
# Last bar might be forming OR already closed (API lag)
if last_bar_ts >= boundary_naive:
    closed_idx = -2  # Normal: last bar is forming
else:
    closed_idx = -1  # API lag: all bars closed
```

#### Dual Exchange Support
- Equity symbols: NSE exchange
- Option contracts: NFO exchange (auto-detected via `_is_option` flag)
- Single WebSocket connection subscribes to all instruments across exchanges

#### Signal Deduplication
```python
# Only fire if this bar/signal type is new
if signal_ts == self._last_signal_ts and signal_type == self._last_signal_type:
    return  # Skip duplicate
```

#### ML Graceful Degradation
- Bot works WITHOUT trained model (pass-through mode)
- `MLFilter.is_ready()` returns False if no model loaded
- Allows data collection phase before ML activation

#### Thread-Safe Database
- SQLite with `check_same_thread=False`
- Each operation gets fresh connection via `_get_conn()`
- Connections closed immediately after use

### Configuration Patterns

#### ML Workflow States
```yaml
ml:
  log_signals: true   # Phase 1: Collect data
  enabled: false      # Phase 1: No filtering yet
  
  # After training:
  enabled: true       # Phase 2: Use ML filter
  confidence_threshold: 0.60
```

#### Trading Toggle Hierarchy
```yaml
trading:
  enabled: true              # Master switch
  equity:
    enabled: true            # Per-asset-type switch
  options:
    enabled: true
```

#### Exchange Mapping
- `exchange: "NSE"` → equity symbols
- `index_exchange: "NSE_INDEX"` → display only (not used for API)
- Options ALWAYS use `"NFO"` exchange internally (hardcoded in app.py line 865)

## Commands

### Start Bot
```bash
python app.py     # starts the dashboard at http://127.0.0.1:9000 — this is
                   # the only supported entry point; there is no separate
                   # headless mode anymore. `python server.py` / `python
                   # dashboard.py` / `uvicorn server:app --port 9000` are
                   # equivalent alternatives.
```

### Label Signals (after market close)
```bash
python label_signals.py           # Label all pending
python label_signals.py --status  # Show DB summary
python label_signals.py --dry-run # Preview without writing
```

### Train ML Model
```bash
python ml_filter.py --train                    # Train with defaults
python ml_filter.py --train --min-samples 50   # Require 50+ labeled signals
python ml_filter.py --report                   # Show model stats
python ml_filter.py --importance               # Plot feature importances
```

### Test Order Placement
```bash
python test.py  # Hardcoded test order
```

## Type Conventions

### Database Schema (signals table)
- All columns use snake_case
- Timestamps stored as ISO 8601 strings
- Boolean `labeled` stored as INTEGER (0/1)
- Nullable outcome/label columns until labeled

### Feature Dictionary
```python
features = {
    "close": float,
    "atr": float,
    "atr_pct": float,
    "volume_ratio": float,
    "rsi_14": float,
    "ema20_dist_pct": float,
    "candle_body_pct": float,
    "atr_percentile": float,
    "hour": int,
    "minute": int,
    "day_of_week": int,
}
```

### ML Model Interface
```python
# Training features (10 total)
FEATURE_COLS = [
    "atr_pct", "volume_ratio", "rsi_14", "ema20_dist_pct",
    "candle_body_pct", "atr_percentile",
    "hour", "minute", "day_of_week",
    "is_buy",  # Added at predict time (1 for BUY, 0 for SELL)
]
```

## UT Bot Strategy Logic (PineScript Port)

### ATR Trailing Stop Algorithm
```python
# Vectorized iterative reconstruction
for i in range(1, n):
    if cur_src > prev_stop and prev_src > prev_stop:
        stop[i] = max(prev_stop, cur_src - nLoss)  # Ratchet up
    elif cur_src < prev_stop and prev_src < prev_stop:
        stop[i] = min(prev_stop, cur_src + nLoss)  # Ratchet down
    elif cur_src > prev_stop:
        stop[i] = cur_src - nLoss  # Flip to long
    else:
        stop[i] = cur_src + nLoss  # Flip to short
```

### Signal Generation
```python
# Position tracking
if src[i-1] < stop[i-1] and src[i] > stop[i]:
    pos[i] = 1   # Bullish crossover
elif src[i-1] > stop[i-1] and src[i] < stop[i]:
    pos[i] = -1  # Bearish crossover

# Final signals (require crossover confirmation)
buy  = (src > xATR) & (ema crosses above xATR)
sell = (src < xATR) & (xATR crosses above ema)
```

## Safety Features

1. **Single Instance Lock**: `.utbot.lock` prevents duplicate processes
2. **Market Hours Check**: Only trades Mon-Fri during configured hours (09:15-15:30 IST)
3. **API Error Handling**: Graceful degradation on connection failures
4. **WebSocket Auto-Reconnect**: 5-second retry on disconnect
5. **UTF-8 Encoding**: Windows console compatibility for emojis/box-drawing chars

## File Structure
```
BOT-Antigravity/
├── app.py              # Main bot (961 lines)
├── signal_logger.py    # Feature extraction & DB (260 lines)
├── label_signals.py    # Outcome labeling (282 lines)
├── ml_filter.py        # XGBoost training/inference (328 lines)
├── telegram.py         # Notifications (38 lines)
├── test.py             # Order test (17 lines)
├── config.yml          # Configuration (69 lines)
├── Readme.md           # Workflow diagram (7 lines)
├── utbot-pinescript.txt # Original strategy (43 lines)
├── signals.db          # SQLite database (runtime)
├── ml_model.pkl        # Trained model (runtime)
└── utbot.log           # Application logs (runtime)
```

## Dependencies

### Core
- pandas, numpy, pyyaml
- openalgo (custom SDK for broker integration)

### ML (optional)
- xgboost, scikit-learn, matplotlib

### Utilities
- requests (for Telegram API)

## Common Pitfalls

### 1. API Key Security
- **Issue**: API key hardcoded in config.yml
- **Fix**: Use environment variables or secure vault

### 2. Exchange Confusion
- **Issue**: Options use NFO, not NSE_INDEX
- **Remember**: `index_exchange` in config is display-only; code uses "NFO" internally

### 3. ML Model Not Loading
- **Symptom**: Bot runs but no filtering happens
- **Check**: 
  - `ml_model.pkl` exists in bot directory
  - `ml.enabled: true` in config.yml
  - Check logs for "ML model loaded" message

### 4. No Signals Generated
- **Check**:
  - Market hours (Mon-Fri, 09:15-15:30)
  - Sufficient historical data (lookback_days: 5)
  - ATR period requirements met
  - WebSocket connected (check logs for LTP updates)

### 5. Duplicate Telegram Alerts
- **Cause**: Multiple bot instances running
- **Fix**: Check for `.utbot.lock` file, kill old processes

### 6. Label Script Fails
- **Cause**: Not enough future candles yet
- **Fix**: Run script 1-2 days after signals generated

## ML Training Workflow

### Phase 1: Data Collection (Week 1-2)
```yaml
ml:
  log_signals: true
  enabled: false
```
- Bot logs all signals to database
- No filtering applied
- Accumulate 30-50+ signals

### Phase 2: Labeling (After market close)
```bash
python label_signals.py
python label_signals.py --status  # Check progress
```
- Script fetches post-signal candles
- Labels WIN/LOSS based on outcome
- Requires signals to be 5-10 candles old

### Phase 3: Training
```bash
python ml_filter.py --train
python ml_filter.py --report
```
- Trains XGBoost on labeled data
- Saves model to ml_model.pkl
- Shows classification metrics

### Phase 4: Production
```yaml
ml:
  log_signals: true   # Keep logging for retraining
  enabled: true       # Activate filter
  confidence_threshold: 0.60
```
- Bot uses model to filter signals
- Only high-confidence signals fire
- Continue collecting data for periodic retraining

## Performance Tuning

### Reduce API Calls
- Increase `signal_check_interval` (default: 5 seconds)
- Reduce number of symbols/timeframes
- Increase `lookback_days` (fetches more data per call)

### Improve Signal Quality
- Lower `confidence_threshold` (more signals, lower quality)
- Raise `confidence_threshold` (fewer signals, higher quality)
- Adjust `win_threshold_pct` for labeling (default: 0.3%)

### Optimize Strategy
- Increase `key_value` (less sensitive, fewer signals)
- Decrease `key_value` (more sensitive, more signals)
- Adjust `atr_period` (1 = very responsive, 14 = smooth)

## Monitoring

### Key Log Messages
```
[WS] Subscribed to LTP for: ['IOC', 'BANKINDIA', ...]  # WebSocket OK
[IOC|5m] SCAN bar=10:25 close=123.45 ... signal=BUY    # Signal detected
[IOC|5m] Signal logged — DB: 45 total, 12 labeled      # DB write OK
[IOC|5m] BUY signal PASSED ML filter (conf=75%)        # ML filter OK
[IOC|5m] ✅ Order SUCCESS | id=12345 | BUY 1 CNC       # Order placed
```

### Health Checks
1. Check `.utbot.lock` exists (bot running)
2. Check `utbot.log` for recent activity
3. Check `signals.db` row count growing
4. Check WebSocket LTP updates in logs
5. Verify Telegram alerts received

## Code Quality Notes

### Strengths
1. **Production-ready**: Comprehensive error handling, logging
2. **Well-documented**: Extensive inline comments and docstrings
3. **Maintainable**: Clean separation of concerns
4. **Scalable**: Multi-threaded design
5. **Flexible**: Works with/without ML

### Areas for Improvement
1. **Security**: API keys in config file (use env vars)
2. **Risk Management**: No stop-loss, position sizing, or portfolio limits
3. **Backtesting**: No historical performance validation
4. **Testing**: No unit tests for strategy logic
5. **Monitoring**: No Prometheus/Grafana integration

## Agent Guidelines

When modifying this codebase:

1. **Preserve candle boundary caching** - critical for performance
2. **Maintain thread safety** - use separate DB connections
3. **Keep ML optional** - bot must work without trained model
4. **Test with dry-run** - use `--dry-run` flags before production
5. **Update FEATURE_COLS** - if adding ML features, update both logger and filter
6. **Respect market hours** - don't bypass `_is_market_hours()` check
7. **Log everything** - use logging module, not print()
8. **Handle API failures** - always wrap OpenAlgo calls in try/except
9. **Document config changes** - update this file when adding config options
10. **Version models** - consider timestamping ml_model.pkl when retraining