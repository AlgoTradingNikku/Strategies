# 03 — End-to-End Process Flow and Implementation
## Momentum-ChatGPT inside Bot-Stocks

---

# 1. Architectural Context

`Bot-Stocks` is the host application.

The implementation MUST reuse existing Bot-Stocks infrastructure wherever compatible.

```text
                         BOT-STOCKS
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
 Existing Data          Existing Core        Existing FastAPI
   Layer                  Services              Dashboard
       │                     │                     ▲
       └──────────────┬──────┘                     │
                      ▼                            │
              Momentum-ChatGPT                    │
                      │                            │
          ┌───────────┼───────────┐                │
          ▼           ▼           ▼                │
       Strategy     Risk       Portfolio           │
                      │                            │
                      └────────────┬───────────────┘
                                   ▼
                         Existing Broker Layer
```

---

# 2. Phase 0 — Mandatory Codebase Discovery

Before implementation:

1. inspect repository tree;
2. inspect README/docs;
3. inspect data layer;
4. inspect WebSocket implementation;
5. inspect historical-data implementation;
6. inspect timeframe handling;
7. inspect existing Momentum Engine;
8. inspect indicators;
9. inspect database;
10. inspect FastAPI routes;
11. inspect dashboard;
12. inspect scheduler;
13. inspect risk;
14. inspect broker/execution;
15. inspect tests.

Create:

```text
docs/MOMENTUM_CHATGPT_CODEBASE_ASSESSMENT.md
```

The assessment MUST answer:

```text
What already exists?
What can be reused?
What must be extended?
What must be new?
What existing behavior must remain unchanged?
```

---

# 3. Reuse Matrix

The agent must complete:

```text
Component                 Existing?   Reuse   Extend   New
----------------------------------------------------------
WebSocket data
Historical data
OHLCV storage
Timeframes
Indicators
Universe
Momentum
Risk
Portfolio
FastAPI
Dashboard
Scheduler
Logging
Broker
Alerts
```

No duplicate infrastructure should be created without documented justification.

---

# 4. Main Scanner Flow

```text
START
  ↓
Load Bot-Stocks configuration
  ↓
Load Momentum-ChatGPT configuration
  ↓
Create scanner_run
  ↓
Validate exchange/session calendar
  ↓
Load historical universe
  ↓
Load NIFTY200 benchmark
  ↓
Load NIFTY50 context
  ↓
Load sector indices
  ↓
Obtain stock OHLCV from existing Bot-Stocks data layer
  ↓
Validate/normalize data
  ↓
Apply history/liquidity/price filters
  ↓
Calculate/reuse indicators
  ↓
Calculate market regime + breadth
  ↓
Calculate sector scores
  ↓
Calculate stock momentum
  ↓
Calculate relative strength
  ↓
Detect setups
  ↓
Apply hard trend/setup filters
  ↓
Calculate score
  ↓
Apply regime multiplier
  ↓
Rank candidates
  ↓
Calculate trade plans
  ↓
Apply R:R/risk filters
  ↓
Construct diversified portfolio
  ↓
Generate explainable report
  ↓
Persist audit records
  ↓
END
```

---

# 5. Existing Momentum Engine Coexistence

The two engines MUST be independently runnable.

Conceptually:

```python
strategies = {
    "existing_momentum": ExistingMomentumEngine(...),
    "momentum_chatgpt": MomentumChatGPTEngine(...)
}
```

Both may consume:

```text
same OHLCV
same benchmark
same instrument metadata
same database
same FastAPI
same scheduler
same broker abstraction
```

But each keeps:

```text
strategy_id
strategy_version
configuration
scoring rules
signals
portfolio decisions
```

separate.

---

# 6. Common Analytics vs Strategy Logic

If the existing Momentum Engine already calculates:

```text
EMA
RSI
MACD
ATR
ADX
Volume
ROC
```

reuse the common calculation implementation where mathematically identical.

Do NOT copy the code into:

```text
momentum_chatgpt/
```

unless isolation is required.

However, strategy interpretation remains independent.

Example:

```text
Shared:
RSI14 = 67.2

Existing Momentum:
RSI rule = existing strategy behavior

Momentum-ChatGPT:
RSI rule = strategy document 02
```

---

# 7. Strategy-Specific Module Structure

Prefer:

```text
strategies/
├── existing_momentum/
│
└── momentum_chatgpt/
    ├── engine.py
    ├── config.py
    ├── market_regime.py
    ├── sector_strength.py
    ├── momentum.py
    ├── relative_strength.py
    ├── setups.py
    ├── scoring.py
    ├── trade_plan.py
    ├── portfolio.py
    ├── explanations.py
    └── schemas.py
```

If Bot-Stocks already has an equivalent structure, adapt to it instead of forcing this exact layout.

---

# 8. Shared Infrastructure Interfaces

Use existing interfaces whenever available.

Conceptual interfaces:

```python
MarketDataProvider
UniverseProvider
IndicatorProvider
PortfolioRiskProvider
BrokerProvider
ReportProvider
```

The new strategy should depend on abstractions rather than directly on WebSocket/broker implementation details.

---

# 9. EOD Pipeline

At the end of a completed trading session:

```text
1. Refresh market data
2. Validate final candles
3. Update universe membership
4. Calculate/reuse indicators
5. Calculate market regime
6. Calculate breadth
7. Calculate sector strength
8. Calculate stock momentum
9. Calculate relative strength
10. Detect setups
11. Apply hard filters
12. Score
13. Rank
14. Calculate trade plans
15. Construct candidate portfolio
16. Generate report
17. Persist run
```

---

# 10. Pre-Market Pipeline

```text
1. Load latest EOD candidates
2. Load opening prices
3. Calculate gap
4. Check planned entry
5. Mark excessive gaps DO_NOT_CHASE
6. Update watchlist
7. Prepare confirmation conditions
```

No forced BUY at the opening auction.

---

# 11. Live Confirmation Pipeline

Optional:

```text
15-minute price confirmation
+
volume confirmation
+
sector confirmation
+
market confirmation
```

Only after confirmation:

```text
ENTRY_PENDING → SIGNAL
```

Live confirmation MUST remain separate from EOD ranking.

---

# 12. Candidate Object

A candidate should contain at least:

```text
symbol
company
sector
as_of
market_regime
sector_score

trend metrics
momentum metrics
relative-strength metrics
volume metrics
technical metrics

setup_type
setup_level

raw_score
regime_multiplier
adjusted_score
rank
```

---

# 13. TradePlan Object

```text
symbol
signal_type
entry
stop
target
risk_per_share
reward_per_share
rr
max_risk
quantity
capital_required
risk_amount
status
reason_codes
```

---

# 14. PortfolioPlan

```text
strategy_id
as_of
positions[]
cash
total_capital
capital_deployed
cash_pct
total_risk
sector_exposure
correlation_flags
```

---

# 15. API Integration

Reuse existing FastAPI application.

Suggested namespace:

```text
/api/strategies/momentum-chatgpt
```

Possible routes:

```text
GET /status
GET /latest
GET /candidates
GET /candidates/{symbol}
GET /sectors
GET /regime
GET /portfolio
GET /runs/{run_id}
```

The actual path prefix should follow Bot-Stocks conventions.

Do not create a second FastAPI process unless technically necessary.

---

# 16. Dashboard Integration

Add Momentum-ChatGPT as a strategy view/tab within the existing dashboard.

Recommended panels:

```text
Market Regime
Sector Ranking
Top Candidates
Candidate Score Breakdown
Trade Plans
Selected Portfolio
Cash
Risk
Rejected Candidates
Scanner Run Status
```

The dashboard MUST distinguish:

```text
Existing Momentum
Momentum-ChatGPT
```

so users do not confuse signals.

---

# 17. Configuration Isolation

Use strategy-specific configuration.

Conceptually:

```text
Bot-Stocks global config
        +
Momentum-ChatGPT strategy config
```

Example:

```text
strategy_id: momentum_chatgpt
risk_per_trade_pct: 0.50
minimum_score: 80
minimum_rr: 1.5
```

Do not overwrite existing Momentum Engine parameters.

---

# 18. Logging

Every run:

```text
run_id
strategy_id
strategy_version
config_version
universe_version
data_timestamp
status
duration
```

Every candidate:

```text
symbol
stage
decision
reason_code
```

Example:

```text
HINDALCO
stage=portfolio
decision=REJECT
reason=SECTOR_CAP
```

---

# 19. Error Handling

## Data failure

Retry using existing data-layer policy.

## Missing benchmark

Fail the normal scan.

## Missing stock

