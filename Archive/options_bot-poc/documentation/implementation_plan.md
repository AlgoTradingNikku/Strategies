# Options Trading Bot - Implementation Plan

A professional automated trading system for NIFTY and Bank Nifty options using OpenAlgo API with multi-timeframe analysis, extensible indicator architecture, and dynamic configuration management.

## User Review Required

> [!IMPORTANT]
> **Strategy Architecture - Multi-Timeframe Approach**
> 
> The bot uses a sophisticated 4-filter entry system with timeframe separation:
> 
> **Higher Timeframe (HTF) - Default: 15 minutes**
> - **EMA Crossover**: Determines overall trend direction (CE vs PE)
> - Role: Filters out counter-trend trades
> 
> **Lower Timeframe (LTF) - Default: 5 minutes**
> - **EMA Alignment**: Ensures recent trend matches HTF
> - **StochRSI**: Triggers entry at momentum extremes
> - **RSI**: Confirms signal reliability
> 
> **Entry Example (Call Option):**
> ```
> ✅ HTF (15min): 9 EMA > 21 EMA → Uptrend confirmed
> ✅ LTF (5min): 9 EMA > 21 EMA → Recent trend aligned
> ✅ LTF (5min): StochRSI > 80 → Momentum building
> ✅ LTF (5min): RSI > 55 → Signal confirmed
> → BUY NIFTY CE
> ```

> [!IMPORTANT]
> **Extensible Indicator System**
> 
> The bot is designed with a plugin architecture for easy indicator additions:
> - **Current Indicators**: EMA, RSI, StochRSI
> - **Easy to Add**: VWAP, Supertrend, MACD, Bollinger Bands, etc.
> - **Naming Convention**: `indicator_htf` or `indicator_ltf` (not tied to specific timeframes)
> - **Configuration**: Enable/disable any indicator without code changes
> 
> **You can add new indicators in the future by:**
> 1. Adding calculation function to `indicators.py`
> 2. Enabling in `config.json`
> 3. Optionally adding custom logic to `strategy.py`

> [!IMPORTANT]
> **Dual Entry System - Catch Trends and Ride Them**
> 
> **Mode 1: Initial Trend Entry**
> - Triggers on fresh EMA crossover
> - All 4 filters must align
> - Catches the start of new trends
> 
> **Mode 2: Pullback Re-Entry**
> - Triggers when price pulls back to EMA and bounces
> - HTF trend must still be intact
> - Allows you to re-enter during trending days
> - Configurable: max re-entries, minimum gap between trades
> 
> **Example:** You won't miss big trending days - bot can enter multiple times as price pulls back and resumes!

