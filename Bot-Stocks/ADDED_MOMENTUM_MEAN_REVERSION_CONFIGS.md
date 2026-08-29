# Added: Momentum & Mean Reversion Engine Configs

## Issue
Config sections for **Momentum Engine** and **Mean Reversion Engine** were missing from `config.yml`, even though the code existed in `signals.py`.

---

## What Was Added ✅

### 1. **Momentum Engine Config** (Lines 73-114)
Detects **trend continuation** opportunities using 6 components:
- RSI (40-70 buy zone)
- Volume Surge (1.5× average)
- ADX Trend Strength (>20 weak, >25 strong)
- EMA Trend Filter (200 period)
- Bollinger Bands Breakout
- Rate of Change (ROC)

```yaml
momentum:
  enabled: false                      # Set to true to enable
  min_score: 60                       # Minimum score (0-100) to trigger
  rsi_enabled: true                   # Each component is toggleable
  volume_enabled: true
  # ... etc
```

### 2. **Mean Reversion Engine Config** (Lines 116-163)
Detects **stretched price moves** likely to snap back using 6 components:
- Bollinger Bands Touch
- RSI Extremes (oversold <30, overbought >70)
- Stochastic Oscillator
- Z-Score Deviation (±2 std devs)
- Williams %R
- CCI (Commodity Channel Index)

```yaml
mean_reversion:
  enabled: false                      # Set to true to enable
  min_score: 60                       # Minimum score (0-100) to trigger
  bb_enabled: true                    # Each component is toggleable
  rsi_extreme_enabled: true
  # ... etc
```

---

## How to Enable

### **Turn on Momentum Engine:**
```yaml
momentum:
  enabled: true            # Change from false to true
```

### **Turn on Mean Reversion Engine:**
```yaml
mean_reversion:
  enabled: true            # Change from false to true
```

---

## Dashboard Integration

Both engines will appear in **Dashboard Quick Controls**:
```
[ ] UT Bot
[ ] S/R Channels
[ ] Momentum Engine    ← NEW
[ ] Mean Reversion     ← NEW
```

Toggle them ON/OFF from the dashboard!

---

## Scoring System

Each engine uses **weighted scoring** (0-100 points):
- Each component contributes points based on its weight
- Signal triggers when total score ≥ `min_score`

**Example (Momentum Buy):**
```
RSI in buy zone:        +20 points
Volume surge:           +20 points
ADX strong trend:       +15 points
Price above EMA:        +20 points
Total:                  75 points → Signal triggers! ✅ (75 >= 60)
```

---

## Configuration Tips

### **More Signals (Aggressive):**
```yaml
momentum:
  min_score: 50           # Lower threshold
```

### **Fewer Signals (Conservative):**
```yaml
momentum:
  min_score: 75           # Higher threshold
```

### **Disable Components:**
```yaml
momentum:
  roc_enabled: false      # Don't use ROC
  bb_enabled: false       # Don't use Bollinger Bands
```

---

## Files Modified

**`config.yml`** (Lines 73-163)
- Added `momentum:` section with 6 components
- Added `mean_reversion:` section with 6 components

---

## Default State

**Both engines are DISABLED by default:**
```yaml
momentum:
  enabled: false          # OFF

mean_reversion:
  enabled: false          # OFF
```

Enable them when you're ready to test!

---

## Summary

| Engine | Purpose | Components | Default |
|--------|---------|-----------|---------|
| Momentum | Trend continuation | RSI, Volume, ADX, EMA, BB, ROC | OFF |
| Mean Reversion | Snap-back trades | BB, RSI, Stoch, Z-Score, Williams, CCI | OFF |

**Ready to use whenever you enable them!** 🚀
