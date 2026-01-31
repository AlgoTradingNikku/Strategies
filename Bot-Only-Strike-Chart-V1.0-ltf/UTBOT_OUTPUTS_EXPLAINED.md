# UTBot Indicator - Complete Output Explained

## Date: January 31, 2026
## Your Question: "Using UTBot, we get two things - 1. Signals, 2. Trend (up or down). Is that correct?"

---

## ✅ **SHORT ANSWER: Yes, BUT with important nuances!**

UTBot gives you **TWO primary outputs**, but each has **multiple states**:

1. **TREND** → Current market direction (3 states)
2. **SIGNAL** → Action to take (5 states)

---

## 📊 **DETAILED BREAKDOWN**

### **Output 1: TREND (Market Direction)**

```python
utbot_result.trend
```

| Value | Meaning | Description |
|-------|---------|-------------|
| **1** | **BULLISH** | Price is above the UTBot trailing stop |
| **-1** | **BEARISH** | Price is below the UTBot trailing stop |
| **0** | **NEUTRAL** | (Rarely used - initialization state) |

**Think of it as:** "Which side of the trail is price on?"

---

### **Output 2: SIGNAL (Entry/Exit Action)**

```python
utbot_result.signal
```

| Value | Meaning | When It Fires |
|-------|---------|--------------|
| **1** | **Fresh BUY** | Price **just crossed ABOVE** the trail (crossover) |
| **-1** | **Fresh SELL** | Price **just crossed BELOW** the trail (crossover) |
| **2** | **Pullback BUY** | Still bullish, but red candle bounced to green (continuation) |
| **-2** | **Pullback SELL** | Still bearish, but green candle turned red (continuation) |
| **0** | **No Signal** | Trend continues, no new action needed |

**Think of it as:** "Should I do something RIGHT NOW?"

---

## 🔍 **KEY DIFFERENCE: TREND vs SIGNAL**

### **TREND = STATE (Persistent)**
- **Always has a value** (bullish or bearish)
- **Persists across candles** until crossover
- **Tells you the current bias**

```python
# Example sequence:
Candle 1: trend = 1  (bullish)
Candle 2: trend = 1  (still bullish)
Candle 3: trend = 1  (still bullish)
Candle 4: trend = -1 (now bearish - changed!)
```

---

### **SIGNAL = EVENT (Momentary)**
- **Usually 0** (no action)
- **Fires only once** when something important happens
- **Tells you WHEN to act**

```python
# Example sequence:
Candle 1: signal = 1   (FRESH BUY - price just crossed up)
Candle 2: signal = 0   (no new action - trend continues)
Candle 3: signal = 0   (no new action - trend continues)
Candle 4: signal = -1  (FRESH SELL - price just crossed down)
```

---

## 🎯 **HOW YOUR BOT USES THEM**

### **1. For Entry Detection:**
```python
# In engine.py, line 821
if utbot_result.signal == 1:  # Fresh BUY signal
    trigger_active = True
    # Bot checks filters and enters if valid
```

**Uses:** `signal` (looking for fresh crossover = 1)

---

### **2. For Exit Detection:**
```python
# In engine.py, line 899
if utbot_result.signal == -1:  # Fresh SELL signal
    # Exit position if use_utbot_sell = True
```

**Uses:** `signal` (looking for fresh crossover = -1)

---

### **3. For Re-Entry Validation:**
```python
# In engine.py, line 885
trend_ok = (utbot_result.trend == 1)  # Still bullish?

if trend_ok:
    # Check for pullback re-entry opportunity
```

**Uses:** `trend` (checking overall direction)

---

### **4. For Logging/Display:**
```python
# In engine.py, line 680-684
if symbol in self._last_utbot_state:
    trend = self._last_utbot_state[symbol]
    if trend == 1: state_emoji = "[BULL]"
    elif trend == -1: state_emoji = "[BEAR]"
```

**Uses:** `trend` (showing market bias)

---

## 📈 **REAL-WORLD EXAMPLE**

