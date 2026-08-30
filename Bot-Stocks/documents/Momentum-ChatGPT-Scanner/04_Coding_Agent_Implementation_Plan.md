# 04 — Coding Agent Implementation Plan
## Build Momentum-ChatGPT inside Bot-Stocks

This is the operational instruction set for Cline, Bob, Cursor, Claude Code, or another autonomous coding agent.

---

# 1. Primary Instruction

> **Bot-Stocks is the existing host application. Momentum-ChatGPT MUST be implemented as a new strategy engine within Bot-Stocks. Do NOT create a separate bot/application.**

The agent must first understand and reuse Bot-Stocks infrastructure.

---

# 2. Absolute Rules

The agent MUST:

1. Inspect Bot-Stocks before changing code.
2. Read all four specification files.
3. Produce a codebase assessment before implementation.
4. Reuse existing WebSocket/data infrastructure.
5. Reuse existing historical-data infrastructure.
6. Reuse existing timeframe infrastructure.
7. Reuse existing database/storage.
8. Reuse existing FastAPI.
9. Reuse existing dashboard.
10. Reuse existing logging.
11. Reuse existing scheduler.
12. Reuse existing broker abstraction.
13. Reuse mathematically identical indicators where appropriate.
14. Keep Momentum-ChatGPT strategy rules separate.
15. Preserve existing Momentum Engine behavior.
16. Keep configuration strategy-specific.
17. Keep strategy versions separate.
18. Write tests for every new rule.
19. Never introduce live execution during initial implementation.
20. Never introduce ML initially.
21. Never use future data.
22. Never silently relax filters.
23. Never duplicate infrastructure without justification.
24. Stop and ask if a material ambiguity cannot be resolved from the specifications or existing code.

---

# 3. First Prompt — Repository Discovery

Give the coding agent this prompt first:

```text
Read the four Momentum-ChatGPT specification files completely.

Do NOT implement anything yet.

Inspect the entire Bot-Stocks repository.

Identify:
1. Existing WebSocket/data layer.
2. Historical market-data layer.
3. Instrument/universe management.
4. Timeframe implementation.
5. Indicator/analytics implementation.
6. Existing Momentum Engine.
7. Risk management.
8. Portfolio management.
9. Database/storage.
10. FastAPI application.
11. Dashboard.
12. Scheduler.
13. Logging.
14. Broker/execution abstraction.
15. Existing tests.

Create:
docs/MOMENTUM_CHATGPT_CODEBASE_ASSESSMENT.md

The assessment must include a reuse matrix:

Component | Existing location | Reuse | Extend | New | Notes

Explicitly identify:
- what should be reused unchanged;
- what should be extended;
- what should be strategy-specific;
- what should not be duplicated;
- conflicts between the specification and existing Bot-Stocks behavior.

Do not modify production code in this phase.

Stop after producing the assessment.
```

---

# 4. Phase 1 — Integration Design

After reviewing the assessment, implement only the integration skeleton.

Requirements:

```text
strategy_id = momentum_chatgpt
strategy_version = 1.0.0
```

Create/reuse strategy interface.

Expected conceptual structure:

```text
strategies/
├── existing_momentum/
└── momentum_chatgpt/
```

Do not copy the existing Momentum Engine wholesale.

The new strategy should consume common Bot-Stocks services.

## Tests

Verify:

- strategy registration;
- independent configuration;
- existing Momentum Engine still loads;
- no API/data duplication.

---

# 5. Phase 2 — Data and Universe

Reuse Bot-Stocks data.

Implement:

```text
NIFTY200
NIFTY_NEXT50
NIFTY250
NIFTY500
NIFTY200_MOMENTUM30
```

Primary:

```text
NIFTY200
```

Require:

```text
>=300 sessions
```

Apply:

```text
minimum turnover = ₹25 crore
minimum price = ₹100
```

Do not create another market-data transport.

## Tests

