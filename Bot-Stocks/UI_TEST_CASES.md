# UI Fix Verification - Visual Test Cases

## Test Case 1: Column Structure
**Expected Result**: 7 columns in both BUY and SELL tables
- Symbol | Close | Quality | Action | Stop Loss | Target | R:R

**How to Verify**:
1. Open dashboard in browser
2. Inspect table header row
3. Count columns - should be exactly 7 (no empty column)

## Test Case 2: AI Recommendation Display

### Scenario A: Strong Buy Signal
**Backend Data**:
```json
{
  "symbol": "TCS",
  "grade": "A",
  "ai_recommendation": "STRONG BUY",
  "ai_score": 92,
  "ai_reasoning": "Excellent technical setup"
}
```

**Expected Display**:
- Quality Column: `A` (green) + `🚀 Strong Buy` (green)
- Tooltip: "AI Recommendation: STRONG BUY (92/100) — Excellent technical setup"

### Scenario B: Avoid Signal
**Backend Data**:
```json
{
  "symbol": "FEDERALBNK",
  "grade": "B",
  "ai_recommendation": "AVOID",
  "ai_score": 35,
  "ai_reasoning": "Despite strong ADX, setup score is low..."
}
```

**Expected Display**:
- Quality Column: `B` (blue) + `🛑 Avoid` (red)
- Tooltip: "AI Recommendation: AVOID (35/100) — Despite strong ADX, setup score is low..."

### Scenario C: Grade D (No AI)
**Backend Data**:
```json
{
  "symbol": "RPOWER",
  "grade": "D",
  "ai_recommendation": null
}
```

**Expected Display**:
- Quality Column: `D` (red) + `🤖 N/A` (gray)
- Tooltip: "AI analysis only runs on Grade A/B/C signals (top 5 per scan)"

### Scenario D: Neutral Recommendation
**Backend Data**:
```json
{
  "symbol": "INFY",
  "grade": "B",
  "ai_recommendation": "NEUTRAL",
  "ai_score": 60
}
```

**Expected Display**:
- Quality Column: `B` (blue) + `⏸️ Neutral` (gray)
- Tooltip: "AI Recommendation: NEUTRAL (60/100) — ..."

## Test Case 3: All AI Recommendation Types

| Backend Value | Badge Text | Badge Emoji | Color | Expected Scenario |
|--------------|------------|-------------|-------|-------------------|
| `STRONG BUY` | Strong Buy | 🚀 | Green (#10b981) | High conviction bullish |
| `BUY` | Buy | ✅ | Light Green (#22c55e) | Standard bullish setup |
| `NEUTRAL` | Neutral | ⏸️ | Gray (#94a3b8) | Wait for clearer signal |
| `AVOID` | Avoid | 🛑 | Red (#ef4444) | Poor setup, skip |
| `STRONG SELL` | Strong Sell | ⛔ | Dark Red (#dc2626) | High conviction bearish |
| `SELL` | Sell | 📉 | Light Red (#f87171) | Standard bearish setup |

## Test Case 4: Column Alignment

### Before Fix (BROKEN):
```
| Symbol       | Close  | WIN RATE | [EMPTY] | Quality | Action |
| FEDERALBNK   | 353.35 | —        | D       | B Hold  | [Buy]  |
```
❌ Problems:
- WIN RATE column still visible
- Empty column showing "D" grade
- Grade "B" appearing in CLOSE column
- "Hold" badge doesn't match tooltip

### After Fix (CORRECT):
```
| Symbol       | Close  | Quality      | Action | Stop Loss | Target | R:R  |
| FEDERALBNK   | 353.35 | B 🛑 Avoid   | [Buy]  | 348.50    | 360.00 | 2.1  |
```
✅ Fixed:
- WIN RATE column removed
- Empty column removed
- Grade + AI badges consolidated in Quality column
- Badge text matches AI recommendation from backend

## Test Case 5: Browser Console Check

**Steps**:
1. Open browser DevTools (F12)
2. Navigate to Console tab
3. Refresh dashboard page
4. Run scanner

**Expected Result**: No JavaScript errors related to:
- `recMap` undefined keys
- Column count mismatch
- Missing table cells

## Test Case 6: Responsive Design

**Test on**:
- Desktop (1920x1080)
- Laptop (1366x768)
- Tablet (768x1024)
- Mobile (375x667)

**Expected**: Quality column badges should wrap gracefully without breaking layout

## Test Case 7: Tooltip Accuracy

**Steps**:
1. Hover over AI recommendation badge
2. Read tooltip text
3. Verify tooltip matches badge

**Example**:
- Badge shows: `🛑 Avoid`
- Tooltip should say: "AI Recommendation: AVOID (35/100) — [reasoning text]"
- ❌ NOT: "AI Recommendation: Hold" (old bug)

## Manual Testing Commands

### Start Server
```bash
cd c:\Rahul\Trade\Strategies\Bot-Stocks
uv run app.py
```

### Open Dashboard
```
http://127.0.0.1:8000
```

### Run Scanner
1. Click "Run Scanner" button
2. Wait for results
3. Inspect BUY and SELL signal tables

### Check Browser Console
Press F12 → Console tab → Look for errors

## Known Good Data (For Testing)

If you need to manually verify, the scanner should return data like:
```json
{
  "buy_signals": [
    {
      "symbol": "FEDERALBNK",
      "close": 353.35,
      "grade": "B",
      "grade_score": 65.0,
      "ai_recommendation": "AVOID",
      "ai_score": 35,
      "ai_reasoning": "Despite strong ADX of 42.9...",
      "stop_loss": 348.50,
      "target": 360.00,
      "risk_reward": 2.1
    }
  ]
}
```

This should render as:
- Symbol: **FEDERALBNK**
- Close: 353.35
- Quality: `B` (blue badge) + `🛑 Avoid` (red badge)
- Action: [Buy button with Qty input]
- Stop Loss: 348.50
- Target: 360.00
- R:R: 2.1

## Regression Checks

Ensure previous fixes still work:
- ✅ Engine labels (UT/SR/UT+SR) NOT displayed
- ✅ WIN RATE column NOT visible
- ✅ Grade badges color-coded correctly (A=green, B=blue, C=amber, D=red)
- ✅ 🤖 N/A indicator for Grade D signals
- ✅ Warning badges (GATE, EXPOSURE) in Symbol column, not Quality column
