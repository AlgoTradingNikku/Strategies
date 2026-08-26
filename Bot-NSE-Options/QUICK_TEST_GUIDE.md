# 🎯 Quick Test Guide - Time Period Selector

## ✅ What You'll See

### Before:
```
[Order Mode: Manual|Auto]  [↻ Auto-refresh: ON]  [▶ Run Scanner]
```

### After (NEW!):
```
[Order Mode: Manual|Auto]  [↻ Auto-refresh: ON]  [▼ 5 min]  [▶ Run Scanner]
                                                    ^^^^^^^^
                                                    NEW DROPDOWN!
```

---

## 🚀 How to Test

### Step 1: Start the Dashboard
```bash
cd C:\Rahul\Trade\Strategies\Bot-NSE-Options
python app.py
```

### Step 2: Open Browser
Navigate to: **http://localhost:9000**

### Step 3: Look for the Dropdown
You should see a dropdown between "Auto-refresh" button and "Run Scanner" button that says **"5 min"**

### Step 4: Test Functionality
1. **Click the dropdown** → Should show: 1 min, 2 min, 3 min, 5 min, 10 min, 15 min, 30 min
2. **Select "3 min"** → Auto-refresh will now run every 3 minutes (if ON)
3. **Open browser console** (F12) → Should see: `✅ Auto-refresh interval set to 3 min`
4. **Refresh page** (F5) → Dropdown should still show "3 min"

### Step 5: Verify Config Persistence
```bash
cat config.yml | grep scan_interval_seconds
```
Should show: `scan_interval_seconds: 180` (if you selected 3 min)

---

## 🎨 What It Looks Like

The dropdown matches your screenshot from Bot-Stocks:
- **Dark theme** (matches existing UI)
- **Same height** as buttons (36px)
- **Rounded corners** (8px)
- **Same position** as in Bot-Stocks

---

## ✅ Expected Behavior

| Action | Expected Result |
|--------|----------------|
| Page loads | Dropdown shows config value (default: 5 min) |
| Change to "1 min" | Auto-refresh runs every 1 minute (if ON) |
| Change to "30 min" | Auto-refresh runs every 30 minutes (if ON) |
| Auto-refresh is OFF | Dropdown still works (sets interval for next ON) |
| Refresh browser | Dropdown remembers last selection |
| Check config.yml | Shows `scan_interval_seconds: <value>` |

---

## 🐛 If Something's Wrong

### Dropdown Not Visible?
- Hard refresh: **Ctrl + Shift + R** (clears browser cache)
- Check browser console for errors (F12)

### Dropdown Doesn't Change Interval?
- Check browser console for error messages
- Verify `/api/config` endpoint is working: `curl http://localhost:9000/api/config`

### Config Not Saving?
- Check `config.yml` has write permissions
- Check app logs for errors

---

## 📊 Recommended Intervals

| Trading Style | Recommended Interval | Reason |
|---------------|---------------------|--------|
| **Day Trader** | 1-2 min | Fast signal updates |
| **Swing Trader** | 5 min (default) | Balanced scanning |
| **Position Monitor** | 15-30 min | Low frequency checks |
| **Testing/Development** | 1 min | Quick feedback loop |

---

## ✅ Success!

If you can:
1. ✅ See the dropdown
2. ✅ Change the interval
3. ✅ See console confirmation
4. ✅ Config.yml updates
5. ✅ Interval persists after refresh

**Then everything is working perfectly!** 🎉

---

**Next:** Start trading with your preferred scan interval!