- historical membership;
- insufficient history;
- missing data;
- duplicate data;
- liquidity;
- price filter.

---

# 6. Phase 3 — Indicators

Reuse existing Bot-Stocks indicators if their definitions match.

Required:

```text
EMA10/20/50/100/200
RSI14
ADX14
MACD
ATR14
ROC5/10/20/60/120
VolumeSMA20/50
RVOL20
High20/50/252
Low20/50/252
```

Do not duplicate an existing correct implementation merely because the new strategy has different thresholds.

If existing indicators have incompatible semantics, add a clearly named compatible implementation or adapter.

---

# 7. Phase 4 — Market Regime

Implement the rules in document 02.

Output:

```text
STRONG_BULL
SELECTIVE_BULL
NEUTRAL
BEARISH
```

Store:

```text
regime
raw inputs
multiplier
timestamp
```

Test every threshold boundary.

---

# 8. Phase 5 — Sector Engine

Implement:

```text
5D
20D
60D
120D
relative strength
trend
breadth
sector score
rank
```

Use the configured weights.

Return a deterministic sector ranking.

---

# 9. Phase 6 — Momentum + Relative Strength

Implement:

```text
5D
20D
60D
120D
```

Momentum weighting:

```text
5D  15%
20D 35%
60D 35%
120D 15%
```

Relative strength:

```text
stock return - NIFTY200 return
```

Preferred RS percentile:

```text
>=80
```

Test benchmark alignment and missing data.

---

# 10. Phase 7 — Setup Detection

Implement independent detectors:

```text
20D breakout
50D breakout
252D breakout
breakout retest
trend continuation
pullback reversal
```

The current candle MUST NOT be included in its own breakout reference.

Add tests specifically designed to detect self-reference/look-ahead bugs.

---

# 11. Phase 8 — Scoring

Implement:

```text
Trend              20
Relative Strength  20
Momentum           15
Setup              15
Volume             10
Technical          10
Sector              5
Market              5
```

Then:

```text
adjusted_score = raw_score * regime_multiplier
```

Thresholds:

```text
85+  STRONG BUY
80+  BUY
75+  WATCH
65+  MONITOR
<65  IGNORE
```

Hard filters MUST remain separate from score.

---

# 12. Phase 9 — Risk

Defaults:

```text
capital = ₹10,00,000
risk/trade = 0.50%
max risk/trade = ₹5,000
```

Position cap:

```text
15%
```

Minimum R:R:

```text
1.5
```

Preferred:

```text
2.0
```

ATR stop multiplier:

```text
1.5
```

recommended default.

Implement structural, ATR and EMA/support stop candidates.

---

# 13. Phase 10 — Portfolio

Defaults:

```text
max positions = 8
max sector = 30%
max portfolio risk = 4%
minimum cash = 20%
correlation lookback = 60D
```

Do not select simply by score.

Apply:

```text
score
→ position cap
→ sector cap
→ correlation
→ portfolio risk
→ cash
```

Test concentrated IT examples such as:

```text
TCS
INFY
HCLTECH
TECHM
WIPRO
```

and ensure the portfolio engine recognizes concentration.

---

# 14. Phase 11 — Reporting

Integrate into the existing FastAPI/dashboard.

Do not create another FastAPI application.

Expose:

```text
market regime
sector ranking
top candidates
score breakdown
trade plans
portfolio
cash
risk
rejections
run status
```

Each candidate must explain why it qualified.

---

# 15. Phase 12 — Backtesting

Implement after the deterministic scanner is stable.

Use the same strategy logic.

MUST prevent:

```text
look-ahead bias
survivorship bias
future constituents
future corporate actions
```

Include:

```text
costs
slippage
risk
portfolio caps
cash
```

---

# 16. Phase 13 — Regression Testing

Before declaring complete:

1. Run all existing Bot-Stocks tests.
2. Run Momentum-ChatGPT tests.
3. Run integration tests.
4. Run E2E tests.
5. Compare fixed-fixture output of existing Momentum Engine before/after.
6. Verify no unintended existing-strategy behavior changed.