> [!IMPORTANT]
> **Dynamic Configuration System - JSON + Commands**
> 
> **Two ways to change settings WITHOUT restarting:**
> 
> **Method 1: Edit config.json**
> - Modify any setting in `config.json`
> - Bot auto-reloads within 5 seconds
> - All changes logged
> 
> **Method 2: Runtime Commands**
> - Type commands while bot runs
> - `sl 40` - Change stop loss to 40%
> - `toggle rsi_ltf` - Enable/disable RSI
> - `show config` - Display current settings
> - `positions` - View open positions
> 
> **Safety & Dynamic Updates:** 
> - **Entry Logic Changes (HTF Filter, EMA, Re-entry):** Apply to **NEXT** trade/re-entry.
>   - *Reason:* You cannot change the reason why an *existing* trade was taken.
>   - *Example:* If you disable HTF Filter, the *next* trade will ignore HTF.
>   - *Re-Entry:* Changing Pullback settings affects the *next* re-entry attempt (since it's a new order).
> - **Exit & Risk Changes (SL, TSL, Exits):** Apply to **ACTIVE** positions on config reload.
>   - *Risk:* Changing SL/TSL updates active trades instantly.
>   - *Strategy Exit:* If you enable/disable "Indicator Reversal Exit", active trades start/stop using it instantly.
>   - **Runtime Commands:** Apply instant overrides to active trades.

> [!WARNING]
> **Default Strategy Parameters**
> 
> All parameters are configurable via `config.json`:
> 
> **Timeframes:**
> - HTF: 15 minutes (adjustable to 30min, 1hour)
> - LTF: 5 minutes (adjustable to 1min, 3min)
> 
> **Indicator Parameters:**
> - EMA: 9 and 21 periods (both HTF and LTF)
> - RSI: 14 period, thresholds 55/45
> - StochRSI: 14 period, K=3, D=3, thresholds 80/20
> 
> **Risk Management:**
> - Stop Loss: 30% of premium
> - Target: 50% of premium
> - Trailing Stop: 5% (activates after 30% profit)
> - Max Positions: 2 (1 NIFTY + 1 Bank Nifty)
> - Auto-exit: 15 minutes before market close (3:15 PM)
> 
> **Entry Modes:**
> - Initial Entry: Enabled
> - Pullback Re-entry: Enabled (max 3 per day, 15min gap)

> [!CAUTION]
> **API Configuration**
> 
> API credentials will be stored in `config.json`:
> - **API Key**: `a2edab0147e5058617b63b677c82c5c44533d356d8b8f33734127d6c5f029a55`
> - **Host**: `http://127.0.0.1:5000`
> - **WebSocket**: `ws://127.0.0.1:8765`
> 
> Keep this file secure. Add to `.gitignore` if using version control.

> [!WARNING]
> **Trading Risks**
> 
> This bot executes real trades automatically:
> 1. **Start with paper trading mode** to validate strategy
> 2. **Test for at least 2-3 days** before going live
> 3. **Use small position sizes initially** (1 lot)
> 4. **Monitor actively** during first week
> 5. **Never invest more than you can afford to lose**
> 6. **Options carry high risk** due to time decay

## Proposed Changes

### Core Architecture

#### [NEW] [config.json](file:///c:/Rahul/06_Nikku/Strategies/options_bot/config.json)
**Main configuration file - Edit this to control bot behavior:**
- API credentials (key, host, websocket URL)
- Timeframe settings (HTF and LTF durations)
- Indicator enable/disable flags (htf/ltf naming)
- Indicator parameters (periods, thresholds)
  - StochRSI: Period (14), K (3), D (3), Oversold (20), Overbought (80)
  - RSI: Period (14), Thresholds (55/45)
  - EMA: Fast (9), Slow (21)
  - **UT Bot:** Key (2), ATR Period (10) (New Addition)
- Entry mode settings (initial entry, pullback re-entry)
- Risk management rules (stop loss, targets, limits)
- Trading hours and session control
- Paper trading mode toggle
- Telegram settings (optional)

**Features:**
- Auto-reloads every 5 seconds (no restart needed)
- Validation on load (prevents invalid settings)
- Changes logged automatically
- JSON format (easy to edit, readable)

---

#### [NEW] [config.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/config.py)
**Configuration loader and manager:**
- Reads and validates `config.json`
- Monitors file for changes (auto-reload)
- Provides typed access to settings
- Validates parameter ranges
- Handles missing/malformed config gracefully
- Logs all configuration changes
- Default fallback values for safety

**Key Methods:**
- `load_config()` - Load from JSON file
- `check_for_updates()` - Detect file changes
- `validate_config()` - Ensure settings are valid
- `get(key)` - Safe parameter access

---

### Indicator System (Extensible)

#### [NEW] [indicators.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/indicators.py)
**Modular technical indicator library:**

**Base Framework:**
- Indicator registration system
- Data validation utilities
- Common helper functions
- Error handling for calculations

**Implemented Indicators:**
- `calculate_ema(df, period)` - Exponential Moving Average
- `calculate_rsi(df, period)` - Relative Strength Index
- `calculate_stochrsi(df, period, k, d)` - Stochastic RSI
- `calculate_utbot(df, key, period)` - UT Bot Trailing Stop (QuantNomad)
- `detect_ema_crossover(df, fast, slow)` - Crossover detection

**Extensibility Design:**
- Clear function signature pattern
- Accepts pandas DataFrame, returns DataFrame
- Each indicator is independent module
- Easy to add: VWAP, Supertrend, MACD, ATR, Bollinger Bands, etc.

**Future Indicator Template:**
```python
def calculate_vwap(df):
    """Calculate VWAP - Volume Weighted Average Price"""
    # Your implementation here
    return df
```

---

### Strategy Engine

#### [NEW] [strategy.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/strategy.py)
**Core trading logic and signal generation:**

**Entry Signal Generation:**
- **Data Source:** **NIFTY 50 SPOT** (Index Data).
  - *Reason:* Indicators (RSI, EMA) are more stable on Spot than Options (which have time decay/theta).
- Multi-timeframe analysis (HTF + LTF) on Spot Charts.
- Checks only enabled indicators from config
- 4-filter system implementation:
  1. HTF EMA trend direction
  2. LTF EMA alignment
  3. LTF StochRSI momentum
  4. LTF RSI- Configurable entry modes (ALL, MAJORITY, ANY)
- **Signal Aggressiveness:** `"wait_for_candle_close": true` (Default: Safe) vs `false` (Instant).
- Option type selection (CE vs PE)
- **Strike Selection:** Configurable via `"strike_mode"`:
  - `ATM_OFFSET`: Uses `"strike_step"` (e.g., `0`=ATM, `+1`=OTM, `-1`=ITM)
  - `PREMIUM`: Uses `"target_premium"` (e.g., Select strike closest to `100`)
  - *Note: Delta-based selection deferred to V2.*
- **Expiry Selection:**
  - `expiry_type`: `"CURRENT_WEEKLY"` (Default), `"NEXT_WEEKLY"`, or `"MONTHLY"`.
  - `auto_switch_on_expiry_day`: `false` (Default). If True, switches to Next Week/Month on expiry day to avoid 0DTE risks.

**Dual Entry System:**
1. **Initial Trend Entry:**
   - Fresh EMA crossover detection
   - All filters must align
   - Catches trend start
   
2. **Pullback Re-Entry:**
   - HTF trend still intact
   - Price pulls back to LTF 21 EMA
   - Price bounces back above EMA
   - StochRSI shows momentum returning
   - Configurable limits (max re-entries, time gaps)

**Exit Signal Generation:**
Multiple exit methods (configurable in `config.json`):
- **Financial Exits (Option Price):**
  - TSL "Highest Wins" (Primary Protection): `Enable: True`
  - Target Profit (Upside Exit): `Enable: True`
- **Technical Exits (Index Price):**
  - Indicator Reversal: `Enable: True/False` (e.g., Exit if HTF trend flips).
  - Time-based Exit: `Enable: True` (3:15 PM)

**Strategy Modes:**
- Configurable indicator combinations
- Adjustable filter requirements
- Multiple exit strategy combinations

---

### Trading Operations

#### [NEW] [order_manager.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/order_manager.py)
**Order execution and management using OpenAlgo API:**

**Core Functions:**
- Initialize OpenAlgo client with credentials
- Place CE/PE buy orders
- Automatic ATM strike selection
- Order validation (margin, limits)
- Order status tracking
- Position monitoring
- Order modification support
- Bulk order cancellation

**Option Symbol Resolution:**
- Fetch current spot price (NIFTY/Bank Nifty)
- Calculate ATM strike (nearest round number)
- Get option chain data
- Select appropriate expiry
- Construct option symbol

**Error Handling:**
- API connection retries
- Rate limit management
- Invalid order detection
- Network failure recovery

**Integration Points:**
- Uses OpenAlgo's `placesmartorder()` (For advanced execution).
- Uses `option_symbol()` or Chain to resolve NIFTY24...CE first.
- `openposition()` for position tracking
- `closeposition()` for exits

---

### Risk Management

#### [NEW] [risk_manager.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/risk_manager.py)
**Comprehensive risk control system:**

**Detailed TSL & Profit Locking Logic ("Highest Wins" Strategy):**
- **Data Source:** **OPTION PREMIUM** (Real-time Trade Price).
The active exit price is the **MAXIMUM** of these three values at any given moment:
1.  **Standard TSL Calculation (Line B)**: `Peak Price - 5%` (Default). Provides growing room for the trade.
2.  **Minimum TSL Floor (Line A)**: `Peak Price - Min Points`. Determined at entry from Price Map (e.g., Entry < ₹25 → Min 1.5 pts). Prevents noise exits.
3.  **Profit Lock / Breakeven (Line C)**: `Entry Price + Buffer`. Activates only after profit threshold is met.

**Default Configuration (The "1:2 Ratio" Rule):**
- **TSL Percentage**: **5%** (Loose enough for Nifty volatility).
- **Profit Lock**: **1%** (Safety net).
- **Activation**: **3%** (Trigger).
- **Logic**: Wait for 3% profit to lock 1%. This ensures a **2% healthy buffer** exists when protection turns on, preventing immediate shakeout.

**Manual Overrides (The Override):**
- **Manual Lock**: `lock NIFTY 140`. Sets a hard floor that overrides all automated lines.
- **Manual Tightening**: `trailing 2%`. Manually tightens the TSL % during runtime for end-game squeezes.

**Position Monitoring:**
- Real-time P&L calculation
- Stop loss tracking (fixed percentage)
- Target profit monitoring
- Trailing stop loss (dynamic "Highest Wins" logic)
- Maximum profit tracking (for trailing stop)

**Risk Limits:**
- Maximum simultaneous positions
- Maximum re-entries per instrument per day
- Minimum gap between re-entries (time-based) (Cool-down timer applied per-instrument after CLOSING a trade. Does not block pyramiding.)
- Daily loss limit (circuit breaker) (Set to 0 to Disable)
- Maximum capital allocation per trade

**Exit Triggers:**
- Stop loss hit → Immediate exit
- Target reached → Profit booking
- Trailing stop calculated price hit → Protect profits
- Indicator reversal → Strategic exit
- Market close approaching → Time-based exit
- Daily loss limit exceeded → Trading halt

**Safety Features:**
- Pre-trade risk checks
- Position limit enforcement
- Prevents over-leveraging
- Emergency stop-all mechanism

---

### Data Management

#### [NEW] [data_handler.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/data_handler.py)
**Multi-timeframe market data collection:**

**Data Sources:**
- Historical data via OpenAlgo `history()` API
- Real-time data via WebSocket (ws://127.0.0.1:8765)
- Option chain data for strike selection

**Multi-Timeframe Management:**
- Fetch HTF data (15min candles)
- Fetch LTF data (5min candles)
- Synchronize data across timeframes
- Maintain rolling window for indicators

**Data Processing:**
- Convert to pandas DataFrame
- Handle missing/incomplete candles
- Data quality validation
- Timezone handling (IST)

**Caching:**
- In-memory cache for recent data
- Minimize API calls
- Smart refresh logic

**WebSocket Integration:**
- Subscribe to NIFTY and Bank Nifty
- Handle tick data
- Update candles in real-time
- Reconnection logic

---

### User Interface

#### [NEW] [command_processor.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/command_processor.py)
**Runtime command interface for bot control:**

**Command Categories:**

**Command Value Parsing Rules:**
- **Units**: `20%` = Percentage, `20` = Points/Rupees (default).
- **Absolute Setting (No Sign)**: `25` → Sets value directly to 25. (Use this to increase/decrease by typing the new total).
- **Relative Adjustments (+/-)**: `+5` or `-5` → Adds or subtracts from current value.
- **Flexibility**: To increase from 20 to 25, you can type `25` (Absolute) OR `+5` (Relative).
- **Examples**:
  - `sl 20%` → Set Stop Loss to 20% (Absolute)
  - `sl +5%` → Increase current SL by 5% (e.g., 20% → 25%)
  - `sl 25%` → Set SL to 25% (Implicit increase from 20%)
  - `trailing 40` → Set Trailing Stop to 40 points
  - `target -10` → Decrease target by 10 points
  - `lock NIFTY +5` → Move manual lock up by 5 points

**Configuration Commands:**
- `sl <value>` - Change stop loss (supports %/pts/relative)
- `target <value>` - Change target (supports %/pts/relative)
- `trailing <value>` - Change trailing stop (supports %/pts/relative)
- `toggle <indicator>` - Enable/disable indicator
- `reload` - Reload config from JSON file

**Monitoring Commands:**
- `status` - Show bot status and active settings
- `positions` - Display open positions with P&L
- `indicators` - Show enabled indicators
- `show config` - Display full configuration
- `stats` - Trading statistics (today's trades, P&L)

**Control Commands:**
- `pause` - Pause trading (no new entries)
- `resume` - Resume trading
- `close all` - Close all open positions
- `exit` - Graceful shutdown

**Information Commands:**
- `help` - Display command reference
- `signals` - Show latest signal analysis
- `logs` - Display recent log entries

**Implementation:**
- Runs in separate thread
- Non-blocking (doesn't interrupt trading loop)
- Command validation and error handling
- Confirmation prompts for dangerous commands

---

### Logging and Monitoring

#### [NEW] [logger.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/logger.py)
**Comprehensive logging and monitoring system:**

**Logging Levels:**
- Trade logs (entries, exits, signals)
- Error logs (exceptions, API failures)
- Config change logs
- Performance logs (P&L, win rate)
- Debug logs (detailed execution flow)

**Log Outputs:**
- File: `logs/trading_{date}.log` (daily rotation)
- Console: Real-time monitoring
- Trade journal: `logs/trades_{date}.csv`

**Logged Information:**
- Timestamp (IST)
- Signal generation details
- Order placement attempts
- Position updates
- P&L changes
- Config modifications
- Errors and warnings

**Analytics:**
- Daily trade summary
- Win/loss statistics
- Average profit/loss
- Strategy performance metrics
- Indicator hit rate

---

### Additional Features

#### [NEW] [notifier.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/notifier.py)
**Telegram notification system (optional):**

**Notification Types:**
- Trade alerts (entry/exit with details)
- Position updates (P&L milestones)
- Risk alerts (stop loss hit, daily limit)
- Error alerts (API failures, critical errors)
- Daily summary (end-of-day report)
- Config change notifications

**Integration:**
- Uses OpenAlgo's Telegram integration
- Configurable notification levels
- Enable/disable via config
- Rate limiting to avoid spam

---

#### [NEW] [main.py](file:///c:/Rahul/06_Nikku/Strategies/options_bot/main.py)
**Main bot orchestrator and entry point:**

**Initialization:**
- Load configuration from JSON
- Initialize OpenAlgo client
- Setup data handlers (HTF + LTF)
- Initialize strategy engine
- Start command listener thread
- Verify API connectivity

**Main Trading Loop:**
```
While market open:
  1. Check for config updates (auto-reload)
  2. Fetch latest HTF and LTF data
  3. Calculate indicators on both timeframes
  4. Generate entry signals (initial + pullback)
  5. If signal → Check risk limits → Place order
  6. Monitor open positions
  7. Check exit conditions
  8. Process exits if triggered
  9. Handle commands from user
  10. Sleep interval (configurable)
```

**Features:**
- Paper trading mode (simulates trades without execution)
- Graceful shutdown (closes positions or warns)
- Signal interrupt handling (Ctrl+C)
- Error recovery and retry logic
- Session management (market hours)

---

### Supporting Files

#### [NEW] [requirements.txt](file:///c:/Rahul/06_Nikku/Strategies/options_bot/requirements.txt)
Python dependencies:
```
openalgo>=1.0.0
pandas>=2.0.0
numpy>=1.24.0
websocket-client>=1.5.0
python-dotenv>=1.0.0
```

---

#### [NEW] [config.schema.json](file:///c:/Rahul/06_Nikku/Strategies/options_bot/config.schema.json)
JSON schema for configuration validation:
- Defines required fields
- Validates data types
- Enforces value ranges
- Provides default values
- Documents each setting

---

#### [NEW] [README.md](file:///c:/Rahul/06_Nikku/Strategies/options_bot/README.md)
Comprehensive user documentation:
- Installation instructions
- Quick start guide
- Configuration guide (config.json explained)
- Command reference (all runtime commands)
- Strategy explanation (multi-timeframe, filters)
- Adding custom indicators tutorial
- Troubleshooting guide
- Risk warnings and best practices
- FAQ

---

#### [NEW] [.gitignore](file:///c:/Rahul/06_Nikku/Strategies/options_bot/.gitignore)
Ignore sensitive and generated files:
```
config.json
logs/
__pycache__/
*.pyc
.env
```

---

#### [NEW] [config.example.json](file:///c:/Rahul/06_Nikku/Strategies/options_bot/config.example.json)
Template configuration file:
- Example values for all settings
- Inline comments explaining each parameter
- Multiple preset configurations (conservative, moderate, aggressive)
- Instructions for customization

## Architecture Highlights

### Modular Design
```
Data Layer → Indicator Layer → Strategy Layer → Execution Layer → Risk Layer
     ↓            ↓               ↓                ↓               ↓
data_handler → indicators → strategy → order_manager → risk_manager
```

### Configuration Flexibility
```
config.json ←→ config.py ←→ All Components
      ↑
Runtime Commands
```

### Extensibility Points
1. **Indicators**: Add new calculations to `indicators.py`
2. **Strategy Logic**: Modify `strategy.py` for custom entry/exit rules
3. **Risk Rules**: Extend `risk_manager.py` with new limits
4. **Commands**: Add new commands to `command_processor.py`

## Phased Implementation & Verification Roadmap

We will build this system in 5 strict phases. Each phase must be verified before moving to the next.

### Phase 1: Foundation (Project Structure)
**Goal:** Establish the skeleton and configuration system.
- **Tasks:**
  - Create `config.json` (The Brain parameters).
  - Implement `config.py` (Validation & Auto-reload).
  - Create `main.py` entry point (Basic loop).
- **Verification:**
  - Run `python main.py` -> Verify it loads config.
  - Edit `config.json` -> Verify "Config Reloaded" message appears.
  - Test invalid inputs (e.g., negative Stop Loss) -> Verify error handling.

### Phase 2: Data & Intelligence (The Brain)
**Goal:** connect to API and calculate indicators.
- **Tasks:**
  - Implement `data_handler.py` (Fetch Spot Data via History & WebSocket).
  - Implement `indicators.py` (EMA, RSI, StochRSI logic).
- **Verification:**
  - Compare calculated values (`ema_9`, `rsi_14`) against TradingView.
  - Verify data updates in real-time.

### Phase 3: Strategy Core (The Decision Maker)
**Goal:** Generate correct signals from data.
- **Tasks:**
  - Implement `strategy.py`.
  - Code the 4-Filter Logic (HTF Trend, LTF Alignment, Momentum, RSI).
  - internalize `wait_for_candle_close` logic.
- **Verification:**
  - Feed dummy data patterns.
  - Confirm SIGNAL triggers only when ALL conditions meet.
  - Confirm signal is REJECTED if one filter fails.

### Phase 4: Execution & Risk (The Bodyguard)
**Goal:** Place orders and manage them safely.
- **Tasks:**
  - Implement `order_manager.py` (Smart Order Placement, Expiry Selection).
  - Implement `risk_manager.py` (Highest Wins TSL, Gap Logic, Daily Limit).
  - **Code Comments:** Explicitly document `min_gap_minutes`, `max_daily_loss`, and `expiry_type` behavior.
- **Verification:**
  - **Paper Simulation:**
    - Place order in Paper Mode.
    - Check if TSL moves up as price rises.
    - Check if "Close All" works instantly.

### Phase 5: Interface (The Controls)
**Goal:** User control during runtime.
- **Tasks:**
  - Implement `command_processor.py`.
  - Add commands: `sl`, `pause`, `positions`.
- **Verification:**
  - Type `sl 50` -> Verify config updates.
  - Type `pause` -> Verify no new trades are taken.

---

## Verification Plan (Post-Implementation)

### component Testing

**1. Configuration System**
```bash
cd c:\Rahul\06_Nikku\Strategies\options_bot

# Test JSON loading
python -c "from config import Config; c = Config(); print('✅ Config loaded')"

# Test auto-reload
# Terminal 1: python main.py
# Terminal 2: Edit config.json
# Verify: Bot detects change within 5 seconds
```

**2. Indicator Calculations**
```bash
# Test each indicator
python -c "from indicators import calculate_ema, calculate_rsi, calculate_stochrsi; print('✅ Indicators working')"

# Test with sample data
python test_indicators.py  # Verify calculations match expected values
```

**3. Multi-Timeframe Logic**
```bash
# Test data fetching
python -c "from data_handler import DataHandler; dh = DataHandler(); print(dh.get_htf_data('NIFTY')); print(dh.get_ltf_data('NIFTY'))"
```

**4. API Connection**
```bash
# Test OpenAlgo connection
python -c "from openalgo import api; client = api(api_key='...', host='http://127.0.0.1:5000'); print(client.funds())"
```

### Phase 2: Strategy Testing (Paper Mode)

**1. Signal Generation Test**
```bash
# Run in paper trading mode
python main.py --paper-trading

# Monitor console output for:
# - HTF trend detection
# - LTF signal generation
# - Filter alignment checks
# - Entry signal triggers
```

**2. Entry Logic Validation**
- Verify initial entry signals match manual analysis
- Confirm pullback re-entry logic works correctly
- Check that all 4 filters are evaluated

**3. Exit Logic Validation**
- Test stop loss triggers (simulate losing trades)
- Test target profit exits
- Test trailing stop mechanism
- Test indicator reversal exits
- Test time-based exits (run till 3:15 PM)

### Phase 3: Runtime Testing

**1. Command Interface**
```bash
# While bot runs, test commands:
> sl 40           # Change stop loss
> toggle rsi_ltf  # Disable RSI
> positions       # View positions
> show config     # Display config
> help            # Command list
```

**2. Config Auto-Reload**
- Edit `config.json` while bot runs
- Change stop_loss_pct: 30 → 35
- Verify bot logs: "Config reloaded"
- Confirm new value in next trade

### Phase 4: Integration Testing

**1. Full Trading Cycle (Paper Mode)**
```bash
# Run for full day in paper mode
python main.py --paper-trading --date 2026-01-02

# Verify:
# - Market open detection
# - Data fetching (HTF + LTF)
# - Signal generation
# - Simulated order placement
# - Position tracking
# - Exit triggering
# - Market close handling
# - Daily summary generation
```

**2. Multiple Instruments**
- Test NIFTY and Bank Nifty simultaneously
- Verify independent signal generation
- Check position limits are enforced

**3. Error Scenarios**
- Simulated API failures
- Network disconnections
- Invalid config values
- Missing data candles

### Phase 5: Live Testing (Minimal Capital)

**1. Single Position Test**
```json
// config.json - Conservative settings
{
  "max_positions": 1,
  "lot_size": 1,
  "symbols": ["NIFTY"],
  "paper_trading": false
}
```

**2. Monitoring Checklist**
- [ ] Entry signal generated correctly
- [ ] Order placed successfully
- [ ] Position tracked in OpenAlgo
- [ ] P&L calculated accurately
- [ ] Stop loss monitored continuously
- [ ] Exit triggered appropriately
- [ ] Order closed successfully
- [ ] Logs captured all events

### Phase 6: Performance Validation

**1. Backtesting (Manual)**
- Run paper mode on historical days
- Calculate win rate, avg profit/loss
- Compare different indicator combinations

**2. Forward Testing**
- Run live with minimal capital for 5-7 days
- Track all trades in spreadsheet
- Analyze performance metrics
- Adjust parameters based on results

## Manual Verification Required

1. **Configuration Review**: Verify all parameters in `config.example.json` match your preferences
2. **API Credentials**: Confirm OpenAlgo is running and credentials are correct
3. **Paper Trading**: Run for 2-3 full days in paper mode, review all signals
4. **Indicator Addition**: Test adding a custom indicator (e.g., VWAP) to verify extensibility
5. **Command Testing**: Try all runtime commands to ensure they work as expected
6. **Risk Limits**: Verify stop loss, targets, and limits trigger correctly
7. **Log Review**: Check logs are comprehensive and readable
8. **Error Handling**: Intentionally cause errors, verify graceful handling

## Next Steps After Implementation

1. **Initial Setup**: Install dependencies, create config.json from template
2. **Configuration**: Customize parameters to match your risk tolerance
3. **Paper Testing**: Minimum 2-3 days of paper trading
4. **Review & Adjust**: Analyze paper trading results, tune parameters
5. **Small Live Test**: 1-2 days with 1 lot on single instrument
6. **Scale Gradually**: If successful, increase position size slowly
7. **Continuous Monitoring**: Watch for at least 1 week actively
8. **Optimization**: After 2 weeks, review data and optimize settings

## Open Questions for User to Confirm

Before proceeding with implementation, please confirm:

1. ✅ **Multi-timeframe**: 15min (HTF) + 5min (LTF) acceptable or want different?
2. ✅ **Indicators**: EMA + StochRSI + RSI confirmed, or want to start with fewer?
3. ✅ **Pullback Re-entry**: Confirmed enabled by default?
4. ✅ **Config Format**: JSON + Commands approach confirmed?
5. ✅ **Naming**: htf/ltf convention confirmed?
6. ⏳ **Paper Trading Duration**: 2-3 days sufficient before live or need more?
7. ⏳ **Telegram Notifications**: Required or optional?
8. ⏳ **Web Dashboard**: Want this in future or commands sufficient?
