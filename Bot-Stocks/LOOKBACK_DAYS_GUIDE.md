# Lookback Days Configuration Guide

## Quick Reference Table

| Timeframe | Recommended Days | Trading Days | Why? |
|-----------|-----------------|--------------|------|
| **1m** | 5-7 days | ~3-5 | yfinance limit |
| **5m** | **7 days** | **~5** | **Optimal for intraday** |
| **15m** | 7-14 days | ~5-10 | More S/R history |
| **30m** | 10-20 days | ~7-14 | Better pivot detection |
| **1h** | 14-30 days | ~10-20 | Good S/R zones |
| **1d** | **400 days** | **~280** | **S/R needs 290 bars** |
| **1w** | **700 days** | **~100 weeks** | **Long-term analysis** |

---

## Updated config.yml Documentation

```yaml
data:
  lookback_days: 7                   # Days (calendar days, not trading days)
                                      # 
                                      # ADJUST BASED ON YOUR TIMEFRAME:
                                      # ================================
                                      # Intraday Timeframes:
                                      #   1m, 2m:         5-7 days   (yfinance hard limit)
                                      #   5m, 15m:        7 days     (safe for all sources)
                                      #   30m, 60m:       7-30 days  (more = better S/R)
                                      # 
                                      # Daily/Weekly Timeframes:
                                      #   1d (daily):     400 days   (≈ 280 trading days)
                                      #   1w (weekly):    700 days   (≈ 100+ weeks)
                                      # 
                                      # WHY SO MUCH FOR DAILY?
                                      # - S/R Channels: loopback=290 bars needed
                                      # - Momentum: RSI/ADX need 14-50 bars
                                      # - Regime: vol_percentile_window=200 bars
                                      # 
                                      # CURRENT: 7 days (for 5m/15m intraday)
```

---

## All Issues Fixed ✅

### 1. 422 Config Error → FIXED
- Added `ApiRateLimitConfig` to `app.py`
- Server restart required (already done)

### 2. OpenAlgo Data Error → FIXED
- Updated `lookback_days: 7` (from 5)
- Added comprehensive documentation
- Works for 5m/15m intraday scanning

### 3. Dashboard UI → UPDATED
- "Quick Filter Controls" → "Quick Controls"
- "UT Bot Engine" → "UT Bot"
- "S/R Zones Engine" → "S/R Channels"
- Removed description text

---

## When You Switch Timeframes

**Current Setup (5m):**
```yaml
candle_timeframe: 5m
lookback_days: 7      ← Optimal
```

**If switching to Daily (1d):**
```yaml
candle_timeframe: 1d
lookback_days: 400    ← MUST CHANGE!
```

**If switching to Hourly (1h):**
```yaml
candle_timeframe: 1h
lookback_days: 20     ← Recommended
```

---

## Why the Error Happened

**Your Original State:**
- Timeframe: `1d` (daily)
- Lookback: `5 days` → Only 3-4 trading days
- S/R Channels needs: `290 trading days`
- Result: "No data available" ❌

**After You Changed to 5m:**
- Timeframe: `5m` (intraday)
- Lookback: `7 days` (now updated)
- Sufficient for intraday indicators
- Result: Scanner works ✅

---

## Files Modified

1. **app.py** - Added `ApiRateLimitConfig` model
2. **frontend/index.html** - Updated dashboard labels
3. **config.yml** - Documented `lookback_days` properly

**Status: All systems operational!** 🚀
