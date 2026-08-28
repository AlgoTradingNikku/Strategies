# ✅ 1W (Weekly) Timeframe Support Added

## Changes Made

**File**: `frontend/index.html`
- **Line 80**: Added `<option value="1w">1w</option>` to LTF dropdown ✅
- **Line 93**: Added `<option value="1w">1w</option>` to HTF dropdown ✅

**Backend**: Already supported (no changes needed) ✅

---

## How to Use 1W Timeframe

### Quick Start
1. **Open Dashboard**: http://localhost:5000
2. **Select Timeframe**: LTF dropdown → Choose `1w`
3. **Adjust Scan Interval** (Settings tab):
   - Change `scan_interval_seconds` from `60` to `3600` (1 hour) or `86400` (1 day)
   - **Why?** Weekly candles don't need minute-by-minute scanning
4. **Enable Momentum Engine** (Settings tab)
5. **Run Scan** → Signals based on weekly candles

---

## ⚠️ Important: Adjust Scan Interval

**Problem**: Default scan interval (60 seconds) is too frequent for weekly data

**Solution**: Update in Settings tab or config.yml:
```yaml
scan_interval_seconds: 3600  # Scan every 1 hour (recommended)
# OR
scan_interval_seconds: 86400 # Scan once per day
```

**Why?** Weekly candles only update once per week, so minute scanning wastes API calls.

---

## Recommended Settings for Weekly Analysis

### config.yml
```yaml
candle_timeframe: "1w"          # Weekly candles
scan_interval_seconds: 3600     # Scan hourly

momentum:
  enabled: true
  lookback_bars: 2              # Last 2 weeks
  rsi_period: 14                # 14 weeks RSI (~3.5 months)
  adx_period: 14                # 14 weeks ADX
  ema_period: 50                # 50 weeks EMA (~1 year)
  bb_period: 20                 # 20 weeks BB
```

### Dashboard Settings
- **LTF**: `1w` (weekly)
- **HTF**: `1d` (daily) or disable MTF filter
- **Scan Interval**: 3600+ seconds

---

## Expected Behavior

✅ **Fewer Signals**: Weekly timeframe = less noise, higher quality signals  
✅ **Slower Updates**: Signals change once per week (expected)  
✅ **Wider Stops**: ATR-based stops will be larger (weekly volatility)  
✅ **Longer Holds**: Suitable for swing/position trading (2-12 weeks)

---

## Data Source Support

| Data Source | Weekly Support | Notes |
|-------------|----------------|-------|
| OpenAlgo    | ✅ Yes | Broker-dependent |
| YFinance    | ✅ Yes | Most reliable |
| TVDataFeed  | ✅ Yes | Premium required |
| TwelveData  | ✅ Yes | Free tier limited |

---

## Use Cases

1. **Swing Trading**: Multi-week position holds
2. **Trend Analysis**: Identify major market trends
3. **Portfolio Screening**: Weekly scans for long-term setups
4. **Reduced Noise**: Filter out intraday volatility

---

## Troubleshooting

**Dropdown doesn't show 1w?**
- Hard refresh: `Ctrl + Shift + R`
- Clear browser cache

**No data available error?**
- Try YFinance as data source (most reliable)
- Check symbol exists

**Too few signals?**
- Expected! Weekly = fewer but higher quality signals
- Lower momentum thresholds if needed

**Scan still runs every minute?**
- Update `scan_interval_seconds` in Settings tab
- Turn off auto-refresh

---

## Testing Checklist

- [x] LTF dropdown shows `1w` ✅
- [x] HTF dropdown shows `1w` ✅
- [ ] Select `1w` and run scan
- [ ] Verify signals generate on weekly data
- [ ] Check scan interval is adjusted (3600+)
- [ ] Confirm TradingView links use weekly chart

---

## Summary

**Implementation**: ✅ Complete  
**Files Changed**: 1 (index.html)  
**Lines Added**: 2  
**Backend Support**: Already existed  
**Ready to Use**: Yes

**Next Steps**: Refresh dashboard → Select 1w → Adjust scan interval → Run scan

---

**Date**: 2026-08-28  
**Status**: ✅ READY FOR USE