Any intentional change to existing behavior must be explicitly documented.

---

# 17. Phase 14 — Paper Trading

Enable:

```text
Momentum-ChatGPT paper mode
```

Do not send broker orders.

Track:

```text
planned entry
observable entry
SL
target
quantity
signal time
exit
MAE
MFE
return
holding period
```

Recommended observation:

```text
8–12 weeks
```

---

# 18. Live Execution Gate

The agent MUST NOT enable live execution automatically.

Live execution requires explicit human approval after:

```text
scanner validation
backtesting
walk-forward validation
paper trading
risk review
```

The execution layer must use existing Bot-Stocks broker abstractions.

Momentum-ChatGPT must generate a `TradePlan`, not directly call broker APIs.

---

# 19. Required Progress Report

After every phase:

```text
PHASE:
STATUS:

IMPLEMENTED:
- ...

REUSED FROM BOT-STOCKS:
- ...

NEW COMPONENTS:
- ...

FILES CHANGED:
- ...

TESTS:
Passed:
Failed:

EXISTING BOT REGRESSION:
PASS / FAIL

ASSUMPTIONS:
- ...

DEVIATIONS:
- ...

KNOWN ISSUES:
- ...

NEXT PHASE:
- ...
```

---

# 20. Mandatory Stop Conditions

Stop and ask for clarification if:

- existing data semantics conflict with the strategy;
- existing Momentum Engine architecture prevents clean coexistence;
- historical constituents are unavailable;
- corporate-action handling is ambiguous;
- execution timing changes backtest meaning;
- risk rules conflict with existing risk controls;
- required market/sector data is unavailable;
- a new infrastructure component appears necessary but duplicates an existing one.

Do not silently invent a major architectural decision.

---

# 21. Final Agent Prompt

Use this after all four documents are present:

```text
You are modifying the existing Bot-Stocks workspace.

Your task is to implement Momentum-ChatGPT as a NEW strategy engine INSIDE Bot-Stocks.

IMPORTANT:
Bot-Stocks already has:
- WebSocket market-data infrastructure
- historical data
- existing Momentum Engine
- FastAPI
- dashboard
- broker/execution infrastructure
- and other common services.

Do NOT create a separate bot.
Do NOT create duplicate WebSocket infrastructure.
Do NOT create a second FastAPI application.
Do NOT create a separate database unless the existing architecture genuinely cannot support the strategy.
Do NOT duplicate indicators or common analytics when an existing compatible implementation can be reused.

Read these four files completely:
1. 01_Architecture_and_Requirements.md
2. 02_Scanning_Strategy_and_Business_Logic.md
3. 03_End_to_End_Process_Flow_and_Implementation.md
4. 04_Coding_Agent_Implementation_Plan.md

Then inspect the entire Bot-Stocks repository.

First perform Phase 0 only and create:
docs/MOMENTUM_CHATGPT_CODEBASE_ASSESSMENT.md

Do not modify production code during Phase 0.

The new strategy must have its own:
- strategy_id
- strategy_version
- configuration
- scoring
- setup detection
- candidate ranking
- portfolio selection
- reporting

Shared infrastructure should be reused.

The existing Momentum Engine must remain backward-compatible.

Implement incrementally in the phases specified by the documents.

After every phase:
- run tests;
- inspect output;
- run existing Bot-Stocks regression tests;
- report reused components;
- report new components;
- report assumptions;
- report deviations;
- stop if a material ambiguity exists.

The strategy must initially be READ-ONLY/PAPER only.

Do not enable live trading.

Do not introduce machine learning.

Do not use future information.

Do not use today's index constituents for historical backtests.

Do not silently weaken strategy filters.

Every candidate must be explainable.

Every meaningful rejection must have a machine-readable reason.

The final result should be a maintainable strategy engine integrated into Bot-Stocks, not a second application.
```
