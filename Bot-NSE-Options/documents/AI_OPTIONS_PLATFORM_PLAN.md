# AI-Assisted Options Strategy Platform — Build Plan

**Repository:** `Bot-NSE-Options`
**Scope:** MVP 1 → MVP 2 new Python platform built on the existing FastAPI foundation
**Broker bridge:** OpenAlgo Python SDK (existing `trading_adapter.py`)
**Core principle:** Python is the quantitative source of truth. LLM interprets, human approves, broker executes.

## Finalized Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary LLM | `claude-sonnet-4-5` (Anthropic) | Best structured JSON compliance + reasoning quality for financial analysis |
| Fallback LLM | `gpt-4o` (OpenAI) | Continuity during Anthropic downtime |
| LLM Orchestration | LangGraph 0.2+ | Best stateful workflow + native human-interrupt support |
| LLM Tool Integration | LangChain 0.2+ | Provider abstraction, structured output parsing |
| State persistence | In-memory MemorySaver (MVP 1) | Sufficient; full audit goes to SQLite |
| Database | SQLite + WAL mode | Existing pattern; PostgreSQL deferred to MVP 3 |
| Greeks source | OpenAlgo `optiongreeks` API | Server-side BSM; no scipy needed per-strike |
| Execution | OpenAlgo `optionsmultiorder` | Multi-leg in one API call |
| Order mode | Semi-manual (Approve → Confirm modal) | Architecture hard requirement for MVP 1 |
| Strikes analyzed | ATM ±10 (20 CE + 20 PE) | ±500 pts coverage; manageable prompt size |
| Recommendation TTL | 90 seconds + 0.3% spot delta | NIFTY can move 75+ pts in 90s |
| Strategy types (MVP 1) | 8 types (see Sub-Task 5) | Covers directional, neutral, and vol plays |
| **Index selection** | **Dashboard toggle — NIFTY (default), BANKNIFTY, FINNIFTY** | User switches index without touching config; platform is index-agnostic |

## Strategy Scoring Weights (configurable in `config.yml`)

| Factor | Weight |
|--------|--------|
| Trend alignment | 22% |
| OI structure | 18% |
| Risk/reward ratio | 15% |
| IV environment | 12% |
| Probability metrics | 10% |
| Liquidity (bid/ask + OI) | 10% |
| Expected-move alignment | 8% |
| Event risk | 3% |
| Slippage estimate | 2% |

---

## Platform Toggle System

The platform exposes four levels of toggles, all controllable from the AI Strategy dashboard tab. Toggle state is persisted to SQLite (`settings` table) and survives restarts. `config.yml` provides the defaults loaded on first run.

### Toggle Persistence Rules
- **Default state:** all data + quant + strategy toggles default `true`; execution safety toggles follow `config.yml` risk defaults
- **Storage:** runtime state in SQLite `settings` table; `config.yml` = factory defaults only
- **Live effect:** toggling any component takes effect on the next analysis run — no restart needed
- **Weight redistribution:** disabling a quant component redistributes its scoring weight proportionally across remaining active components so total always sums to 100%
- **LLM prompt impact:** disabled components are automatically excluded from the LLM context payload
- **Locked toggles:** Auto-Execution locked OFF until MVP 3 (visible but labelled "Available in MVP 3")
- **Greyed toggles:** FINNIFTY and Futures Basis visible but greyed with "MVP 2" badge

### Level 1 — Index & Data Source Toggles (default: all ON)

| Toggle | Default | Controls |
|--------|---------|----------|
| Index: NIFTY | ON (default active) | Primary index selection |
| Index: BANKNIFTY | OFF (selectable) | Switch full pipeline to BANKNIFTY |
| Index: FINNIFTY | OFF (greyed, MVP 2) | Enabled in MVP 2 |
| Expiry: Weekly | ON (default) | Use nearest weekly expiry |
| Expiry: Monthly | OFF | Use current month expiry |
| Data: Option Chain | ON | Fetch full chain via `optionchain` API |
| Data: Spot/Futures | ON | Fetch spot LTP and OHLCV history |
| Data: India VIX | ON | Fetch INDIAVIX LTP for vol regime |
| Data: Option Greeks | ON | Fetch per-strike Greeks via `optiongreeks` API |
| Data: Futures Basis | OFF (greyed, MVP 2) | Futures premium/discount vs spot |

### Level 2 — Quant Engine Component Toggles (default: all ON)

| Toggle | Default | Scoring Weight | Controls |
|--------|---------|---------------|----------|
| OI Analysis | ON | 18% | PCR, call/put writing zones, OI concentration, S/R from OI |
| IV Engine | ON | 12% | ATM IV, IV Rank, IV Percentile, Expected Move |
| Greeks | ON | part of scoring | Per-strike delta, gamma, theta, vega |
| Technicals | ON | 22% (trend) | EMA 9/20/50, VWAP, RSI 14, ATR 14 |
| Regime Engine | ON | master gate | Market regime classification (8 regimes + NO_TRADE) |
| VIX Regime | ON | 3% (event risk) | Fear/greed overlay on regime |
| Session Weighting | OFF | optional bonus | Time-of-day signal bias (opening/prime/closing) |

### Level 3 — Strategy Type Toggles (default: all ON)

