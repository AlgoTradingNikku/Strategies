# Signal Lookback Implementation - Bot-NSE-Options

## Implementation Date: August 26, 2026

---

## 📋 Summary

Successfully implemented **Signal Lookback Logic** with **"Most-Recent-Wins" Conflict Resolution** in Bot-NSE-Options, matching the proven approach from Bot-Stocks.

---

## 🎯 What Was Implemented

### **Core Feature: Signal Lookback Window**
- Bot checks the configured number of recent completed candles for UTBot signals. Current config uses `1`, meaning **latest completed candle only**.
- Prevents missing signals due to scan timing misalignment, network latency, or rapid market moves
- Configurable via `strategy.signal_lookback_candles` parameter (range: 1-5 recommended). Legacy `options.signal_lookback_candles` is still accepted as a fallback.

### **Intelligent Conflict Resolution**
- When both BUY and SELL signals appear within the lookback window, only the **most recent signal** is kept
- Prevents contradictory signals from rapid market reversals
- Matches TradingView's discrete label behavior (latest tag = active signal)

### **Smart Incomplete Candle Detection**
- Time-based detection using exchange timezone (Asia/Kolkata for NSE)
- Automatically excludes incomplete/forming candles when `signal_on_closed_bar: true`
- Fail-safe design: defaults to treating candles as closed if detection fails

---

## 📝 Files Modified

### **1. config.yml**
**Added under UTBot strategy settings:**
```yaml
strategy:
  signal_lookback_candles: 1        # Latest completed candle only
```

**Updated:**
```yaml
strategy:
  signal_on_closed_bar: true        # Renamed from signal_on_running_bar (standardized naming)
```

### **2. signals.py**
- Added `_parse_timeframe_seconds()` helper function (lines 25-43)
- Added `_is_last_candle_incomplete()` helper function (lines 46-94)
- Modified `evaluate_composite_signals()` with lookback logic (lines 306-381)
- Implemented "most-recent-wins" conflict resolution
- Added backward compatibility for old config naming

### **3. scanner.py**
- Enhanced startup logging with lookback window info (lines 201-218)
- Updated bar label logic for new config naming (lines 240-247)

---

## 🔄 Backward Compatibility

✅ Old config variable `signal_on_running_bar` still works  
✅ New config variable `signal_on_closed_bar` takes precedence  
✅ Default behavior unchanged for existing users  
✅ Lookback defaults to 2 if not specified, but current config intentionally uses 1 for latest-candle-only signals

---

## 🎛️ Configuration Reference

### **New Parameters**

```yaml
strategy:
  signal_lookback_candles: 1        # 1-5 supported
                                    # 1 = most strict (current bar only)
                                    # 2 = balanced (default, 1-candle buffer)
                                    # 3-5 = more lenient (catches delayed signals)

  signal_on_closed_bar: true        # Recommended for stable signals
```

### **Recommended Settings by Timeframe**

| Timeframe | Lookback | Rationale |
|-----------|----------|-----------|
| 1m | 1-2 | Fast markets, keep signals fresh |
| 3m | 2 | Balanced for quick moves |
| 5m | 1 | Strict/latest completed candle only; use 2 if scan timing needs a buffer |
| 15m | 2-3 | More buffer for slower scans |
| 1h+ | 3-5 | Longer timeframes need wider window |

---

## 🧪 Testing Recommendations

### **Before Going Live:**

1. **Paper Trading Test** (1-2 days)
   - Monitor signal quality
   - Check for false positives/negatives
   - Verify lookback window behavior in logs

2. **Log Analysis**
   - Look for "most-recent-wins" conflict messages
   - Verify incomplete candle detection during market hours
   - Check scan startup shows correct settings

### **Debug Commands:**

```bash
# View scan startup info
tail -f Bot-NSE-Options/scanner.log | grep "Starting Options Scan"

# Monitor conflict resolution
tail -f Bot-NSE-Options/scanner.log | grep "Lookback window:"

# Check incomplete candle detection
tail -f Bot-NSE-Options/scanner.log | grep "Dropped incomplete candle"
```