Let's say NIFTY option price is moving:

### **Scenario: Price Rallying**

```
Time   Price   Trail   TREND   SIGNAL   Explanation
-------------------------------------------------------------
10:00  100     105      -1       0      Below trail, bearish, no signal
10:01  103     105      -1       0      Still below trail
10:02  106     105      +1       1      ✅ CROSSED ABOVE - FRESH BUY! ✅
10:03  108     106      +1       0      Above trail, bullish, no new signal
10:04  107     106      +1       0      Small pullback, still above trail
10:05  109     107      +1       0      Rally continues
10:06  105     107      -1      -1      ✅ CROSSED BELOW - FRESH SELL! ✅
```

**Key Insights:**
- **TREND** changed only twice (at crossovers)
- **SIGNAL** fired only at crossover moments (1 and -1)
- Most candles have `signal = 0` (no action needed)

---

## 🧪 **TESTING WHAT YOU GET**

You can verify this yourself:

```python
# Your bot calculates UTBot like this (engine.py, line 798 or 801)
utbot_result = self.indicators["option_utbot"].calculate(df_opt, use_ha=use_ha)

# Then you can access:
print(f"Trend: {utbot_result.trend}")      # 1 or -1
print(f"Signal: {utbot_result.signal}")    # 0, 1, -1, 2, or -2
print(f"Strength: {utbot_result.strength}") # Always 1.0
print(f"Trail Level: {utbot_result.metadata['stop_level']}")
print(f"ATR: {utbot_result.metadata['atr']}")
```

---

## 🎓 **BONUS: PULLBACK SIGNALS (2 and -2)**

Your bot also uses **pullback signals** for re-entry:

### **Pullback Buy (signal = 2)**
```
Condition:
- TREND is still +1 (bullish)
- Previous candle was RED (bearish candle)
- Current candle is GREEN (bullish candle)

Interpretation: "Temporary dip in bullish trend - re-entry opportunity"
```

### **Pullback Sell (signal = -2)**
```
Condition:
- TREND is still -1 (bearish)
- Previous candle was GREEN (bullish candle)
- Current candle is RED (bearish candle)

Interpretation: "Temporary bounce in bearish trend - re-short opportunity"
```

**Note:** Your bot uses re-entry logic (lines 1217-1288) to act on pullbacks.

---

## 📋 **COMPLETE UTBot OUTPUT STRUCTURE**

```python
IndicatorSignal(
    trend=1,           # ← Output 1: Market direction (1 or -1)
    signal=1,          # ← Output 2: Action signal (0, ±1, ±2)
    strength=1.0,      # Always 1.0 (UTBot is binary - no confidence score)
    metadata={
        'stop_level': 210.5,      # Current trailing stop level
        'atr': 5.2,               # Average True Range value
        'trend_series': [...],    # Full history of trend values
        'signal_series': [...],   # Full history of signals
        'trail_series': [...]     # Full history of trail levels
    }
)
```

---

## 🏆 **FINAL ANSWER TO YOUR QUESTION**

### **"Using UTBot, we get two things - 1. Signals, 2. Trend. Is that correct?"**

✅ **YES, that's correct!** But to be precise:

1. **TREND** = Current market bias (bullish +1 or bearish -1)
   - **Persistent state** that changes only on crossovers
   
2. **SIGNAL** = Entry/exit trigger (5 possible values)
   - **Momentary event** that fires at specific moments
   - **Most important:** `1` (fresh buy) and `-1` (fresh sell)

### **How Your Bot Uses Them:**
- **SIGNAL = 1** → Triggers entry scan (checks filters)
- **TREND = 1** → Validates re-entry attempts (must be bullish)
- **SIGNAL = -1** → Triggers exit (if `use_utbot_sell: True`)

**Both are equally important** - SIGNAL tells you WHEN, TREND tells you WHAT DIRECTION.

---

*Explained: January 31, 2026*  
*Reference: `indicators/utbot.py` and `core/engine.py`*