| Toggle | Default | Strategy Type |
|--------|---------|--------------|
| Bull Put Spread | ON | Mildly bullish, credit, defined risk |
| Bear Call Spread | ON | Mildly bearish, credit, defined risk |
| Bull Call Spread | ON | Strongly bullish, debit |
| Bear Put Spread | ON | Strongly bearish, debit |
| Iron Condor | ON | Range/neutral, four legs |
| Iron Fly | ON | Very tight range, ATM short |
| Long Straddle | ON | High-vol / event-driven |
| Short Strangle | ON | Low-vol, premium selling |

### Level 4 — Risk & Execution Toggles (defaults from `config.yml` risk section)

| Toggle | Default | Controls |
|--------|---------|----------|
| Kill Switch | OFF | 🛑 Blocks ALL new orders immediately |
| Paper Trade Mode | OFF | Simulate without real broker orders |
| Auto-Execution | OFF (locked) | Automatic order placement — locked until MVP 3 |
| Daily Loss Limit | OFF | Auto-halt when day P&L exceeds max_loss_pct |
| Market Hours Check | OFF | Block orders outside 9:15–15:30 IST |
| Duplicate Guard | OFF | Block re-entry on same symbol while position open |
| Recommendation TTL | ON | Invalidate recommendation after 90s or 0.3% spot move |
| Consecutive Loss Breaker | OFF | Pause after N consecutive losing trades |

### Settings API
- `GET  /api/ai/settings` — returns full current toggle state as JSON
- `POST /api/ai/settings/toggle` — `{key, value}` — updates single toggle, persists to DB
- `POST /api/ai/settings/reset` — resets all toggles to `config.yml` defaults

---

## Top-Level Overview

### Goal
Transform the existing UTBot signal scanner into a full **AI-assisted options decision-support platform** with:
1. Live option chain ingestion → deterministic quant analysis
2. Strategy candidate generation + payoff/risk calculations
3. LangGraph-orchestrated workflow with LLM (Claude/GPT) interpretation
4. Dashboard that displays ranked strategy recommendations
5. Human-in-the-loop approval gate before multi-leg order placement
6. Audit trail of every recommendation, decision, and execution

### Approach
- **Evolve, don't rewrite.** The existing bot's FastAPI app, `trading_adapter`, `risk_manager`, `trade_db`, `signals`, and observability modules are reused directly.
- New modules are added as subdirectories/files alongside existing code.
- No second backend. LangGraph and LangChain are **internal services** consumed by the FastAPI app.
- SQLite (WAL mode) retained for MVP 1/2. PostgreSQL deferred to MVP 3.
- **Platform is index-agnostic from day one.** Every quant/strategy/workflow module takes an `UnderlyingConfig` object. Adding a new index = zero code change.

### Supported Indexes (Dashboard Toggle)

| Index | Exchange | Strike Gap | Lot Size | Expiry Day | MVP Phase |
|-------|----------|-----------|----------|-----------|-----------|
| **NIFTY** | NSE_INDEX | 50 pts | 75 | Thursday | MVP 1 (default) |
| **BANKNIFTY** | NSE_INDEX | 100 pts | 35 | Wednesday | MVP 1 (toggle enabled) |
| **FINNIFTY** | NSE_INDEX | 50 pts | 40 | Tuesday | MVP 2 |

All three are in `config.yml` from day one. NIFTY is the default active selection. The dashboard toggle switches context — the entire analysis pipeline re-runs for the selected index. FINNIFTY is visible in the toggle but greyed out until MVP 2 (controlled by `enabled: true/false` in config).

### Index Toggle Behavior
- Selecting an index on the dashboard updates the active `underlying` context for the current session
- The LangGraph workflow is index-aware via `AnalysisState.underlying` and `AnalysisState.underlying_config`
- Each index maintains its own recommendation history and audit trail in the database
- The existing UTBot scanner tab continues to operate on whichever `options.underlying` is set in `config.yml` — it is unaffected by the AI tab toggle

### MVP Scope of This Plan
- **MVP 1:** NIFTY + BANKNIFTY (toggle), one expiry per index, ±10 strikes ATM, 8 strategy types, LLM ranking, dashboard, human approval, no automatic execution
- **MVP 2:** FINNIFTY, historical snapshot storage, IV Rank, multiple expiries, paper-trade mode

---

## Architecture Summary

```
OpenAlgo API
    │
    ▼
Data Ingestion Layer       ← new: data/providers/option_chain_provider.py
    │ (frozen OptionChainSnapshot)
    ▼
Python Quant Engine        ← new: quant/ (greeks, iv, oi_analysis, technicals, payoff)
    │
    ▼
Strategy Engine            ← new: strategies/ (8 strategy generators)
    │
    ▼
LangGraph Workflow         ← new: ai/workflow/graph.py  (stateful)
    │   ├─ Load Snapshot
    │   ├─ Validate Freshness
    │   ├─ Run Quant Analytics
    │   ├─ Classify Regime
    │   ├─ Generate Candidates
    │   ├─ Calculate Risk/Payoff
    │   ├─ Score Candidates
    │   ├─ LLM Interpretation (LangChain)
    │   ├─ Strategy Validation
    │   ├─ Recommendation Output
    │   └─ WAIT_FOR_HUMAN ──── REJECT
    │           │
    │        APPROVE
    │           │
    │   Order Validation
    │           │
    │   Broker Execution (optionsmultiorder)
    ▼
FastAPI API Layer           ← extend existing app.py with new routes
    │
    ▼
Dashboard Frontend          ← extend existing frontend (index.html/js)
```

---

## LangGraph State Schema (TypedDict)

