# 🎛️ Quick Reference: Default Toggle States

## Summary
**All sub-toggles are now OFF by default** for cleaner onboarding.

---

## Toggle States After Update

### 🛡️ Risk Guardrails
```
❌ Market Hours Check (OFF)
❌ Daily Loss Limit (OFF)
❌ Consecutive Loss Breaker (OFF)
```

### 🎯 Signal Quality
```
❌ ATR Filter (OFF)
❌ ADX Filter (OFF)
❌ Spread Filter (OFF)
```

### 💰 Position Sizing
```
❌ Dynamic Position Sizing (OFF)
❌ Grade Multiplier (OFF)
```

### 🚀 Alpha Enhancers
```
❌ Alpha Master Switch (OFF)
  ├─ VIX Regime (OFF)
  ├─ Session Weighting (OFF)
  ├─ Volume Profile (OFF)
  ├─ Greeks Filter (OFF)
  └─ Strict MTF (OFF)
```

---

## 🚦 Recommended Starter Config

For safe trading, enable at least these:

```yaml
# Essential Safety
✅ market_hours_check: true      # Line 83
✅ daily_loss_limit: true        # Line 234
✅ spread_filter_enabled: true   # Line 270
```

**How to enable:**
1. Open dashboard: http://localhost:9000
2. Click toggles in "Quick Filters" section
3. Settings save automatically

**Or edit config.yml directly:**
```bash
cd Bot-NSE-Options
nano config.yml  # or your preferred editor
```

---

## 📋 Total Changes: 13 Toggles

| Category | Toggles Changed |
|----------|----------------|
| Risk Guardrails | 3 |
| Signal Quality | 3 |
| Position Sizing | 2 |
| Alpha Enhancers | 5 |
| **Total** | **13** |

---

## ⚡ Quick Test

After restart, verify all OFF:
```bash
cd Bot-NSE-Options
python app.py
# Dashboard should show all toggles in OFF position
```

---

**Updated:** August 26, 2026  
**Status:** ✅ Complete
