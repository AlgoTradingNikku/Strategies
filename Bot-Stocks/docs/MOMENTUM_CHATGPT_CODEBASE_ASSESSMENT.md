# Momentum-ChatGPT Strategy — Codebase Assessment & Integration Plan

## 1. Executive Summary
This document fulfills Phase 0 (Mandatory Codebase Discovery) for integrating the **Momentum-ChatGPT** strategy engine into `Bot-Stocks`.

`Bot-Stocks` is an existing modular trading application. The `Momentum-ChatGPT` engine will be added as a **new registered strategy engine** coexisting with `UT Bot`, `S/R Channels`, `Momentum Engine`, and `Mean Reversion Engine`.

In `Bot-Stocks`, strategy engines can run **independently** (when only that engine is enabled) or **combinedly** (when multiple engines are enabled).

---

## 2. Codebase Discovery & Reuse Matrix

| Component | Existing `Bot-Stocks` Location | Reuse | Extend | New | Technical Description / Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Engine Registry** | `signals.py::ENGINE_REGISTRY` | | **X** | | Register `"momentum_chatgpt"` strategy dictionary with toggles & subcomponents. |
| **Configuration** | `config.yml` | | **X** | | Add `momentum_chatgpt:` config block with configurable parameters (risk, scores, setups). |
| **Historical & Live Market Data** | `scanner.py`, `yfinance`/`OpenAlgo` layer | **X** | | | Reuse existing daily/intraday OHLCV fetching and incremental caching infrastructure. |
| **Benchmark & Index Data** | NIFTY 50 (`^NSEI`), NIFTY 200 (`^CNX200`), Sectors | | **X** | | Extend scanner data loading to fetch NIFTY 200 & 12 sector indices (`^CNXIT`, `^CNXBANK`, etc.). |
| **Technical Indicators** | `signals.py` | **X** | | | Reuse vectorized `EMA`, `RSI`, `ADX`, `MACD`, `ATR`, `Bollinger Bands` functions. |
| **Market Regime & Breadth** | New strategy module | | | **X** | Calculate NIFTY 50 trend/EMAs and NIFTY 200 breadth (% stocks above EMA20/50/200). |
| **Sector Strength Ranking** | New strategy module | | | **X** | Calculate 5D/20D/60D momentum, RS vs NIFTY 200, trend & breadth across 12 sectors. |
| **Stock Momentum & Setups** | New strategy module | | | **X** | Detect `BREAKOUT`, `PULLBACK_RETEST`, and `CONSOLIDATION_CONTINUATION` setups + R:R calculation. |
| **Scoring & Ranking** | New strategy module | | | **X** | Multi-factor composite score (0–100) multiplied by Market Regime score multiplier. |
| **Position Sizing & Risk** | `rules_engine.py` / `monitor.py` | **X** | **X** | | Risk-per-trade (0.50% default on ₹10L) with position value cap (15%). |
| **Portfolio Selection** | New strategy module | | | **X** | Sequential constraints: max 8 positions, max 30% sector, min 20% cash, 60D return correlation cap. |
| **Database Storage** | `signal_db.py`, `trade_db.py` | **X** | | | Store signals, candidate rejection reasons, setup metadata, and position lifecycle in SQLite. |
| **FastAPI Backend** | `app.py` | | **X** | | Support engine toggle endpoints (`/api/engines`, `/api/quick-filters`) and strategy status. |
| **Dashboard UI** | `frontend/index.html`, `frontend/index.js` | | **X** | | Add sidebar toggle under Quick Filters, Configuration Modal tab, and strategy signal badge. |
| **Scheduler & Execution** | `scanner.py`, `monitor.py` | **X** | | | Run in `READ_ONLY` / `PAPER` mode during initial implementation (no live order placement). |

---

## 3. Strategy Independence & Backward Compatibility

1. **Isolation**:
   - Existing engines (`UT Bot`, `S/R Channels`, `Momentum Engine`, `Mean Reversion Engine`) remain **100% untouched** in their core calculation logic.
   - All tests in `tests/` continue to run and pass without modification.

2. **Engine Toggling**:
   - Setting `momentum_chatgpt.enabled: true` in `config.yml` (or via Dashboard Quick Filters) activates the engine.
   - Turning off other engines lets `Momentum-ChatGPT` run completely standalone.
   - When combined with other engines in multi-engine mode, `evaluate_composite_signals()` evaluates all active engines in unison.

3. **Execution Safety**:
   - Initial implementation runs in `READ_ONLY` / `PAPER` mode. Live order submission is disabled until explicitly enabled after paper observation.