```python
class UnderlyingConfig(TypedDict):
    symbol: str                # "NIFTY" | "BANKNIFTY" | "FINNIFTY"
    exchange: str              # "NSE_INDEX"
    option_exchange: str       # "NFO"
    strike_gap: int            # 50 | 100 | 50
    lot_size: int              # 75 | 35 | 40
    expiry_date: str           # e.g. "24JUL25"
    strikes_each_side: int     # default 10
    enabled: bool

class AnalysisState(TypedDict):
    # Input
    underlying: str                        # "NIFTY" | "BANKNIFTY" | "FINNIFTY"
    underlying_config: UnderlyingConfig    # full index parameters
    expiry_date: str
    analysis_id: str
    triggered_by: str          # "user_request" | "regime_change" | "oi_shift"

    # Data layer
    snapshot: OptionChainSnapshot | None
    snapshot_age_sec: float
    data_valid: bool

    # Quant layer
    market_data: MarketDataModel | None
    regime: MarketRegime | None
    oi_signals: OISignals | None

    # Strategy layer
    candidates: list[StrategyCandidate]
    scored_candidates: list[ScoredCandidate]

    # LLM layer
    llm_recommendation: LLMRecommendation | None
    llm_validation_passed: bool
    prompt_version: str

    # Human gate
    human_decision: str | None       # "approve" | "reject" | None (pending)
    approved_strategy: ScoredCandidate | None
    recommendation_valid_until: datetime | None

    # Execution
    order_validation_result: OrderValidationResult | None
    execution_result: dict | None

    # Audit
    errors: list[str]
    warnings: list[str]
    no_trade_reason: str | None
```

---

## Sub-Tasks

---

### Sub-Task 1 — Project Scaffold & Dependencies

**Status:** `[ ] pending`

**Intent**
Create the new directory structure, install new dependencies, and add the `ai:` and `underlyings:` configuration sections to `config.yml`. This is the foundation all subsequent sub-tasks build on.

