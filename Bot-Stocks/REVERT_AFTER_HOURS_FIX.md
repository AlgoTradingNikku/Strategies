# Revert: After-Hours Date Adjustment (Keep Code Simple)

## Why Reverted?

The "Friday night after-hours" fix was **unnecessary complexity**. The original issue was:
- **Root cause:** OpenAlgo connection problem with mstock broker backend (not date range)
- **Not needed:** After-market hours date adjustment logic
- **Better approach:** Keep code simple, only handle weekends

---

## What Was Reverted ✅

### 1. Removed After-Hours Detection Logic
**Deleted from scanner.py (lines 306-319):**
```python
# REMOVED:
is_intraday = timeframe in ("1m", "2m", "3m", "5m", ...)
market_close_hour = 15

if data_source == "openalgo" and is_intraday and end_dt.hour >= market_close_hour:
    if end_dt.weekday() < 5:
        end_dt = end_dt - timedelta(days=1)  # Use previous day
```

**Why:** Adds complexity for a problem that doesn't exist when OpenAlgo is properly connected.

---

### 2. Removed Debug Logging
**Deleted from scanner.py (lines 487-491):**
```python
# REMOVED:
log.debug(
    "[%s] OpenAlgo request: exchange=%s, interval=%s, start=%s, end=%s, lookback=%d days",
    symbol, exchange, oa_interval, start_str, end_str, lookback_days
)
```

**Why:** Extra logging noise not needed for normal operation.

---

### 3. Reverted Log Level
**config.yml line 345:**
```yaml
# Changed back:
log_level: "INFO"  # Was: "DEBUG"
```

**Why:** DEBUG mode is verbose and slows down scanning slightly.

---

### 4. Deleted Temporary Documentation
**Removed files:**
- `FIX_FRIDAY_NIGHT_OPENALGO.md`
- `FIX_OPENALGO_WEEKEND_DATE_ISSUE.md`
- `test_date_fix.py`

**Why:** No longer relevant after determining root cause was OpenAlgo connection.

---

## What Was Kept ✅

### Weekend Date Adjustment (Still Useful!)
**Kept in scanner.py (lines 306-314):**
```python
# KEPT:
if end_dt.weekday() >= 5:  # Saturday/Sunday
    days_since_friday = end_dt.weekday() - 4
    end_dt = end_dt - timedelta(days=days_since_friday)
    log.debug("[%s] Weekend detected; adjusted end_date to last Friday: %s", ...)
```

**Why:** Still valuable - prevents requesting data for Saturday/Sunday when markets are closed.

---

## Final Code State

### scanner.py Date Logic (Simplified):
```python
# Calculate date range, adjusting end_date for weekends/non-trading days
end_dt = datetime.now()

# If today is Saturday/Sunday, roll back to last Friday
if end_dt.weekday() >= 5:  # 5=Saturday, 6=Sunday
    days_since_friday = end_dt.weekday() - 4
    end_dt = end_dt - timedelta(days=days_since_friday)
    log.debug(
        "[%s] Weekend detected; adjusted end_date to last Friday: %s",
        symbol, end_dt.strftime("%Y-%m-%d")
    )

start_dt = end_dt - timedelta(days=lookback_days)
start_str = start_dt.strftime("%Y-%m-%d")
end_str   = end_dt.strftime("%Y-%m-%d")
```

**Result:** Clean, simple logic that handles weekends but doesn't overthink weekday behavior.

---

## Benefits of Simplification

✅ **Less code complexity** - easier to maintain  
✅ **Faster scanning** - no unnecessary time checks  
✅ **Fewer edge cases** - weekend handling is sufficient  
✅ **Cleaner logs** - INFO level, no debug noise  
✅ **Better performance** - no extra date calculations on weekdays  

---

## Lessons Learned

1. **Diagnose first, code later** - The after-hours issue was actually an OpenAlgo connection problem
2. **Keep it simple** - Weekend check is enough; weekday logic works fine as-is
3. **Avoid premature optimization** - Don't add complexity for theoretical problems
4. **Trust the broker** - If OpenAlgo/broker is connected properly, date ranges work correctly

---

## Summary

| Feature | Before | After | Reason |
|---------|--------|-------|--------|
| After-hours check | ✅ Active | ❌ Removed | Unnecessary complexity |
| Weekend check | ✅ Active | ✅ Kept | Still useful |
| Debug logging | ✅ Enabled | ❌ Removed | Too verbose |
| Log level | DEBUG | INFO | Normal operation |

**Result:** Scanner is back to simple, clean code that works correctly with OpenAlgo! 🎯
