# 01 — Architecture and Requirements
## Momentum-ChatGPT Strategy Engine inside Bot-Stocks

**Host application:** `Bot-Stocks`  
**New strategy:** `Momentum-ChatGPT`  
**Market:** NSE India equities  
**Initial capital/risk model:** ₹10,00,000  
**Primary style:** Long-only momentum swing trading  
**Primary holding period:** 2–20 trading days *(ASSUMPTION / RECOMMENDATION; configurable)*  
**Primary universe:** NIFTY 200  
**Extended universes:** NIFTY Next 50, NIFTY 250/500, NIFTY200 Momentum 30  
**Primary benchmark:** NIFTY 200  
**Secondary benchmark/context:** NIFTY 50  
**Execution:** Existing Bot-Stocks execution infrastructure, but disabled for the initial implementation of Momentum-ChatGPT.

> **Critical architectural decision:** `Bot-Stocks` is the existing host/platform. `Momentum-ChatGPT` MUST be implemented as a new strategy engine within `Bot-Stocks`, NOT as a separate bot/application.

---

# 1. Architecture Decision

The system MUST use:

```text
Bot-Stocks
│
├── Existing Common Infrastructure
│   ├── WebSocket market-data layer
│   ├── Historical-data layer
│   ├── Instrument/universe infrastructure
│   ├── Timeframe handling
│   ├── Database/storage
│   ├── Configuration
│   ├── Logging
│   ├── Scheduling
│   ├── FastAPI application
│   ├── Dashboard
│   ├── Broker abstraction
│   └── Existing risk/execution infrastructure
│
├── Existing Momentum Engine
│
└── Momentum-ChatGPT Engine
    ├── Market Regime
    ├── Sector Strength
    ├── Stock Momentum
    ├── Relative Strength
    ├── Setup Detection
    ├── Scoring
    ├── Risk/Trade Plan
    └── Portfolio Selection
```

The new engine MUST reuse existing Bot-Stocks infrastructure wherever it is functionally compatible.

---

# 2. Mandatory Codebase Discovery

Before implementing anything, the coding agent MUST inspect Bot-Stocks and produce a mapping of:

| Requirement | Existing Bot-Stocks component | Reuse? | Extension? | New? |
|---|---|---:|---:|---:|
| WebSocket market data | TBD | TBD | TBD | TBD |
| Historical data | TBD | TBD | TBD | TBD |
| OHLCV storage | TBD | TBD | TBD | TBD |
| Timeframes | TBD | TBD | TBD | TBD |
| Indicators | TBD | TBD | TBD | TBD |
| Universe management | TBD | TBD | TBD | TBD |
| Existing momentum engine | TBD | TBD | TBD | TBD |
| Risk engine | TBD | TBD | TBD | TBD |
| Broker abstraction | TBD | TBD | TBD | TBD |
| FastAPI | TBD | MUST reuse if compatible | — | — |
| Dashboard | TBD | MUST reuse if compatible | — | — |
| Logging | TBD | MUST reuse if compatible | — | — |
| Scheduler | TBD | MUST reuse if compatible | — | — |

The agent MUST NOT start by creating duplicate data, FastAPI, broker, database, WebSocket, or logging infrastructure.

---

# 3. Objective

Implement a deterministic, explainable, reproducible `Momentum-ChatGPT` strategy engine that identifies high-quality momentum swing opportunities using:

```text
Market Regime
→ Sector Strength
→ Universe
→ Trend
→ Momentum
→ Relative Strength
→ Breakout/Retest/Continuation
→ Volume
→ Technical Quality
→ Score
→ Risk/Reward
→ Position Sizing
→ Portfolio Diversification
```

The strategy should coexist with the existing Bot-Stocks Momentum Engine.

---

# 4. What MUST Be Separate

The following MUST be strategy-specific:

- Momentum-ChatGPT strategy configuration;
- Momentum-ChatGPT scoring;
- Momentum-ChatGPT setup detection;
- Momentum-ChatGPT market-regime interpretation if existing logic differs;
- Momentum-ChatGPT sector ranking;
- Momentum-ChatGPT relative-strength logic;
- Momentum-ChatGPT candidate ranking;
- Momentum-ChatGPT portfolio-selection rules;
- Momentum-ChatGPT reports;
- Momentum-ChatGPT strategy version.