**Expected Outcomes**
- New directories `quant/`, `strategies/`, `ai/workflow/`, `ai/prompts/`, `ai/schemas/`, `data/providers/`, `persistence/` exist with `__init__.py` files
- `requirements.txt` updated with `langgraph`, `langchain`, `langchain-anthropic`, `langchain-openai`, `scipy`, `pandas-ta`
- `config.yml` has `ai:` section (LLM config) and `underlyings:` list (NIFTY, BANKNIFTY, FINNIFTY with `enabled` flags)
- `config.yml` has `toggles:` section with all default toggle states for all 4 levels
- `secrets_loader.py` updated to overlay `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from environment

**Todo List**
1. Create directory tree: `quant/`, `strategies/`, `ai/workflow/`, `ai/prompts/`, `ai/schemas/`, `data/providers/`, `data/normalizers/`, `persistence/` — each with empty `__init__.py`
2. Add to `requirements.txt`: `langgraph>=0.2.0`, `langchain>=0.2.0`, `langchain-anthropic>=0.1.0`, `langchain-openai>=0.1.0`, `scipy>=1.10.0`, `pandas-ta>=0.3.14b`
3. Add `ai:` block to `config.yml` with LLM provider, model, api_key_env, freshness_max_sec, recommendation_ttl_sec, stale_price_threshold_pct
4. Add `underlyings:` list to `config.yml` with NIFTY (enabled: true), BANKNIFTY (enabled: true), FINNIFTY (enabled: false) — each with strike_gap, lot_size, expiry_date, strikes_each_side
5. Add `toggles:` section to `config.yml` covering all Level 1–4 toggle defaults (data sources, quant components, strategy types, risk controls)
6. Update `secrets_loader.py` to overlay LLM API keys from env

**Relevant Context**
- Existing [`requirements.txt`](Bot-NSE-Options/requirements.txt)
- Existing [`config.yml`](Bot-NSE-Options/config.yml) `bot:` and `openalgo:` sections as style guide
- Existing [`secrets_loader.py`](Bot-NSE-Options/secrets_loader.py) for env-override pattern
- Platform Toggle System section above — all defaults must be represented in `toggles:` config block

---

### Sub-Task 2 — Data Models (Pydantic Schemas)

**Status:** `[ ] pending`

**Intent**  
Define all canonical Pydantic data models used across the platform. These are the single source of truth for data shapes — quant engine writes them, LLM contract is validated against them, execution layer reads from them.

**Expected Outcomes**
- `ai/schemas/market.py` — `OptionLeg`, `OptionStrike`, `OptionChainSnapshot`, `MarketDataModel`, `VolatilityData`
- `ai/schemas/regime.py` — `MarketRegime`, `TrendDirection`, `VolRegime`
- `ai/schemas/strategy.py` — `StrategyCandidate`, `StrategyLeg`, `ScoredCandidate`, `PayoffResult`
- `ai/schemas/recommendation.py` — `LLMRecommendation`, `OrderValidationResult`, `ExecutionRequest`
- `ai/schemas/state.py` — `AnalysisState` TypedDict (as shown in architecture summary above)
- `ai/schemas/settings.py` — `PlatformSettings` Pydantic model with all 4 toggle levels as typed fields

**Todo List**
1. Create `ai/schemas/market.py` with `OptionLeg`, `OptionStrike`, `OptionChainSnapshot` (includes `fetched_at: datetime`, `underlying_ltp: float`, `atm_strike: float`, `chain: list[OptionStrike]`, `freshness_status: str`)
2. Create `ai/schemas/regime.py` with `MarketRegime` (`trend`, `trend_strength`, `vol_regime`, `iv_regime`, `timeframe_alignment`, `no_trade: bool`, `no_trade_reason: str`)
3. Create `ai/schemas/strategy.py` with `StrategyCandidate` (type, legs with strike+action+option_type, credit/debit, max_profit, max_loss, breakevens, risk_reward, probability) and `ScoredCandidate` (extends with score, score_breakdown)
4. Create `ai/schemas/recommendation.py` with `LLMRecommendation` (market_view, confidence, selected_strategy, reasoning: list[str], risks: list[str], conflicts: list[str])
5. Create `ai/schemas/state.py` with `AnalysisState` TypedDict including `underlying_config: UnderlyingConfig` and `active_toggles: PlatformSettings`
6. Create `ai/schemas/settings.py` with `PlatformSettings` Pydantic model — all Level 1–4 toggles as `bool` fields with defaults matching `config.yml`

**Relevant Context**
- Architecture document Section 4 (Market Data Model), Section 15 (Structured LLM Contract)
- OpenAlgo `optionchain` response shape (from OpenAPI docs): `underlying_ltp`, `atm_strike`, `chain[].strike`, `chain[].ce/pe.{symbol, ltp, bid, ask, volume, oi, lotsize}`
- OpenAlgo `optiongreeks` response: `greeks.{delta, gamma, theta, vega}`, `implied_volatility`

---

### Sub-Task 3 — Data Ingestion Layer

**Status:** `[ ] pending`

**Intent**  
Build the `OptionChainProvider` that fetches a complete, timestamped option chain snapshot from OpenAlgo and validates data freshness. This is the single entry point for all market data — downstream layers never call OpenAlgo directly.

**Expected Outcomes**
- `data/providers/option_chain_provider.py` with `OptionChainProvider` class
- `fetch_snapshot(underlying, expiry, strike_count)` → `OptionChainSnapshot` with full metadata
- `fetch_vix()` → float (India VIX LTP via `trading_adapter.get_ltp`)
- `fetch_spot_history(underlying, timeframe, days)` → pd.DataFrame (wraps existing `scanner.fetch_history`)
- Freshness validation: `snapshot.fetched_at` vs `config.ai.freshness_max_sec`
- All errors fail-open with logged warnings (never crash the analysis cycle)

**Todo List**
1. Create `data/providers/option_chain_provider.py` with `OptionChainProvider` class that wraps `trading_adapter._get_oa_client`
2. Implement `fetch_snapshot()` — call `client.optionchain()`, normalize response into `OptionChainSnapshot`, attach `fetched_at=datetime.now()`
3. Implement `validate_freshness(snapshot)` → return `(bool, age_sec)` based on `config.ai.freshness_max_sec`
4. Implement `fetch_vix()` — reuse `trading_adapter.get_ltp(cfg, "INDIAVIX", exchange="NSE_INDEX")`
5. Implement `fetch_spot_history()` — thin wrapper around existing `scanner.fetch_history()`
6. Add unit tests in `tests/test_option_chain_provider.py` with mocked OpenAlgo responses

**Relevant Context**
- Existing [`trading_adapter.py`](Bot-NSE-Options/trading_adapter.py) — `_get_oa_client`, `get_ltp`, `get_quote`
- Existing [`scanner.fetch_history()`](Bot-NSE-Options/scanner.py:97) for spot OHLCV
- OpenAPI docs `OptionChain Example` — response shape (lines 1051-1242)
- Config: `openalgo.apikey`, `openalgo.base_url`, `options.underlying`, `options.expiry_date`

---

### Sub-Task 4 — Quant Engine

**Status:** `[ ] pending`

**Intent**  
Build the deterministic quantitative analysis engine. This is the most important layer — it must produce all numerical values before the LLM is ever invoked. The LLM receives only pre-computed signals, never raw chain data.

**Expected Outcomes**
- `quant/oi_analysis.py` — OI interpretation signals (call/put writing zones, PCR, OI concentration, support/resistance from OI)
- `quant/technicals.py` — regime indicators (VWAP, EMA20/50/200, RSI, ATR, trend direction per timeframe) reusing existing `signals.py` computations
- `quant/volatility.py` — IV rank, IV percentile, VIX percentile, expected move calculation
- `quant/greeks.py` — per-strike Greek fetching via `optiongreeks` API; portfolio-level Greek aggregation
- `quant/market_snapshot.py` — `build_market_data_model(snapshot, vix, spot_history, config)` → `MarketDataModel`
- `quant/regime_engine.py` — `classify_regime(market_data)` → `MarketRegime` (8 regimes + NO_TRADE)

**Todo List**
1. Create `quant/oi_analysis.py`: implement `analyze_oi(chain: OptionChainSnapshot)` → `OISignals` covering PCR, near-ATM PCR, call-writing zones, put-writing zones, OI concentration strikes, change-OI concentration, support/resistance candidates from OI
2. Create `quant/technicals.py`: implement `compute_technicals(df_spot, config)` → dict of VWAP, EMA20, EMA50, RSI, ATR, trend (reuse `signals.compute_utbot_signals` for ATR-trailing)
3. Create `quant/volatility.py`: implement `compute_iv_metrics(chain, vix)` → dict with ATM IV, IV rank (52-week), IV percentile, expected move (ATM_IV × spot × √(DTE/365)), VIX percentile
4. Create `quant/greeks.py`: implement `fetch_chain_greeks(chain, config)` — calls `client.optiongreeks` for ±5 ATM strikes; caches per analysis cycle
5. Create `quant/market_snapshot.py`: implement `build_market_data_model()` — assembles `MarketDataModel` from all quant outputs
6. Create `quant/regime_engine.py`: implement `classify_regime(market_data)` with configurable regime rules (trend score, vol regime, IV regime); must output `no_trade=True` when signals are contradictory

**Relevant Context**
- Architecture document Section 5 (OI Engine), Section 6 (Regime Engine)
- Existing [`signals.py`](Bot-NSE-Options/signals.py) — `compute_utbot_signals`, `compute_sr_signals`, `evaluate_composite_signals` (reuse for technicals)
- OpenAPI docs `OptionGreeks Example` (lines 1504-1540)
- Architecture Rule 13: system must be able to say "NO TRADE"

---

### Sub-Task 5 — Strategy Generation & Payoff Engine

**Status:** `[ ] pending`

**Intent**  
Generate the finite universe of candidate option strategies from the chain snapshot. All payoff calculations are deterministic Python — the LLM never calculates P&L. Only strikes present in the actual chain are used.

**Expected Outcomes**
- `strategies/generator.py` — generates `list[StrategyCandidate]` for all applicable strategy types given market conditions
- `strategies/payoff.py` — deterministic payoff calculation for all 8 strategy types
- `strategies/scoring.py` — transparent weighted score model (configurable weights) producing `list[ScoredCandidate]`
- 8 strategy types implemented: Long Call, Long Put, Bull Call Spread, Bear Put Spread, Bull Put Spread, Bear Call Spread, Iron Condor, Short Strangle / Iron Fly, Long Straddle

**Todo List**
1. Create `strategies/payoff.py` with functions for each strategy type: `calc_bull_put_spread(chain, short_strike, long_strike, lots)` etc. Each returns `PayoffResult(credit, max_profit, max_loss, breakevens, risk_reward, probability_profit)`
2. Create `strategies/generator.py` with `generate_candidates(chain, regime, config)` → `list[StrategyCandidate]`. Must enforce: only strikes present in chain, validate bid/ask spread, validate minimum OI per leg, apply regime filter (no long calls in strong bearish, etc.)
3. Create `strategies/scoring.py` with `score_candidates(candidates, market_data, regime, config)` → `list[ScoredCandidate]`. Implement the 9-factor score (trend alignment 20%, OI structure 15%, IV environment 10%, expected-move alignment 10%, risk/reward 15%, liquidity 10%, probability 10%, event risk 5%, slippage 5%). Weights must be in config.
4. Write `tests/test_payoff.py` with payoff calculation correctness tests (known inputs → known outputs)

**Relevant Context**
- Architecture document Section 7 (Strategy Types), Section 8 (Candidate Evaluation), Section 9 (Scoring)
- Architecture critical rule: LLM must not invent strikes not in chain
- OpenAlgo `optionsmultiorder` API — execution uses offsets, but generation uses actual strikes from snapshot
- Existing [`position_sizer.py`](Bot-NSE-Options/position_sizer.py) for lot size logic

---

### Sub-Task 6 — LangGraph Workflow

**Status:** `[ ] pending`

**Intent**  
Build the LangGraph stateful workflow that orchestrates the full analysis pipeline from data ingestion to human approval gate. This replaces the existing linear scanner loop for the AI-assisted path.

**Expected Outcomes**
- `ai/workflow/graph.py` — compiled `StateGraph` with all nodes and edges
- `ai/workflow/nodes.py` — individual node functions (pure functions that take/return `AnalysisState`)
- `ai/workflow/llm_node.py` — LangChain LLM invocation with structured output parsing and validation
- `ai/prompts/analysis_prompt.py` — versioned system + human prompt templates (v1.0)
- Human-in-the-loop interrupt at `wait_for_human` node using LangGraph `interrupt()`

**Todo List**
1. Create `ai/workflow/nodes.py` — implement each node as a pure function: `load_snapshot_node`, `validate_freshness_node`, `run_quant_node`, `classify_regime_node`, `generate_candidates_node`, `calculate_payoff_node`, `score_candidates_node`, `validate_strategy_node`, `build_recommendation_node`
2. Create `ai/workflow/llm_node.py` — implement `llm_interpretation_node`: build structured prompt from `AnalysisState.scored_candidates` + `market_data`, invoke LLM via LangChain with `structured_output(LLMRecommendation)`, validate response (must reference only chain strikes, must have reasoning, confidence 0-100)
3. Create `ai/prompts/analysis_prompt.py` — system prompt emphasizing: LLM receives only pre-computed data, must not invent strikes, must explain reasoning from supplied signals. Include `PROMPT_VERSION = "1.0"` constant.
4. Create `ai/workflow/graph.py` — assemble `StateGraph(AnalysisState)`, add all nodes, define edges including conditional branches (freshness fail → error, regime NO_TRADE → end, LLM validation fail → retry or error), add `interrupt()` at human gate
5. Add graph-level error handling: any node exception sets `state.errors` and routes to a `handle_error` terminal node (never surfaces unhandled exceptions to the API layer)

**Relevant Context**
- Architecture document Section 11 (LangGraph Workflow), Section 10 (LLM Responsibility), Section 12 (Human-in-the-Loop)
- Architecture Section 15 (Structured LLM Contract) — the JSON output schema
- LangGraph interrupt pattern for human-in-the-loop checkpoints
- Config `ai.provider`, `ai.model`, `ai.prompt_version`

---

### Sub-Task 7 — Recommendation Service & Persistence

**Status:** `[ ] pending`

**Intent**  
Build the recommendation session manager that stores each analysis run end-to-end (for audit and backtesting). Implement recommendation TTL/invalidation logic.

**Expected Outcomes**
- `persistence/recommendation_db.py` — SQLite table `recommendations` with all audit fields
- `persistence/snapshot_db.py` — SQLite table `market_snapshots` (store chain snapshot for backtesting replay)
- `ai/workflow/recommendation_service.py` — `RecommendationService` that triggers the LangGraph workflow, resumes it after human decision, persists all events
- Recommendation TTL: configurable `recommendation_ttl_sec` (default 90s) + `stale_price_threshold_pct` (default 0.3%)

**Todo List**
1. Create `persistence/recommendation_db.py`: define and init `recommendations` table (id, analysis_id, timestamp, underlying, expiry, market_snapshot_id, quant_engine_version, prompt_version, llm_provider, llm_model, llm_response_json, selected_strategy_json, candidate_set_json, human_decision, execution_details_json, outcome_json)
2. Create `persistence/snapshot_db.py`: define and init `market_snapshots` table (id, timestamp, underlying, expiry, atm_strike, spot_ltp, vix, chain_json, technicals_json)
3. Create `ai/workflow/recommendation_service.py` with `RecommendationService` class: `async run_analysis(underlying, expiry, trigger)` that invokes the graph, `async approve(analysis_id, lots)` that resumes the graph with approval, `async reject(analysis_id)` that resumes with rejection, TTL check before resume
4. Implement recommendation freshness check in `approve()`: re-fetch current spot, compare against `snapshot.underlying_ltp`; if change > `stale_price_threshold_pct` or TTL expired, return `STALE` error requiring re-analysis

**Relevant Context**
- Architecture document Section 17 (Historical Data), Section 19 (Observability & Audit)
- Existing [`trade_db.py`](Bot-NSE-Options/trade_db.py) as pattern for SQLite init and CRUD
- Architecture Rule 9: every recommendation must be persisted
- Architecture Rule 15: stale market snapshot invalidates recommendation

---

### Sub-Task 8 — Order Validation & Execution Layer

**Status:** `[ ] pending`

**Intent**  
Implement the order safety layer that runs between human approval and broker submission. Use OpenAlgo's `optionsmultiorder` for multi-leg strategies. Reuse existing risk_manager and trading_adapter.

**Expected Outcomes**
- `execution/order_validator.py` — pre-flight validation (market open, quote freshness, margin, position limits, duplicate check, quantity validation)
- `execution/multi_leg_executor.py` — maps strategy legs to `optionsmultiorder` call; handles single-leg fallback via `optionsorder`
- All 13 safety checks from architecture Section 13 implemented
- Execution result persisted and Telegram alert sent (reuse existing `telegram.py`)

**Todo List**
1. Create `execution/order_validator.py` with `validate_order(strategy, config)` implementing all safety checks: market open (reuse `risk_manager`), quote freshness (re-fetch LTP, compare vs snapshot), available margin (if accessible via OpenAlgo funds API), quantity/lot size validation, duplicate position check, position limit check, user confirmation token verification
2. Create `execution/multi_leg_executor.py` with `execute_strategy(approved_strategy, config)`: map `StrategyCandidate.legs` to `optionsmultiorder` legs format (derive offsets from strike distance to ATM), call `client.optionsmultiorder()`, handle partial fill errors, persist execution result
3. Implement `execution/leg_offset_mapper.py` — converts absolute strike to ATM offset string (e.g., strike 24900 with ATM 25000 → "OTM2") for use with `optionsmultiorder`
4. Wire execution result back into `RecommendationService` and persist to `recommendations.execution_details_json`
5. Send Telegram notification on execution (reuse existing [`telegram.py`](Bot-NSE-Options/telegram.py))

**Relevant Context**
- Architecture document Section 13 (Order Safety Layer)
- Existing [`risk_manager.py`](Bot-NSE-Options/risk_manager.py) — `check_market_hours`, `check_kill_switch`
- Existing [`trading_adapter.py`](Bot-NSE-Options/trading_adapter.py) — `_get_oa_client`, `place_order` (extend for multi-leg)
- OpenAPI docs `OptionsMultiOrder Example` (lines 248-370) — `client.optionsmultiorder(legs=[...])`
- Existing [`telegram.py`](Bot-NSE-Options/telegram.py) for alerts

---

### Sub-Task 9 — FastAPI Routes

**Status:** `[ ] pending`

**Intent**  
Add new API routes to the existing FastAPI app for the AI analysis workflow. The existing dashboard routes (`/api/scan`, `/api/trades`, etc.) remain untouched.

**Expected Outcomes**
- `POST /api/ai/analyze` — trigger new analysis run, returns `analysis_id`
- `GET /api/ai/recommendation/{analysis_id}` — returns full recommendation (strategies, regime, LLM output)
- `POST /api/ai/approve/{analysis_id}` — human approval; triggers order validation + execution
- `POST /api/ai/reject/{analysis_id}` — records rejection
- `GET /api/ai/history` — list of past recommendations with outcomes
- `GET /api/ai/chain` — current option chain snapshot (for dashboard display)
- WebSocket `/ws/ai/stream` — real-time updates during analysis (regime, candidates, LLM thinking)

**Todo List**
1. Create `api/ai_routes.py` with all AI endpoint handlers; import and register with existing `app` in `app.py` via `app.include_router(ai_router, prefix="/api/ai")`
2. Implement `POST /api/ai/analyze` — accepts `{underlying, expiry_date, trigger_source}`, validates config, calls `RecommendationService.run_analysis()` async, returns `{analysis_id, status: "running"}`
3. Implement `GET /api/ai/recommendation/{analysis_id}` — queries `recommendation_db`, returns full payload including top 3 candidates, LLM reasoning, expiry countdown, `is_stale` flag
4. Implement `POST /api/ai/approve/{analysis_id}` — calls `RecommendationService.approve()`, runs order validation, if valid calls `execute_strategy()`, returns execution result
5. Implement `POST /api/ai/reject/{analysis_id}` — records rejection reason, updates recommendation record
6. Implement `GET /api/ai/history` — paged list of past recommendations with P&L outcomes where available
7. Implement `GET /api/ai/chain` — returns current `OptionChainSnapshot` for display
8. Implement WebSocket `/ws/ai/stream` for live analysis progress updates

**Relevant Context**
- Existing [`app.py`](Bot-NSE-Options/app.py) — FastAPI app structure, existing router patterns
- Architecture document Section 14 (FastAPI Integration)
- Existing route patterns (e.g., `/api/scan`, `/api/signals`) as style guide

---

### Sub-Task 10 — Dashboard Frontend

**Status:** `[ ] pending`

**Intent**  
Extend the existing dark-mode dashboard with a new "AI Strategy" tab that displays regime analysis, ranked strategy recommendations, payoff charts, LLM reasoning, and the approve/reject controls. Do not modify existing scanner tab behavior.

**Expected Outcomes**
- New "AI Strategy" tab in [`frontend/index.html`](Bot-NSE-Options/frontend/index.html) alongside existing tabs
- Regime panel: trend direction, timeframe alignment, vol regime, IV environment
- Strategy cards: top 3 candidates with payoff visualization, score breakdown, risk metrics
- LLM reasoning panel: market view, confidence bar, reasoning bullets, risk warnings
- Approve / Reject buttons with countdown timer showing recommendation TTL
- Option chain table display with OI concentration heatmap
- All new UI uses existing CSS variables and dark-mode theme from [`frontend/index.css`](Bot-NSE-Options/frontend/index.css)

**Todo List**
1. Add "AI Strategy" tab button to tab bar in `index.html`; add corresponding tab content panel `<div id="tab-ai-strategy">`
2. Build regime panel: 5-timeframe trend alignment grid, trend strength bar, vol/IV regime badges
3. Build strategy cards component: card per candidate showing type, legs (symbol + action + price), credit/debit, max profit/loss, breakeven, score bar, score factor breakdown on hover
4. Build LLM reasoning panel: market_view badge, confidence meter, reasoning list, risks list, conflicts warning box
5. Build option chain mini-table (ATM ±5 strikes): CE OI | CE LTP | Strike | PE LTP | PE OI with color intensity proportional to OI concentration
6. Build **Toggle Control Panel** (collapsible sidebar/panel on AI tab): 4-section accordion — Index & Data / Quant Engine / Strategy Types / Risk & Execution. Each toggle is a styled ON/OFF pill switch. Calls `POST /api/ai/settings/toggle` on change. Shows "Reset to Defaults" button.
7. Index selector: three-button radio group (NIFTY | BANKNIFTY | FINNIFTY-dim). Selecting triggers new analysis run automatically.
8. Add approve/reject controls: "Approve & Place Order" button (disabled when stale/TTL expired, shows countdown), "Reject" button, confirmation modal showing exact legs + estimated fills + margin
9. Add WebSocket listener in `index.js` to receive real-time analysis progress and update UI

**Relevant Context**
- Existing [`frontend/index.html`](Bot-NSE-Options/frontend/index.html) for tab structure and HTML patterns
- Existing [`frontend/index.css`](Bot-NSE-Options/frontend/index.css) for CSS variables, theme, component patterns
- Existing [`frontend/index.js`](Bot-NSE-Options/frontend/index.js) for API call patterns, WebSocket usage
- Platform Toggle System section in this plan — all 4 levels must be represented in the UI

---

### Sub-Task 10a — Settings Service (Toggle Backend)

**Status:** `[ ] pending`

**Intent**
Build the backend settings service that persists toggle state to SQLite, loads defaults from `config.yml`, and serves toggle state to the frontend. This is a supporting service used by all other components via `PlatformSettings`.

**Expected Outcomes**
- `persistence/settings_db.py` — SQLite `settings` table CRUD (key-value store for toggle state)
- `ai/workflow/settings_service.py` — `SettingsService` class: `get_settings()`, `update_toggle(key, value)`, `reset_to_defaults()`, `get_active_underlyings()`
- Settings loaded into `AnalysisState.active_toggles` at the start of every analysis run
- Quant engine components skip their computation when their toggle is `false`
- Strategy generator skips disabled strategy types
- Scoring weight redistribution logic when quant components are disabled

**Todo List**
1. Create `persistence/settings_db.py`: init `settings` table (key TEXT PK, value TEXT, updated_at TEXT); implement `get_all()`, `set(key, value)`, `reset_to_defaults(config)` functions
2. Create `ai/workflow/settings_service.py` with `SettingsService` class: on init, load defaults from `config.yml toggles:` into DB if not already present; `get_settings()` returns `PlatformSettings`; `update_toggle(key, value)` persists and returns updated `PlatformSettings`
3. Add `load_settings_node` as first node in LangGraph graph — loads current `PlatformSettings` into `state.active_toggles`
4. Update quant engine functions to accept `active_toggles: PlatformSettings` and skip disabled components (return `None` for disabled components, not errors)
5. Update scoring engine to redistribute weights when components are disabled — `effective_weights = redistribute(base_weights, active_toggles)`
6. Update strategy generator to filter out disabled strategy types from candidate generation

**Relevant Context**
- Existing [`trade_db.py`](Bot-NSE-Options/trade_db.py) as SQLite init/CRUD pattern
- `ai/schemas/settings.py` `PlatformSettings` Pydantic model (from Sub-Task 2)
- `config.yml toggles:` section (from Sub-Task 1) as the defaults source
- Settings API endpoints in Sub-Task 9: `GET /api/ai/settings`, `POST /api/ai/settings/toggle`, `POST /api/ai/settings/reset`

---

### Sub-Task 11 — Configuration & Documentation Update

**Status:** `[ ] pending`

**Intent**  
Update `config.yml` with all new AI platform configuration sections. Update `README.md` with the new platform overview, setup instructions (including LLM API keys), and architecture diagram reference.

**Expected Outcomes**
- `config.yml` fully documented `ai:` section with LLM provider settings, strategy scoring weights, regime thresholds
- `requirements.txt` finalized with all new dependencies pinned
- `README.md` updated with new platform description, setup steps, how to run

**Todo List**
1. Add full `ai:` section to `config.yml`: provider, model, api_key_env, freshness_max_sec, recommendation_ttl_sec, stale_price_threshold_pct, scoring_weights (9 factors)
2. Add `strategies:` section to `config.yml` with enabled strategy list and per-strategy parameters (min OTM distance, max spread pct)
3. Add `regime:` section to `config.yml` with configurable thresholds (trend score cutoffs, VIX high/low thresholds)
4. Update `requirements.txt` with pinned versions
5. Update `README.md` sections: platform overview, architecture summary, setup (including `pip install`, LLM key env vars), how to run, how to use AI strategy tab

**Relevant Context**
- Existing [`config.yml`](Bot-NSE-Options/config.yml) for formatting and documentation style
- Existing [`README.md`](Bot-NSE-Options/README.md) for document style

---

## Architectural Rules Cross-Reference

| Rule | Where Enforced |
|------|---------------|
| LLM is not the market-data source | Sub-Task 6 — LLM node receives only pre-computed `AnalysisState` |
| LLM is not the calculator | Sub-Task 5 — all payoff calculated before LLM invoked |
| Python is quant source of truth | Sub-Task 4 — quant engine runs before any LLM call |
| All payoffs calculated deterministically | Sub-Task 5 — `strategies/payoff.py` |
| Only available strikes recommended | Sub-Task 5 — generator filters to chain strikes only |
| Every recommendation has timestamp + TTL | Sub-Task 7 — `recommendation_valid_until` field |
| Human approval mandatory | Sub-Task 6 — `interrupt()` at human gate |
| Broker execution isolated | Sub-Task 8 — `execution/` layer |
| Every recommendation persisted | Sub-Task 7 — `recommendation_db.py` |
| No "guaranteed profit" labelling | Sub-Task 6 — prompt instructions forbid it |
| System can say NO TRADE | Sub-Task 4 — `regime_engine` NO_TRADE output |
| LLM prompts versioned | Sub-Task 6 — `PROMPT_VERSION` constant |
| Stale snapshot invalidates recommendation | Sub-Task 7 — TTL + price delta check in `approve()` |

---

## Reused Existing Modules

| Existing Module | Reused In |
|-----------------|-----------|
| `trading_adapter._get_oa_client` | Sub-Task 3 — data provider |
| `trading_adapter.get_ltp` | Sub-Task 3 — VIX fetch; Sub-Task 8 — freshness check |
| `trading_adapter.get_quote` | Sub-Task 8 — pre-execution quote |
| `scanner.fetch_history` | Sub-Task 3 — spot OHLCV |
| `signals.compute_utbot_signals` | Sub-Task 4 — technicals |
| `signals.compute_sr_signals` | Sub-Task 4 — support/resistance |
| `risk_manager.check_market_hours` | Sub-Task 8 — order validation |
| `risk_manager.check_kill_switch` | Sub-Task 8 — order validation |
| `broker_retry.with_retry` | Sub-Task 3, 8 — all broker calls |
| `trade_db` (SQLite pattern) | Sub-Task 7 — recommendation DB |
| `telegram.send_telegram_alert` | Sub-Task 8 — execution alerts |
| `logging_setup`, `metrics` | All sub-tasks |
| `secrets_loader.apply_env_overrides` | Sub-Task 1 — LLM API keys |

---

## Implementation Order

Sub-tasks should be implemented in this order (each can proceed after the prior is complete and confirmed):

1. → Sub-Task 1 (Scaffold)
2. → Sub-Task 2 (Data Models)
3. → Sub-Task 3 (Data Ingestion)
4. → Sub-Task 4 (Quant Engine)
5. → Sub-Task 5 (Strategy Engine)
6. → Sub-Task 6 (LangGraph Workflow)
7. → Sub-Task 7 (Recommendation Service)
8. → Sub-Task 8 (Order Execution)
9. → Sub-Task 9 (FastAPI Routes)
10. → Sub-Task 10 (Dashboard Frontend)
11. → Sub-Task 11 (Config & Docs)
