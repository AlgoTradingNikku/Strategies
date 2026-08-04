# How the Options Scanner Works

## Overview

The scanner runs a **three-stage pipeline** for each enabled underlying (NIFTY, BANKNIFTY). Every stage must pass before the next one runs. A single scan cycle can produce at most **4 signals** — one CE and one PE per underlying.

---

## Stage 1 — Underlying Chart Signal

The selected engine (UTBot and/or SR Lines) runs on the **NIFTY / BANKNIFTY index chart** (not the option chart yet). This generates a directional signal:

- **BUY signal** on the index → look for a **Call (CE)** option
- **SELL signal** on the index → look for a **Put (PE)** option

If the composite score does not meet the **Gate 1 threshold** (`min_underlying_score`, default 60), the pipeline stops here for that direction.

Filters applied at this stage (on the index chart):

| Filter | Config Key | Description |
|---|---|---|
| Multi-Timeframe (MTF) | `mtf_filter_enabled` | Confirms trend direction on a higher timeframe |
| EMA | `ema_filter_enabled` | Rejects signals on the wrong side of EMA 200 |
| ADX | `adx_filter_enabled` | Requires minimum trend strength |
| RSI | `rsi_filter_enabled` | Requires momentum to be within acceptable range |
| Volume | `volume_filter_enabled` | Rejects low-volume underlying candles |

---

## Stage 2 — Strike Selection + Option Filters

This stage selects **exactly one contract** per signal — not multiple. The selection method is configured via `strike_selection.method`:

| Method | How it selects |
|---|---|
| `ATM` | Strike closest to the current spot price |
| `OTM` | N strikes out-of-the-money (`otm_strikes`) |
| `ITM` | N strikes in-the-money (`itm_strikes`) |
| `PREMIUM` | Strike whose LTP falls within `premium_min`–`premium_max`, closest to ATM |
| `LIQUIDITY` | Highest OI × Volume within ±5 strikes of ATM |
| `TREND` | 1 strike ITM for direction-aligned higher-delta trade (`trend_itm_offset`) |
| `DELTA` | Strike whose option delta is closest to `target_delta` (e.g. 0.40) |

After the contract is selected, hard filters are applied (OI minimum, volume minimum). If the contract fails these, the pipeline stops.

Option-specific **scoring adjustments** are then added on top of the Stage 1 score:

| Adjustment | What it does |
|---|---|
| IV score | Penalises high implied volatility — expensive to buy |
| OI momentum | Rewards rising OI + rising premium (conviction signal) |
| Time decay penalty | Penalises contracts with fewer than `time_decay_threshold_days` days to expiry |
| Candle pattern | Rewards hammer / engulfing patterns near S/R zones on the index chart |

If the **combined score** does not meet Gate 2 (`min_alert_score`, default 60), the pipeline stops here.

---

## Stage 3 — Option Premium Chart Confirmation *(optional)*

When `option_chart_confirmation.enabled: true`, UTBot runs on the **selected option contract's own premium chart** — the NFO OHLCV of the contract itself (e.g. `NIFTY28AUG2524500CE`).

This is a **confirmation check on the single selected contract**, not a scan of multiple contracts:

| Outcome | Effect |
|---|---|
| Premium chart **confirms** the direction | Bonus points added (`confirmation_bonus_pts`, default +15) |
| Premium chart **contradicts** | Penalty applied (`contradiction_penalty_pts`, default −15) |
| Mode = `strict` | Contradiction is a hard reject regardless of score |
| Mode = `score_only` | Contradiction only reduces the score, does not hard reject |

---

## Final Gate — Deduplication

Before saving, a deduplication check suppresses the same `(symbol, direction)` pair if it was already signalled within `scan_dedup_window_seconds` (default 15 minutes). This prevents repeated Telegram alerts and duplicate auto-orders on consecutive scan cycles.

---

## Result

If the final score clears all gates, **one signal card** is saved to the database and displayed on the dashboard. The maximum output per scan cycle is:

| Underlying | CE (BUY on index) | PE (SELL on index) |
|---|---|---|
| NIFTY | 1 signal | 1 signal |
| BANKNIFTY | 1 signal | 1 signal |

**Maximum 4 signals per scan cycle** (when both sides and both underlyings are enabled and all stages pass).

---

## Score Composition

The final confidence score is built from:

| Component | Source | Typical Range |
|---|---|---|
| Underlying trend score | UTBot + SR Lines (Stage 1) | 0 – 100 |
| IV penalty | High IV → penalise buying | −15 to +5 |
| OI momentum | Rising OI + rising premium | −10 to +10 |
| Time decay penalty | Days to expiry | −100 to 0 |
| Candle pattern | Reversal candle near S/R zone | −5 to +8 |
| Stage 3 confirmation | UTBot on option premium chart | −15 to +15 |

Final score is clamped to **[0, 100]**. Only signals ≥ `min_alert_score` are saved and displayed.

---

## Configuration Keys (Quick Reference)

```yaml
min_underlying_score: 60          # Gate 1 — Stage 1 minimum score
filters:
  min_alert_score: 60             # Gate 2 — final combined score minimum
scan_dedup_window_seconds: 900    # Gate 3 — duplicate suppression window (15 min)

strike_selection:
  method: "ATM"                   # ATM / OTM / ITM / PREMIUM / LIQUIDITY / TREND / DELTA
  expiry_preference: "WEEKLY"     # WEEKLY / MONTHLY / NEXT_WEEKLY / NEXT_MONTHLY
  scan_both_sides: true           # Scan CE and PE simultaneously

option_chart_confirmation:
  enabled: true
  mode: "score_only"              # "strict" = hard reject | "score_only" = penalty only
```
