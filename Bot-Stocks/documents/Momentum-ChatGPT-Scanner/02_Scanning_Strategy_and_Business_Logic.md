# 02 — Scanning Strategy and Business Logic
## Momentum-ChatGPT Strategy

This document defines the actual strategy rules. The host application is `Bot-Stocks`; shared infrastructure should be reused as specified in document 01.

---

# 1. Strategy Objective

Find stocks with strong, persistent, benchmark-relative upward momentum, preferably in strong sectors and supportive market regimes, where an actionable technical setup exists and the trade has acceptable risk/reward.

The strategy is initially long-only.

It is NOT a simple "highest return" scanner.

---

# 2. Universe

Primary:

```text
NIFTY 200
```

Optional:

```text
NIFTY Next 50
NIFTY 250
NIFTY 500
NIFTY200 Momentum 30
```

NIFTY200 Momentum 30 membership is contextual only and MUST NOT automatically create a BUY.

Historical scans MUST use historical constituents.

---

# 3. Data Sufficiency

Require at least:

`300 trading sessions`

for the primary strategy.

If insufficient history exists:

```text
status = REJECTED
reason = INSUFFICIENT_HISTORY
```

Do not fill missing history with fabricated values.

---

# 4. Liquidity Filters

Recommended defaults:

```text
20D average turnover >= ₹25 crore
close >= ₹100
```

Preferred turnover:

```text
>= ₹50 crore
```

Below the hard threshold → reject.

Reason: the portfolio is ₹10 lakh, so the strategy does not need thin securities and should reduce execution/slippage risk.

---

# 5. Market Regime

Use NIFTY 50:

```text
close
EMA20
EMA50
EMA200
EMA20 slope
EMA50 slope
RSI14
Return20
Return60
```

Use NIFTY 200 breadth:

```text
breadth_20 = % stocks above EMA20
breadth_50 = % stocks above EMA50
breadth_200 = % stocks above EMA200
```

Also calculate advances/declines.

## STRONG_BULL

Recommended:

```text
NIFTY close > EMA50
NIFTY close > EMA200
EMA50 > EMA200
breadth_50 >= 60%
```

## SELECTIVE_BULL

```text
NIFTY close > EMA200
AND not STRONG_BULL
AND breadth_50 >= 40%
```

## BEARISH

```text
NIFTY close < EMA200
AND EMA50 < EMA200
AND breadth_50 < 40%
```

## NEUTRAL

Everything else.

All thresholds are configurable.

## Regime multiplier

```text
STRONG_BULL     1.00
SELECTIVE_BULL  0.95
NEUTRAL         0.85
BEARISH         0.65
```

Store both raw and adjusted scores.

---

# 6. Sector Strength

For every configured sector index calculate:

```text
Return5
Return20
Return60
Return120
RelativeStrength vs NIFTY200
Trend
Breadth
```

Initial score:

```text
5D momentum          20%
20D momentum         25%
60D momentum         20%
Relative strength    20%
Trend                10%
Breadth               5%
```

Normalize components into comparable percentile/score ranges.

Sector score = 0–100.

---

# 7. Indicators

Calculate:

```text
EMA10
EMA20
EMA50
EMA100
EMA200

RSI14
ADX14

MACD
MACD signal
MACD histogram

ATR14
ATR%

ROC5
ROC10
ROC20
ROC60
ROC120

VolumeSMA20
VolumeSMA50
RVOL20

High20
High50
High252
Low20
Low50
Low252
```

---

# 8. Trend Filter

BUY candidates MUST satisfy:

```text
Close > EMA20
EMA20 > EMA50
EMA50 > EMA200
```

Additional positive evidence:

```text
EMA20 slope > 0
EMA50 slope > 0
Close near 52-week high
```

Initial BUY implementation should reject candidates failing the basic bullish structure.

---

# 9. Momentum

Calculate:

```text
Return5   = Close / Close[-5]   - 1
Return20  = Close / Close[-20]  - 1
Return60  = Close / Close[-60]  - 1
Return120 = Close / Close[-120] - 1
```

Percentile-rank within the eligible stock universe.

Weights:

```text
5D       15%
20D      35%
60D      35%
120D     15%
```

---

# 10. Relative Strength

Primary benchmark:

```text
NIFTY200
```

Calculate:

```text
RS5   = StockReturn5   - NIFTY200Return5
RS20  = StockReturn20  - NIFTY200Return20
RS60  = StockReturn60  - NIFTY200Return60
RS120 = StockReturn120 - NIFTY200Return120
```

Also calculate:

```text
RS_ratio = StockClose / NIFTY200Close
```

Measure whether the ratio is improving.

Preferred:

```text
RS percentile >= 80
```

Strong:

```text
>= 90
```

