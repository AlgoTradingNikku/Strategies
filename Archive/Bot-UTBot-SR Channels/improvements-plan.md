# Bot-UTBot-SR Channels — High-Impact Improvements Plan

## Overview

This plan addresses the 5 high-impact issues identified during code review.
Each sub-task is independent and can be implemented and reviewed one at a time.
No changes touch the Pine Script replication logic — all fixes are in the
Python infrastructure layer.

**Files in scope:**
- `signals.py` — sub-tasks 1, 2, 4
- `signal_db.py` — sub-task 3
- `nse_indices.py` — sub-task 5

---

## Sub-Tasks

---

### Sub-Task 1 — Vectorise SR Zone Touch Counting (Performance)

**Status:** [x] done

**Intent:**
The touch-count inner loop in `_cluster_sr_zones()` (lines 253–265 of `signals.py`)
iterates every bar in the loopback window for every raw zone. With ~290 loopback
bars and up to P² raw zones, this is the dominant cost on every symbol scan.
This sub-task replaces the nested Python loop with a vectorised NumPy operation.

**Root Cause:**
```
for z in raw_zones:                      # O(Z)
    for bar_i in range(start_idx, end_idx):  # O(B) — ~290 iterations
        if (lo <= h <= hi) or (lo <= l <= hi):
            touches += 1
```
With 50 raw zones × 290 bars = 14,500 iterations per symbol, repeated for
every stock in the segment.

**Expected Outcomes:**
- The inner touch-count loop is replaced with a vectorised NumPy boolean mask.
- For each raw zone `(hi, lo)`, the check becomes a single array comparison:
  `((high_slice >= lo) & (high_slice <= hi)) | ((low_slice >= lo) & (low_slice <= hi))`
  and `.sum()` gives the touch count.
- Correctness is identical — only the implementation changes.
- Measurable speedup on large segments (NIFTY200, NIFTY500).

**Todo List:**
1. In `_cluster_sr_zones()`, extract `high_arr[start_idx:end_idx]` and
   `low_arr[start_idx:end_idx]` as NumPy slices once, before the zone loop.
2. Replace the inner `for bar_i` loop with:
   `touches = int(((h_slice >= z_lo) & (h_slice <= z_hi) | (l_slice >= z_lo) & (l_slice <= z_hi)).sum())`
3. Verify the start/end index slice logic is identical to the original
   (`max(0, loopback_end_idx - loopback)` to `min(loopback_end_idx + 1, len(high_arr))`).
4. Run a quick smoke test: call `compute_sr_signals` on RELIANCE.NS 15m data
   and confirm zone count and zone boundaries are unchanged versus the original.

**Relevant Context:**
- Function: `_cluster_sr_zones` in `signals.py` lines 194–295
- Caller: `compute_sr_signals` line 382 — no signature change needed
- The zone expansion loop (Step 1, O(P²)) is left unchanged; with a typical
  loopback of 290 bars and pivot_period=10, P rarely exceeds ~30, so the
  P² expansion cost is already small (~900 iterations vs 14,500 for touch count)

---

### Sub-Task 2 — Fix RSI Calculation to Use Wilder's Smoothing

**Status:** [x] done

**Intent:**
The RSI in `evaluate_composite_signals()` uses `.rolling(rsi_period).mean()`
(Simple Moving Average) for gain and loss. The standard RSI (Wilder, 1978) and
TradingView both use exponential smoothing with alpha=1/period. This causes RSI
values to diverge from TradingView by several points, making any RSI filter
thresholds set against TV charts produce different results in the bot.

**Root Cause (signals.py lines 752–756):**
```python
gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()   # SMA — WRONG
loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()  # SMA — WRONG
```

The ATR and ADX calculations in the same file already use the correct pattern:
`tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()`

**Expected Outcomes:**
- RSI values match TradingView output for the same symbol and period.
- The fix is a 2-line change — no structural changes.
- RSI scoring in `evaluate_composite_signals()` and the optional RSI hard filter
  both benefit automatically.

**Todo List:**
1. In `evaluate_composite_signals()` (around lines 752–756), replace:
   `gain = ... .rolling(rsi_period).mean()`
   `loss = ... .rolling(rsi_period).mean()`
   with:
   `gain = ... .ewm(alpha=1.0/rsi_period, adjust=False).mean()`
   `loss = ... .ewm(alpha=1.0/rsi_period, adjust=False).mean()`
2. Keep the `rs = gain / (loss + 1e-10)` and `rsi = 100 - (100 / (1 + rs))` lines unchanged.
3. Verify on RELIANCE.NS daily data that RSI(14) now matches TradingView to within 0.5 points.

**Relevant Context:**
- Function: `evaluate_composite_signals` in `signals.py` lines 748–756
- Reference for correct pattern: `compute_utbot_signals` line 87 and `compute_adx` line 548
- RSI is used in two places in `evaluate_composite_signals`: the optional RSI hard filter
  (lines 862–874) and the RSI momentum scoring (lines 953–964)