Suggested identity:

```text
strategy_id = "momentum_chatgpt"
strategy_version = "1.0.0"
```

---

# 5. What MUST Be Shared

Where compatible, reuse:

- WebSocket connections;
- market-data ingestion;
- historical OHLCV;
- instrument metadata;
- exchange/session calendar;
- existing timeframe abstractions;
- existing indicator implementations;
- database infrastructure;
- FastAPI application;
- dashboard framework;
- authentication;
- logging;
- scheduling;
- broker abstraction;
- common order models;
- common risk utilities;
- alerting infrastructure.

Do not duplicate these merely to make the strategy self-contained.

---

# 6. FastAPI Architecture

The existing FastAPI application is the preferred API/dashboard host.

Do NOT create a second FastAPI server unless the existing architecture makes this impossible.

Add strategy-specific routes under a namespace such as:

```text
/api/strategies/momentum-chatgpt/...
```

Possible endpoints:

```text
GET /api/strategies/momentum-chatgpt/status
GET /api/strategies/momentum-chatgpt/latest
GET /api/strategies/momentum-chatgpt/candidates
GET /api/strategies/momentum-chatgpt/candidates/{symbol}
GET /api/strategies/momentum-chatgpt/sectors
GET /api/strategies/momentum-chatgpt/regime
GET /api/strategies/momentum-chatgpt/portfolio
GET /api/strategies/momentum-chatgpt/runs/{run_id}
```

Exact routing must follow existing Bot-Stocks API conventions.

The dashboard SHOULD expose:

- market regime;
- sector ranking;
- candidate ranking;
- score breakdown;
- entry/SL/target;
- risk;
- portfolio;
- rejected candidates/reasons;
- scanner run status.

---

# 7. WebSocket/Data Architecture

Existing Bot-Stocks WebSocket infrastructure is the source of truth for streaming data.

Momentum-ChatGPT MUST consume data through existing abstractions.

Do NOT open independent WebSocket connections for the new strategy unless explicitly required by the existing architecture.

The strategy MUST remain independent from the transport mechanism.

Conceptually:

```text
Existing WebSocket
       ↓
Bot-Stocks Data Layer
       ↓
Normalized Market Data
       ↓
Momentum-ChatGPT
```

---

# 8. Strategy Interface

If Bot-Stocks already has a strategy interface, extend/reuse it.

Otherwise introduce a generic strategy contract such as:

```python
class StrategyEngine(Protocol):
    strategy_id: str
    strategy_version: str

    def scan(self, context) -> list:
        ...

    def score(self, candidates, context) -> list:
        ...

    def generate_trade_plans(self, candidates, context) -> list:
        ...
```

Then:

```python
class ExistingMomentumEngine(StrategyEngine):
    ...

class MomentumChatGPTEngine(StrategyEngine):
    ...
```

The exact implementation MUST follow existing Bot-Stocks patterns.

---

# 9. Functional Requirements

## MUST HAVE

1. New `Momentum-ChatGPT` strategy engine inside Bot-Stocks.
2. Dynamic NIFTY 200 universe.
3. Optional extended universes.
4. Daily OHLCV.
5. Existing data-layer reuse.
6. Market regime.
7. Market breadth.
8. Sector strength.
9. Technical indicators.
10. Momentum.
11. Relative strength.
12. Breakout/retest/continuation setups.
13. Volume confirmation.
14. 0–100 score.
15. Ranking.
16. Entry/SL/target/R:R.
17. Risk-based position sizing.
18. Portfolio concentration controls.
19. Correlation control.
20. Explainability.
21. Auditability.
22. Backtesting.
23. Paper-trading mode.
24. Tests.
25. Configuration-driven parameters.
26. Live execution disabled initially.

## SHOULD HAVE

- Existing FastAPI dashboard integration.
- JSON/CSV/HTML reports.
- 15-minute confirmation.
- alerts;
- historical constituents;
- structured logs.