Reason: outperformers are preferable to stocks merely rising with the broad market.

---

# 11. Breakout Setups

## 11.1 20D breakout

```text
Close > highest_high(previous 20 completed sessions)
```

## 11.2 50D breakout

```text
Close > highest_high(previous 50 completed sessions)
```

## 11.3 252D breakout

```text
Close >= highest_high(previous 252 completed sessions)
```

## 11.4 Breakout retest

A previous breakout occurs, then price retests:

- breakout level;
- EMA20;
- EMA50;

and produces bullish confirmation while preserving trend structure.

Retest tolerance should be ATR-based and configurable.

## 11.5 Trend continuation

Bullish trend + pullback toward EMA20/EMA50 + lower pullback volume + bullish reversal/continuation confirmation.

Breakout setups have higher initial priority than continuation setups.

---

# 12. Critical Breakout Implementation Rule

The current candle MUST NOT be included in its own breakout reference.

Correct conceptual implementation:

```python
previous_20_high = high.shift(1).rolling(20).max()
breakout = close > previous_20_high
```

This prevents look-ahead/self-reference errors.

---

# 13. Volume

```text
RVOL20 = CurrentVolume / SMA(Volume,20)
```

Interpretation:

```text
<1.00       weak
1.00–1.25   normal
1.25–1.50   good
1.50–2.00   strong
>2.00       very strong
```

Preferred breakout:

```text
RVOL20 >= 1.50
```

Minimum setup quality:

```text
RVOL20 >= 1.25
```

For retests/continuations, lower volume during the pullback is positive if volume expands on confirmation.

---

# 14. RSI

Preferred:

```text
50 <= RSI14 <= 70
```

RSI >70 does NOT automatically reject.

Apply an overextension penalty when all are true:

```text
RSI > 75
AND
price materially extended from EMA20
AND
ATR-adjusted extension is excessive
```

The exact extension threshold is configurable.

---

# 15. ADX

Interpretation:

```text
<15        no meaningful trend
15–20      weak
20–25      developing
25–35      strong
>35        very strong
```

Preferred:

```text
ADX >= 20
```

Reward:

```text
ADX >= 25
```

ADX is not a standalone BUY signal.

---

# 16. MACD

Positive evidence:

```text
MACD > Signal
MACD histogram > 0
```

A fresh bullish crossover can add setup quality.

MACD alone cannot create a BUY.

---

# 17. ATR

```text
ATR_pct = ATR14 / Close * 100
```

Use ATR for:

- stop-loss;
- volatility normalization;
- gap/extension checks;
- optional target projection.

Do not use one universal stop percentage for all stocks.

---

# 18. Support and Resistance

Potential support:

- recent swing low;
- breakout level;
- EMA20;
- EMA50;
- consolidation low.

Potential resistance:

- previous swing high;
- 20D high;
- 50D high;
- 252D high;
- supply zone.

Persist the levels used in the trade explanation.

---

# 19. Momentum Score

Raw score:

```text
Trend                  20
Relative Strength      20
Price Momentum         15
Breakout/Setup         15
Volume                 10
Technical Momentum     10
Sector Strength         5
Market Regime           5
                       ---
                       100
```

## Trend

```text
Close > EMA20          4
EMA20 > EMA50          4
EMA50 > EMA200         4
EMA20 slope positive   3
EMA50 slope positive   2
Near 52W high          3
```

## Relative Strength

Base:

```text
RS percentile >=90    10
80–90                   7
70–80                   4
<70                     0
```

Add improvement points, capped at 20 total.

## Momentum

Use 5/20/60/120D percentile ranks with:

```text
5D  15%
20D 35%
60D 35%
120D 15%
```

## Setup

Base score:

```text
252D breakout          15
50D breakout            13
20D breakout            10
breakout retest          9
trend continuation       8
pullback reversal        6
none                     0
```

Cap at 15.

## Volume

```text
RVOL >2.0              10
1.5–2.0                 8
1.25–1.5                5
1.0–1.25                2
<1.0                    0
```

## Technical Momentum

Combine:

- RSI;
- ADX;
- MACD;
- ROC.

Cap at 10.

## Sector

```text
sector percentile >90   5
80–90                    4
70–80                    3
60–70                    1
<60                      0
```

## Market

```text
STRONG_BULL              5
SELECTIVE_BULL           4
NEUTRAL                  2
BEARISH                  0
```

---

# 20. Final Score

```text
raw_score = sum(component scores)

adjusted_score = raw_score * regime_multiplier
```

Classification:

```text
>=85       STRONG BUY CANDIDATE
80–84.99   BUY CANDIDATE
75–79.99   WATCH / CONFIRM
65–74.99   MONITOR
<65        IGNORE
```