Exclude stock and record reason.

## Missing sector

Use configured fallback or reject/downgrade.

## Database failure

Do not publish a successful run.

## Partial scan

Mark:

```text
PARTIAL
```

not SUCCESS.

---

# 20. Performance

The target universe is approximately NIFTY-200 scale, so the initial implementation should favor:

- vectorized calculations;
- bulk database operations;
- cached historical data;
- batch API calls;
- shared indicators.

Do not make one API request per indicator per stock.

---

# 21. Testing

## Unit

Test:

- indicators;
- returns;
- percentiles;
- relative strength;
- trend;
- breakouts;
- RVOL;
- sector score;
- market regime;
- scoring;
- position sizing;
- R:R;
- portfolio caps.

## Integration

Test:

```text
Bot-Stocks data layer
→ Momentum-ChatGPT
→ database
→ FastAPI
```

## E2E

Use a frozen dataset and verify deterministic output.

---

# 22. Regression Tests for Existing Momentum Engine

Adding Momentum-ChatGPT MUST NOT unexpectedly change existing Momentum Engine behavior.

Before and after the implementation:

```text
run existing Momentum Engine test suite
```

Where possible, compare baseline outputs for a fixed fixture.

Any intentional changes must be documented.

---

# 23. Backtesting

Momentum-ChatGPT backtesting MUST use the same core strategy code used by live scanning.

Required:

- historical universe;
- historical benchmark;
- historical sector data;
- historical OHLCV;
- transaction costs;
- slippage;
- position sizing;
- portfolio constraints.

Must prevent:

- look-ahead;
- survivorship bias;
- future constituent leakage;
- future corporate-action leakage.

---

# 24. Strategy Comparison

Future capability:

```text
Backtest:
Existing Momentum
vs
Momentum-ChatGPT
```

Use identical:

```text
capital
cost model
slippage
historical period
execution assumptions
portfolio constraints
```

Compare:

```text
CAGR
Max Drawdown
Sharpe
Sortino
Win Rate
Profit Factor
Expectancy
Exposure
Turnover
Average Holding Period
```

---

# 25. Paper Trading

Run:

```text
Momentum-ChatGPT
```

without live orders.

Recommended observation:

```text
8–12 weeks
```

Track:

```text
signal
planned entry
observable entry
SL
target
actual exit
MAE
MFE
return
holding period
```

---

# 26. Implementation Sequence

```text
PHASE 0
Codebase discovery
      ↓
PHASE 1
Integration interfaces
      ↓
PHASE 2
Universe/data reuse
      ↓
PHASE 3
Indicators/common analytics
      ↓
PHASE 4
Market regime/breadth
      ↓
PHASE 5
Sector strength
      ↓
PHASE 6
Momentum/relative strength
      ↓
PHASE 7
Setup detection
      ↓
PHASE 8
Scoring/ranking
      ↓
PHASE 9
Risk/trade plan
      ↓
PHASE 10
Portfolio construction
      ↓
PHASE 11
FastAPI/dashboard
      ↓
PHASE 12
Backtesting
      ↓
PHASE 13
Paper trading
      ↓
PHASE 14
Optional execution
```

After each phase:

```text
implement
→ test
→ inspect output
→ compare with existing Bot-Stocks behavior
→ document deviations
→ commit
```

---

# 27. Definition of Done

```text
[ ] Bot-Stocks inspected
[ ] Reuse matrix completed
[ ] No unnecessary duplicate infrastructure
[ ] Existing Momentum Engine unaffected
[ ] Momentum-ChatGPT strategy isolated
[ ] Shared indicators reused where appropriate
[ ] NIFTY200 universe
[ ] Market regime
[ ] Breadth
[ ] Sector strength
[ ] Momentum
[ ] Relative strength
[ ] Breakout/retest/continuation
[ ] Volume
[ ] Scoring
[ ] Ranking
[ ] Entry
[ ] SL
[ ] Target
[ ] R:R
[ ] Position sizing
[ ] Portfolio diversification
[ ] Correlation
[ ] FastAPI integration
[ ] Dashboard integration
[ ] Logging
[ ] Error handling
[ ] Unit tests
[ ] Integration tests
[ ] E2E tests
[ ] Backtest
[ ] Look-ahead tests
[ ] Survivorship controls
[ ] Paper mode
[ ] Live execution disabled
```