## OPTIONAL / FUTURE

- full automatic execution;
- ML;
- advanced regime models;
- distributed processing.

---

# 10. Non-Functional Requirements

The strategy MUST be:

- deterministic;
- testable;
- explainable;
- reproducible;
- modular;
- configuration-driven;
- safe;
- observable;
- performant for NIFTY 200-scale scanning.

---

# 11. Data Requirements

Required daily fields:

```text
symbol
date
open
high
low
close
volume
turnover if available
adjusted_close if available
```

Optional:

```text
1-minute
5-minute
15-minute
1-hour
```

Daily data is authoritative for EOD ranking.

15-minute/1-hour data is optional confirmation.

Minimum history:

`>=300 trading sessions`.

---

# 12. Storage

Reuse Bot-Stocks database/storage.

Do NOT create a separate database unless the existing architecture cannot support strategy isolation.

Strategy-specific records may use:

```text
strategy_id
strategy_version
scanner_run_id
```

Minimum logical datasets:

- OHLCV;
- universe membership;
- indicator snapshots;
- sector scores;
- market regimes;
- momentum scores;
- trade plans;
- portfolio plans;
- scanner runs.

---

# 13. Configuration

Use Bot-Stocks' existing configuration mechanism if suitable.

Otherwise add:

```text
config/strategies/momentum_chatgpt.yaml
```

All strategy-specific thresholds MUST be configurable.

Recommended defaults are defined in document 02.

---

# 14. Scheduling

Use existing Bot-Stocks scheduler if available.

Required modes:

```text
EOD
PRE_MARKET
LIVE_CONFIRMATION
```

No new scheduler should be created unless required.

---

# 15. Logging

Reuse existing logging.

Every Momentum-ChatGPT run must include:

```text
run_id
strategy_id
strategy_version
config_version
universe_version
data_timestamp
status
```

Candidate rejection reasons MUST be machine-readable.

---

# 16. Error Handling

The strategy MUST fail safely.

If:

- benchmark data is missing → normal scan cannot be trusted;
- required stock data is invalid → reject stock;
- sector data is missing → apply configured fallback or reject/downgrade;
- data is stale → do not generate a normal BUY;
- database fails → run cannot be marked successful;
- WebSocket/API fails → use existing Bot-Stocks recovery mechanisms.

Never silently substitute zero values.

---

# 17. Security

Reuse Bot-Stocks secret management.

Never duplicate or expose:

- broker credentials;
- API keys;
- WebSocket tokens;
- session tokens.

The Momentum-ChatGPT strategy should not directly manage credentials.

---

# 18. Extensibility

The architecture SHOULD support:

```text
Existing Momentum
Momentum-ChatGPT
Future Mean Reversion
Future Breakout
Future ML Strategy
```

All strategies should consume shared infrastructure and produce compatible signal/trade-plan models.

---

# 19. Strategy Comparison

A major benefit of this architecture is that the existing Momentum Engine and Momentum-ChatGPT can run against the same:

- market data;
- universe;
- costs;
- risk model;
- timeframes;
- historical period.

The system SHOULD eventually support comparison:

```text
Strategy A
vs
Momentum-ChatGPT
```

using common metrics.

This is a future enhancement unless comparison infrastructure already exists.

---

# 20. Execution Safety

Initial Momentum-ChatGPT implementation MUST be:

```text
READ_ONLY
```

Then:

```text
PAPER
→ MANUAL APPROVAL
→ SEMI-AUTOMATED
→ FULL AUTOMATION
```

Do not enable live order placement during scanner development.

---

# 21. Assumptions / Recommendations

These are recommendations, not immutable rules:

- holding period: 2–20 trading days;
- NIFTY 200 primary universe;
- ₹25 crore minimum 20D turnover;
- ₹50 crore preferred;
- price >= ₹100;
- risk/trade = 0.50%;
- maximum position = 15%;
- maximum sector = 30%;
- maximum positions = 8;
- minimum cash = 20%;
- portfolio risk cap = 4%;
- minimum R:R = 1.5;
- preferred R:R = 2.0.