A high score cannot override a hard rejection.

---

# 21. Hard Rejection Rules

Reject if:

```text
INSUFFICIENT_HISTORY
DATA_INVALID
DATA_STALE
BENCHMARK_DATA_MISSING
LIQUIDITY_FAIL
PRICE_FILTER_FAIL
TREND_FAIL
NO_SETUP
INVALID_STOP
LOW_RR
```

Additional portfolio-level rejection:

```text
POSITION_CAP
SECTOR_CAP
CORRELATION_CAP
PORTFOLIO_RISK_CAP
CASH_RESERVE
```

---

# 22. Entry

Supported:

```text
BREAKOUT
RETEST
CONTINUATION
```

Breakout:

```text
entry = breakout_level + confirmation_buffer
```

Continuation:

```text
entry = confirmation_candle_high + buffer
```

Buffer is configurable and should preferably be volatility-aware.

---

# 23. Gap Protection

Calculate:

```text
gap_pct =
(open - planned_entry) / planned_entry
```

If the configured threshold is exceeded:

```text
status = DO_NOT_CHASE
```

Wait for pullback, consolidation or fresh confirmation.

Do not force an entry.

---

# 24. Stop Loss

Candidate stop methods:

1. structural swing low;
2. ATR stop;
3. EMA/support stop.

ATR example:

```text
ATR_SL = Entry - ATR14 * multiplier
```

Recommended initial multiplier:

```text
1.5
```

This is an ASSUMPTION / RECOMMENDATION.

Store selected method and all relevant calculations.

---

# 25. Target and R:R

Calculate:

```text
risk_per_share = entry - stop
reward_per_share = target - entry
RR = reward_per_share / risk_per_share
```

Hard minimum:

```text
RR >= 1.5
```

Preferred:

```text
RR >= 2.0
```

Target may use:

- next resistance;
- previous swing high;
- 52W high;
- measured move;
- ATR projection.

---

# 26. Risk Model

Capital:

```text
₹10,00,000
```

Default:

```text
risk_per_trade = 0.50%
```

Maximum initial risk:

```text
₹5,000
```

A configurable 0.75% upper setting may be supported, but MUST NOT be the default.

---

# 27. Position Sizing

```python
risk_per_share = entry - stop

risk_qty = floor(
    max_trade_risk / risk_per_share
)

capital_qty = floor(
    max_position_value / entry
)

quantity = min(risk_qty, capital_qty)
```

Never round upward beyond risk/capital limits.

---

# 28. Portfolio Constraints

Defaults:

```text
max_positions = 8
max_position_pct = 15
max_sector_exposure_pct = 30
max_portfolio_risk_pct = 4
minimum_cash_pct = 20
correlation_lookback = 60
```

These are configurable.

---

# 29. Correlation

Calculate rolling 60D return correlation.

If two candidates are highly correlated, prefer the stronger candidate unless portfolio rules permit both.

Correlation is a portfolio filter, not a stock-quality score.

---

# 30. Portfolio Selection

Sort candidates by adjusted score.

Then sequentially apply:

```text
position cap
sector cap
correlation
portfolio risk
cash reserve
```

Do NOT simply take the top N scores.

Example:

```text
TCS
INFY
HCLTECH
TECHM
WIPRO
```

should be recognized as concentrated IT exposure.

Select the strongest feasible set across strong sectors.

---

# 31. Trade Lifecycle

```text
SCANNED
→ QUALIFIED
→ SIGNAL
→ ENTRY_PENDING
→ ENTERED
→ ACTIVE
→ TRAILING
→ EXITED
```

Exit reasons:

```text
STOP_LOSS
TARGET
TRAILING_STOP
MOMENTUM_FAILURE
TIME_STOP
MARKET_REGIME_CHANGE
MANUAL_EXIT
```

---

# 32. Trailing Stop

After approximately +1R:

- move toward breakeven if configured.

After +1.5R/+2R:

- trail using EMA20, ATR or previous swing low.

Exact rule is configurable.

---

# 33. Time Stop

Recommended initial rule:

```text
If trade has not reached +0.5R within 5 trading days,
flag for review/exit.
```

ASSUMPTION / RECOMMENDATION; configurable.

---

# 34. Strategy Independence

The existing Bot-Stocks Momentum Engine MUST NOT be modified merely to make it behave like Momentum-ChatGPT.

If shared code is useful:

```text
extract common reusable logic
```

rather than:

```text
change existing strategy semantics
```

The behavior of the existing strategy must remain backward-compatible unless an explicit migration is approved.

---

# 35. Strategy Comparison

Momentum-ChatGPT should eventually expose comparable:

```text
signal
score
setup
entry
SL
R:R
risk
return
holding period
```

so it can be compared against the existing Momentum Engine.