---

### Sub-Task 3 — Fix Outcome Checking to Use OHLC High/Low

**Status:** [x] done

**Intent:**
`check_outcomes()` in `signal_db.py` fetches a DataFrame and checks only
`df["close"].iloc[-1]` to determine if a target or stop was hit. Between the
signal time and the outcome check (up to 4+ hours later), the price may have
touched the target intrabar but closed away from it. Using only the closing
price systematically undercounts target hits and stop hits, making the
Performance Stats panel unreliable.

**Root Cause (signal_db.py lines 154–175):**
```python
current_price = float(df["close"].iloc[-1])   # Only latest close used

if target is not None and current_price >= target:
    hit_target = 1
if stop_loss is not None and current_price <= stop_loss:
    hit_stop = 1
```

The `df` returned by `fetch_history()` has full OHLCV columns including `high`
and `low` on every candle. The signal timestamp is stored in the `timestamp`
column of the signals table.

**Expected Outcomes:**
- For candles that occurred after the signal timestamp, use `df["high"].max()`
  to check if any candle's high reached or exceeded the target (for BUY).
- Use `df["low"].min()` to check if any candle's low touched or breached the
  stop loss level (for BUY). Mirror logic for SELL.
- The `outcome_price` field is updated to the relevant extreme (high or low)
  rather than just the latest close, giving more meaningful tracking data.
- `outcome_pnl_pct` is still computed from the current close (last row), which
  is appropriate for an unrealised P&L view.

**Todo List:**
1. In `check_outcomes()`, after fetching `df`, filter to only rows after the
   signal timestamp:
   `sig_time = pd.to_datetime(row["timestamp"])`
   `post_df = df[df.index > sig_time]`
   If `post_df` is empty, skip this signal (data not yet available).
2. For BUY: `hit_target = 1` if `post_df["high"].max() >= target`.
   `hit_stop = 1` if `post_df["low"].min() <= stop_loss`.
3. For SELL: `hit_target = 1` if `post_df["low"].min() <= target`.
   `hit_stop = 1` if `post_df["high"].max() >= stop_loss`.
4. Keep `current_price = float(df["close"].iloc[-1])` for `outcome_pnl_pct`
   and `outcome_price` — the P&L view from current close is still valid.
5. Handle the edge case where `target` or `stop_loss` is None (already handled
   by the existing null guards).

**Relevant Context:**
- Function: `check_outcomes` in `signal_db.py` lines 118–193
- The `timestamp` column in the signals table stores signal time as
  `"YYYY-MM-DD HH:MM:SS"` (line 78 of signal_db.py)
- `df.index` from `fetch_history()` is a DatetimeIndex (tz-localised to None)
  so `pd.to_datetime(row["timestamp"])` will be directly comparable
- The circular import (`from scanner import fetch_history`) is pre-existing and
  not addressed in this sub-task (it is a medium-priority issue, not in scope)

---

### Sub-Task 4 — Add Stale-Cache Fallback in nse_indices.py

**Status:** [x] done

**Intent:**
If `niftyindices.com` is unreachable (network outage, site maintenance), the
current code returns `[]` for the segment — causing the scanner to either scan
nothing or fall back to the custom symbols list silently. During market hours
this is a serious reliability gap. The fix preserves the most recently fetched
symbol list as a separate `"previous"` key in the cache file, independent of
today's date, as a last-resort fallback.

**Root Cause (nse_indices.py lines 43–59):**
```python
def _load_cache() -> dict:
    if data.get("date") == _today():     # Only today's data is valid
        return _memory_cache
    return {}                            # Stale data is discarded — no fallback
```

**Expected Outcomes:**
- When a segment fetch fails (HTTP error or empty response), the function
  checks for a `"previous"` key in the cache file containing the last known
  good symbol list for that segment.
- The fallback is used with a clear `log.warning` message stating the date it
  was originally fetched.
- On a successful fetch, the newly fetched data is also saved to `"previous"`
  in addition to the date-keyed entry, so it's always available as a fallback.
- The cache file format becomes:
  `{"date": "YYYY-MM-DD", "segments": {...}, "previous": {"NIFTY50": [...], ...}}`

**Todo List:**
1. In `_save_cache()`, after saving today's data under `cache["segments"]`,
   also copy it to `cache["previous"][segment_key]`.
   This means every successful fetch also updates the fallback layer.
2. In `get_index_symbols()`, after `fetch_from_niftyindices()` returns empty:
   - Load the cache file (ignoring the date check).
   - Check `cache.get("previous", {}).get(key)`.
   - If found, log a warning: `"Using stale fallback for '{segment}' (last fetched: {prev_date})"`.
   - Return the fallback list.
   - If no fallback exists, return `[]` as before.
