# Testing Checklist: OpenAlgo Logging Fix

## Pre-Test Verification

### 1. Check Code Changes
- [x] `app.py` lines 27-29: OpenAlgo logging suppression added
- [x] `scanner.py` lines 89-91: OpenAlgo logging suppression added
- [x] Both files set "data" and "openalgo" loggers to WARNING level

### 2. Verify Config
```bash
cd C:\Rahul\Trade\Strategies\Bot-Stocks
python -c "import yaml; cfg = yaml.safe_load(open('config.yml')); print('scan_interval_seconds:', cfg.get('scan_interval_seconds'))"
```
Expected output: `scan_interval_seconds: 300`

## Test Steps

### Test 1: Restart Bot & Check Initial Logs

**Action:**
```bash
cd C:\Rahul\Trade\Strategies\Bot-Stocks
python app.py
```

**Expected Result:**
- ✅ Bot starts successfully
- ✅ See configuration summary in console
- ✅ NO "Debug - API Response Status: True" spam
- ✅ Clean startup logs

**Fail Criteria:**
- ❌ Multiple "Debug - API Response Status" lines on startup

---

### Test 2: Manual Scan

**Action:**
1. Open dashboard: http://localhost:9000
2. Click "Run Scanner" button
3. Observe console logs

**Expected Result:**
- ✅ Scan runs successfully
- ✅ See scan start message
- ✅ See scan complete summary (0 BUY, 0 SELL or actual signals)
- ✅ Total log lines: 2-10 (depends on signals found)
- ✅ NO "Debug - API Response Status: True" spam

**Fail Criteria:**
- ❌ 20-50 "Debug - API Response Status" lines per scan
- ❌ Console flooded with logs

---

### Test 3: Auto-Refresh (5 Min Interval)

**Action:**
1. Dashboard → Auto-refresh: ON
2. Interval dropdown: Select "5 min"
3. Wait and observe console
4. Note timestamp of first scan
5. Wait 5 minutes
6. Note timestamp of second scan

**Expected Result:**
- ✅ First scan runs immediately
- ✅ Clean logs (2-10 lines)
- ✅ Second scan runs exactly 5 minutes later
- ✅ NOT after 1 second, NOT continuous
- ✅ Each scan shows only relevant info, no API spam

**Fail Criteria:**
- ❌ Scans appear every few seconds
- ❌ "Debug - API Response Status" spam returns
- ❌ Interval not respected (not 5 minutes)

---

### Test 4: Auto-Refresh (1 Min Interval)

**Action:**
1. Change interval dropdown to "1 min"
2. Observe console for 3 minutes
3. Count scans

**Expected Result:**
- ✅ Scans appear every ~60 seconds
- ✅ Total scans in 3 minutes: 3-4
- ✅ Clean logs each time
- ✅ Consistent 1-minute gaps

**Fail Criteria:**
- ❌ More than 5 scans in 3 minutes
- ❌ Logs appear continuously
- ❌ "Debug - API Response Status" spam

---

### Test 5: Check Logs File

**Action:**
```bash
cd C:\Rahul\Trade\Strategies\Bot-Stocks
Get-Content logs\bot-stocks.log -Tail 100
```

**Expected Result:**
- ✅ Log file still contains scanner activity
- ✅ NO "Debug - API Response Status" in recent logs
- ✅ Clean, readable log format

**Fail Criteria:**
- ❌ Log file full of API debug messages

---

## Regression Tests

### Test 6: Verify Functionality Not Broken

**Action:**
1. Manual scan
2. Check if signals appear (if market is open)
3. Try placing a test order (manual mode)
4. Check positions tab
5. Check config tab

**Expected Result:**
- ✅ All features work normally
- ✅ Signals detected (if market conditions met)
- ✅ Orders can be placed
- ✅ Positions visible
- ✅ Config loads correctly

**Fail Criteria:**
- ❌ Any feature broken
- ❌ API calls failing
- ❌ Data not loading

---

### Test 7: Error Visibility

**Action:**
1. Temporarily misconfigure OpenAlgo (e.g., wrong API key)
2. Try manual scan
3. Check console for error

**Expected Result:**
- ✅ Error message IS visible
- ✅ ERROR or WARNING level logs still appear
- ✅ User can see what went wrong

**Fail Criteria:**
- ❌ Errors completely suppressed
- ❌ Silent failures

**Restore:** Fix OpenAlgo config after test

---

## Performance Verification

### Test 8: Console Performance

**Before Fix:**
- 50 log lines per scan
- Every 5 minutes = 10 lines/minute
- In 1 hour = 600 lines

**After Fix:**
- 5 log lines per scan
- Every 5 minutes = 1 line/minute
- In 1 hour = 60 lines

**Measure:**
1. Run with auto-refresh for 15 minutes
2. Count total console log lines
3. Expected: ~15 lines (3 scans × 5 lines each)
4. Not expected: 150+ lines

---

## Sign-Off

**Tester:** _______________  
**Date:** _______________  
**Result:** ☐ Pass  ☐ Fail  

**Notes:**
_______________________________________________________________________
_______________________________________________________________________
_______________________________________________________________________

---

## Rollback Plan (If Tests Fail)

If this fix causes issues:

1. **Revert app.py:**
   ```bash
   git checkout app.py
   ```

2. **Revert scanner.py:**
   ```bash
   git checkout scanner.py
   ```

3. **Restart bot:**
   ```bash
   python app.py
   ```

Will return to verbose logging, but all functionality intact.

---

**Files:**
- `FIX_OPENALGO_VERBOSE_LOGGING.md` - Detailed explanation
- `SUMMARY_AUTOREFRESH_FIX.md` - Executive summary
- `TESTING_CHECKLIST.md` - This file