3. Optionally store the date each fallback entry was originally fetched, so the
   warning message can tell the user how old the data is.
   Structure: `"previous": {"NIFTY50": {"date": "YYYY-MM-DD", "symbols": [...]}}`
4. No changes to `_find_pivots`, callers, or cache memory logic are required.

**Relevant Context:**
- Functions: `_load_cache` (lines 43–59), `_save_cache` (lines 62–77),
  `get_index_symbols` (lines 175–223), `fetch_from_niftyindices` (lines 148–172)
- Cache file: `segment_cache.json` in the same directory as `nse_indices.py`
- In-memory `_memory_cache` only stores today's data — the fallback layer lives
  only in the JSON file (intentionally, to survive process restarts)
- `segment_cache.json` currently exists and contains live data — the new
  `"previous"` key will be added on the next successful fetch

---

### Sub-Task 5 — Pre-fetch MTF Data Once Per Scan Cycle (Latency)

**Status:** [x] done

**Intent:**
`scan_symbol()` fetches the higher-timeframe (HTF) data per symbol when MTF
confirmation is enabled. With 50+ symbols this means 50+ additional HTTP calls
in sequence within each thread. All stocks in the same index share the same
market context — the HTF UTBot trend direction of RELIANCE and HDFC on a 1h
chart are independent, so per-symbol HTF fetching is legitimate, but the
bottleneck is that it doubles the total fetch count.

The faster win is to batch-pre-fetch HTF data for all symbols before the
`ThreadPoolExecutor` starts, store results in a dict keyed by symbol, and pass
the cached HTF DataFrame into `scan_symbol()` — eliminating the per-thread
network call.

**Expected Outcomes:**
- A new optional parameter `htf_df` is added to `scan_symbol()`:
  `def scan_symbol(symbol, timeframe, config, lookback_candles, nifty_df, htf_df=None)`
- `run_scan()` pre-fetches HTF data for all symbols in a separate
  `ThreadPoolExecutor` batch (parallel, same as primary fetch) before the
  main scan loop, storing results as `htf_cache: dict[str, pd.DataFrame]`.
- `scan_symbol()` uses `htf_df` if provided, skipping the internal
  `fetch_history(symbol, mtf_tf, config)` call.
- When `mtf_enabled` is False, the pre-fetch step is skipped entirely.
- Total wall-clock time for a 50-symbol scan with MTF enabled roughly halves
  (two parallel batches instead of 50+50 sequential per-thread fetches).

**Todo List:**
1. In `run_scan()` (scanner.py lines 491–656), after building the `symbols`
   list and before the main `ThreadPoolExecutor`, add an MTF pre-fetch block:
   ```
   if filters_cfg.get("mtf_enabled", False):
       mtf_tf = filters_cfg.get("mtf_timeframe", "1h")
       htf_cache = {}
       with ThreadPoolExecutor(max_workers=10) as ex:
           htf_futures = {ex.submit(fetch_history, sym, mtf_tf, config): sym for sym in symbols}
           for f in as_completed(htf_futures):
               sym = htf_futures[f]
               try:
                   htf_cache[sym] = f.result()
               except Exception:
                   htf_cache[sym] = None
   else:
       htf_cache = {}
   ```
2. Pass `htf_df=htf_cache.get(sym)` to `scan_symbol()` in the main executor
   `futures` dict.
3. In `scan_symbol()`, add `htf_df=None` as a parameter. In the MTF block,
   use `htf_df` if it is not None; otherwise fall back to calling
   `fetch_history(symbol, mtf_tf, config)` (preserves backward compatibility).
4. No changes to `check_mtf_confirmation()` or `signals.py` are required.

**Relevant Context:**
- Function: `run_scan` in `scanner.py` lines 491–656 (main executor at lines 624–648)
- Function: `scan_symbol` in `scanner.py` lines 330–488 (MTF fetch at lines 381–389)
- `nifty_df` follows the same pre-fetch pattern already (lines 614–618 of scanner.py)
  — this sub-task mirrors that exact pattern for HTF data
- `as_completed` is already imported at the top of scanner.py

---

## Implementation Order

The sub-tasks are independent and can be done in any order. Recommended sequence
based on risk and verifiability:

1. **Sub-Task 2** (RSI fix) — smallest change, easiest to verify against TV
2. **Sub-Task 1** (SR vectorisation) — pure performance, zero logic change
3. **Sub-Task 3** (outcome OHLC) — improves DB accuracy, isolated to signal_db.py
4. **Sub-Task 4** (stale cache) — reliability fix, isolated to nse_indices.py
5. **Sub-Task 5** (MTF pre-fetch) — scan-level change, test with MTF enabled

---

## Non-Goals

- No changes to Pine Script replication logic (UTBot trail, SR zone geometry)
- No changes to the frontend dashboard
- No changes to the Telegram alert format
- Medium-priority items (#7–#16 from the review) are out of scope for this plan
