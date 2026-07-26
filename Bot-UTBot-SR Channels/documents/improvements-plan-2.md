# Bot-UTBot-SR Channels — Medium & Low Impact Improvements Plan

## Overview

This plan addresses 8 remaining improvements identified during code review.
Items #13 (chart button) and #16 (credentials) are explicitly excluded per user decision.
Each sub-task is independent and can be implemented and reviewed one at a time.

**Files in scope:**
- `telegram.py`           — sub-task 1
- `app.py`                — sub-tasks 2, 3, 4
- `signals.py`            — sub-task 5
- `signal_db.py`          — sub-task 6
- `frontend/index.js`     — sub-tasks 7, 8
- `frontend/index.html`   — sub-task 8
- `frontend/index.css`    — sub-task 8

---

## Sub-Tasks

---

### Sub-Task 1 — telegram.py: Pass Config Instead of Reloading from Disk

**Status:** [ ] pending

**Intent:**
`send_telegram_alert()` calls `load_config()` on every invocation, opening and
YAML-parsing `config.yml` from disk each time an alert is sent. The scanner
already has a fully-loaded `config` dict in scope at both call sites. This is
unnecessary I/O and means any config change mid-session only takes effect at
the next alert fire.

**Root Cause (telegram.py line 28):**
```
send_telegram_alert(message, priority=5, silent=False):
    config = load_config()   # file read on every call
```

**Call sites in scanner.py:**
- Line 1011 (single scan): `send_telegram_alert(msg, priority=8, silent=not has_priority)`
- Line 1064 (continuous): `send_telegram_alert(msg, priority=8, silent=not has_priority)`

**Expected Outcomes:**
- `send_telegram_alert` gains an optional `config: dict = None` parameter.
- When `config` is provided, `load_config()` is not called.
- When `config` is `None` (e.g. called from a test or standalone script),
  `load_config()` is called as a fallback — preserving backward compatibility.
- Both call sites in `scanner.py` are updated to pass the `config` dict.

**Todo List:**
1. In `telegram.py`, update the `send_telegram_alert` signature to:
   `def send_telegram_alert(message, priority=5, silent=False, config: dict = None):`
2. Replace the unconditional `config = load_config()` with:
   `if config is None: config = load_config()`
3. In `scanner.py` at line 1011, add `config=config` to the call.
4. In `scanner.py` at line 1064, add `config=config` to the call.
5. No changes to `_send_direct()` or `_send_via_openalgo()` — they already
   receive the config dict as a parameter from `send_telegram_alert`.

**Relevant Context:**
- `send_telegram_alert` in `telegram.py` lines 20-35
- Both scanner.py call sites are in `main()` where the `config` dict is in scope
- `app.py` does NOT call `send_telegram_alert`, so no change needed there

---

### Sub-Task 2 — app.py: Run Scan in Background Thread (Non-Blocking)

**Status:** [ ] pending

**Intent:**
The `POST /api/scan` route calls `run_scan()` directly in the FastAPI request
handler. `run_scan()` takes 60-120 seconds for large segments. During this time,
FastAPI's synchronous worker is blocked, causing all other dashboard API calls
(`/api/logs`, `/api/config`, `/api/signal-history`) to queue and appear to hang.

**Root Cause (app.py lines 147-166):**
```
@app.post("/api/scan")
def trigger_scan(timeframe, mode):
    buy, sell, label, tf = run_scan(...)   # blocks for 60-120s
    return { "status": "success", ... }
```

**Expected Outcomes:**
- `trigger_scan` becomes an `async` function.
- `run_scan()` is executed in a thread pool via
  `starlette.concurrency.run_in_threadpool` so the event loop is not blocked.
- The API response shape is unchanged — the route still returns the full scan
  result, just without blocking other requests while it computes.
- No frontend changes needed — the scan result arrives in the same shape.

**Todo List:**
1. Add `from starlette.concurrency import run_in_threadpool` to `app.py` imports.
2. Change `def trigger_scan(...)` to `async def trigger_scan(...)`.
3. Replace the direct `run_scan(cfg, ...)` call with:
   `buy, sell, label, tf = await run_in_threadpool(run_scan, cfg,
   timeframe_override=timeframe, mode_override=mode)`
   (Note: if Sub-Task 4 has been applied first, unpack 5 values here too.)
4. No changes to the return dict or the frontend.

**Relevant Context:**
- `trigger_scan` in `app.py` lines 147-166
- `run_in_threadpool` is part of Starlette which is already a FastAPI dependency
  — no new package install required
- FastAPI natively supports `async def` route handlers

---

### Sub-Task 3 — app.py: Preserve Config Comments on Save (ruamel.yaml)

**Status:** [ ] pending

**Intent:**
`update_config()` uses `yaml.dump()` (PyYAML) which strips all inline comments
from `config.yml`. Every `# Options: ...`, `# Range: 4 to 30`, and section
header comment is permanently lost after one save from the Settings panel.
`ruamel.yaml` is a drop-in library that performs comment-preserving round-trip
YAML serialisation.

**Root Cause (app.py lines 139-143):**
```
config_dict = req.model_dump()
with open(config_path, "w", encoding="utf-8") as fh:
    yaml.dump(config_dict, fh, sort_keys=False, default_flow_style=False)
```

**Expected Outcomes:**
- On save, the existing `config.yml` file is read first to capture its comment
  structure via `ruamel.yaml`'s round-trip loader.
- Each changed value is updated in-place in the CommentedMap without disturbing
  comments or key ordering.
- The resulting saved file retains all inline comments exactly as authored.
- On GET `/api/config`, loading is unchanged (still uses PyYAML `yaml.safe_load`
  inside `load_config()` in `scanner.py` — no change needed there).

**Todo List:**
1. Install `ruamel.yaml` (`pip install ruamel.yaml`).
2. In `app.py`, add `from ruamel.yaml import YAML` alongside the existing
   `import yaml` (keep `yaml` for all read operations; use `YAML` only for save).
3. Write a private helper `_update_commented_map(cm, updates: dict)` that
   recursively walks `updates` and sets each value on the CommentedMap `cm`
   in-place. For nested dicts, recurse. For scalar values, assign directly.
   This preserves the comment nodes attached to each key.
4. Replace the `yaml.dump()` block in `update_config()` with:
   - Instantiate `ryaml = YAML()` with `ryaml.preserve_quotes = True`.
   - Read the current `config.yml` into a CommentedMap: `cm = ryaml.load(fh)`.
   - Call `_update_commented_map(cm, config_dict)` to merge new values in-place.
   - Write back with `ryaml.dump(cm, fh)`.

**Relevant Context:**
- `update_config` in `app.py` lines 134-145
- `load_config` in `scanner.py` uses PyYAML `yaml.safe_load` — no change needed
- `ruamel.yaml` CommentedMap supports direct key assignment while retaining
  attached comment nodes; only values need to change, not structure

---

### Sub-Task 4 — scanner.py + app.py + index.js: Add total_scanned to Scan Response

**Status:** [ ] pending

**Intent:**
`renderScanData()` in `index.js` hard-codes `totalCountScanned = 50` for
NIFTY50 and special-cases 14 and 59 for BANKNIFTY combinations. This is wrong
for any other segment (NIFTYIT, NIFTY200, custom symbols list, etc.).
`run_scan()` already knows the exact symbol count — it just is not returned.

**Root Cause:**
- `app.py trigger_scan` return dict (lines 157-164): no `total_scanned` key.
- `index.js renderScanData` lines 434-441: hardcoded counts.

**Expected Outcomes:**
- `run_scan()` return signature expands from a 4-tuple to a 5-tuple, adding
  `total_symbols: int` as the fifth element.
- `trigger_scan` in `app.py` adds `"total_scanned": total_symbols` to the
  response dict.
- `renderScanData` in `index.js` reads `data.total_scanned` directly, with a
  fallback to the sum of buy+sell signals if the key is absent.
- The hardcoded 50/14/59 block is removed.

**Todo List:**
1. In `scanner.py run_scan()`, change the return statement (line 656) from:
   `return buy_results, sell_results, segment_label, timeframe`
   to:
   `return buy_results, sell_results, segment_label, timeframe, len(symbols)`
2. Update the two unpacking sites in `scanner.py main()`:
   - Line 966: `buy_results, sell_results, seg_label, tf = _do_scan()`
     becomes `buy_results, sell_results, seg_label, tf, _ = _do_scan()`
   - Line 1016: same change (the total count is not used in CLI output)
3. In `app.py trigger_scan`, unpack the 5th value:
   `buy, sell, label, tf, total_symbols = await run_in_threadpool(...)`
   (or the sync equivalent if Sub-Task 2 has not been applied yet)
   and add `"total_scanned": total_symbols` to the return dict.
4. In `index.js renderScanData`, replace lines 434-441 with:
   `const totalCountScanned = data.total_scanned ?? total;`
   (where `total` on line 424 is already the sum of buy+sell signals — a
   reasonable fallback for cached/older API responses)
5. Update `statScannedCount.textContent = totalCountScanned` (unchanged line
   number, just new value source).

**Relevant Context:**
- `run_scan` return statement in `scanner.py` line 656
- Two `_do_scan()` unpack sites in `scanner.py main()` at lines 966 and 1016
- `trigger_scan` in `app.py` lines 147-166
- `renderScanData` in `index.js` lines 434-441

---

### Sub-Task 5 — signals.py: Replace df.attrs with Explicit Zone Return

**Status:** [ ] pending

**Intent:**
`df.attrs["sr_zones"]` is silently dropped by most pandas operations (slices,
copies, `pd.concat`, groupby, etc.). The current code works by luck — zones are
consumed in the same call chain without an intervening pandas op. Any future
refactor chaining a DataFrame operation between `compute_sr_signals()` and
`evaluate_composite_signals()` will silently produce empty zones with no error.

**Root Cause:**
- WRITE: `compute_sr_signals()` sets `df.attrs["sr_zones"] = zones`
  (signals.py lines 343, 382, 422)
- READ: `evaluate_composite_signals()` reads `df.attrs.get("sr_zones", [])`
  (signals.py lines 886, 1016)
- READ: `app.py` history endpoint reads `df.attrs["sr_zones"]` (line 207)

**Expected Outcomes:**
- `compute_sr_signals()` return type changes to `tuple[pd.DataFrame, list]`.
- All three `df.attrs["sr_zones"] = ...` writes are removed entirely. Returns
  become `return df, zones` using a local `zones` variable.
- `evaluate_composite_signals()` gains an explicit `sr_zones: list` parameter
  and uses it directly — the `df.attrs` reads at lines 886 and 1016 are removed.
- The `app.py` history endpoint unpacks `df, zones` directly — the `df.attrs`
  read block at lines 206-210 is removed.
- `df.attrs["sr_zones"]` is fully eliminated. Every consumer is inside this
  project and is updated as part of this sub-task — no backward-compat needed.

**Todo List:**
1. In `compute_sr_signals()`, replace all three `df.attrs["sr_zones"] = X`
   statements with a local `zones = X`. Change all three `return df` statements
   to `return df, zones`. Update the docstring return type.
2. Update `evaluate_composite_signals()` signature to accept `sr_zones=None`.
   At the very start of the function body set `zones = sr_zones or []`.
   Remove the `df.attrs.get("sr_zones", [])` read at line 886 and replace the
   `df.attrs["sr_zones"]` read at line 1016 with the `zones` variable already
   in scope.
3. In `scanner.py scan_symbol()`, unpack: `df, sr_zones = compute_sr_signals(df, ...)`
   and pass `sr_zones=sr_zones` into `evaluate_composite_signals()`.
4. In `app.py` history endpoint, unpack: `df, zones = compute_sr_signals(df, ...)`
   and use `zones` directly, removing the `df.attrs` read block at lines 206-210.

**Relevant Context:**
- `compute_sr_signals` in `signals.py` lines 298-422; returns at lines 338, 379, 420
- `evaluate_composite_signals` in `signals.py` lines 727-1028; df.attrs reads at 886, 1016
- `scan_symbol` in `scanner.py` lines 330-488; compute_sr_signals call at line 361
- `app.py` history endpoint lines 192-245; compute_sr_signals call at line 193

---

### Sub-Task 6 — signal_db.py: Remove Circular Import via fetch_fn Parameter

**Status:** [ ] pending

**Intent:**
`check_outcomes()` contains `from scanner import fetch_history` inside the
function body to avoid a circular import at module load time. This makes
`signal_db.py` (a data layer) depend on `scanner.py` (an orchestration layer),
and makes `signal_db.py` untestable in isolation.

**Root Cause (signal_db.py line 142):**
```
def check_outcomes(hours=4, config=None):
    ...
    from scanner import fetch_history   # circular dep hidden in function body
```

**Call site in scanner.py (line 1087):**
```
check_outcomes(hours=outcome_hours, config=config)
```

**Expected Outcomes:**
- `check_outcomes()` gains a `fetch_fn` callable parameter defaulting to `None`.
- When `fetch_fn` is `None`, the function returns `0` immediately with a debug
  log — preserving safe behavior when called outside the scanner context.
- The `from scanner import fetch_history` line is removed from the function body.
- `signal_db.py` no longer imports anything from `scanner.py`.
- The call site in `scanner.py` passes `fetch_fn=fetch_history` explicitly.

**Todo List:**
1. Update `check_outcomes` signature to:
   `def check_outcomes(hours: int = 4, config: dict = None, fetch_fn=None) -> int:`
2. At the top of the function body, before the DB connection, add:
   ```
   if fetch_fn is None:
       log.debug("check_outcomes: no fetch_fn provided, skipping outcome check.")
       return 0
   ```
3. Remove the `from scanner import fetch_history` line (line 142).
4. Replace `df = fetch_history(symbol, tf, config or {})` with
   `df = fetch_fn(symbol, tf, config or {})`.
5. In `scanner.py` at line 1087, update the call to:
   `check_outcomes(hours=outcome_hours, config=config, fetch_fn=fetch_history)`

**Relevant Context:**
- `check_outcomes` in `signal_db.py` lines 118-194; lazy import at line 142
- Call site in `scanner.py` line 1087 inside the continuous scan loop
- `fetch_history` defined in `scanner.py` lines 128-323

---

### Sub-Task 7 — index.js: Re-initialise Auto-Refresh After Config Save

**Status:** [ ] pending

**Intent:**
`initAutoRefresh()` sets a `setInterval` once at page load and never updates
it. If the user saves new `scan_interval_seconds` in the Settings panel, the
auto-refresh fires at the old rate until a manual page reload.

**Root Cause (index.js lines 466-469):**
```javascript
setTimeout(() => {
    const seconds = activeConfig?.scan_interval_seconds || 300;
    autoRefreshInterval = setInterval(executeScan, seconds * 1000);
}, 1000);   // runs ONCE at startup, never again
```

After config save, `loadConfig()` is called (line 277) and `activeConfig` is
updated (line 95), but the live `setInterval` is unchanged.

**Expected Outcomes:**
- The interval-start logic is extracted to a helper `_startAutoRefresh()` that
  reads the current `activeConfig.scan_interval_seconds`, clears any existing
  interval, and starts a fresh one.
- `initAutoRefresh()` calls `_startAutoRefresh()` instead of the inline
  `setTimeout`.
- The `configForm` submit handler calls `_startAutoRefresh()` after
  `loadConfig()` resolves — but only when auto-refresh is currently ON.
- The ON/OFF toggle still works correctly: toggling OFF clears the interval;
  toggling ON calls `_startAutoRefresh()`.

**Todo List:**
1. Define a new function `_startAutoRefresh()` inside the
   `DOMContentLoaded` closure (near `initAutoRefresh`):
   - Clear `autoRefreshInterval` if set.
   - Read `const seconds = activeConfig?.scan_interval_seconds || 300`.
   - Set `autoRefreshInterval = setInterval(executeScan, seconds * 1000)`.
2. In `initAutoRefresh()`, replace the `setTimeout` block with a direct call to
   `_startAutoRefresh()` (no delay needed — `activeConfig` is already loaded
   by the time `initAutoRefresh()` is called from `init()`).
3. In the toggle click handler inside `initAutoRefresh()`, replace
   `autoRefreshInterval = setInterval(executeScan, seconds * 1000)` with
   `_startAutoRefresh()`.
4. In the `configForm` submit success handler, after `await loadConfig()`,
   add:
   ```javascript
   if (autoRefreshInterval !== null) {
       _startAutoRefresh();
   }
   ```

**Relevant Context:**
- `initAutoRefresh` in `index.js` lines 447-470
- `configForm` submit handler success branch in `index.js` lines 269-280
- `activeConfig` is set by `loadConfig()` at line 95 before `initAutoRefresh`
  is called from `init()` at line 691

---

### Sub-Task 8 — index.js + index.html + index.css: Stale Data Indicator

**Status:** [ ] pending

**Intent:**
While a scan runs (60-120 seconds), the signal tables show previous-cycle data
with no visual cue beyond the button spinner. Users may act on stale signals.
A timestamp badge on each table header, turning amber when data is old, solves this.

**Expected Outcomes:**
- A "Last updated: HH:MM:SS" badge appears in the BUY and SELL table card
  headers, updated each time `renderScanData()` runs.
- While a scan is actively running, the badges show a pulsing "Refreshing…"
  state.
- When the elapsed time since the last scan exceeds `2 × scan_interval_seconds`,
  the badge turns amber (`badge-stale` CSS class).
- No existing layout or functionality is disrupted.

**Todo List:**

**index.html changes:**
1. In the BUY table card header (inside `.table-card-header`, after the `<h3>`):
   add `<span class="last-updated-badge" id="buy-last-updated">—</span>`
2. In the SELL table card header (same pattern):
   add `<span class="last-updated-badge" id="sell-last-updated">—</span>`

**index.js changes:**
3. Add module-level variable: `let lastScanTimestamp = null;`
4. Cache the two new elements near the other element cache declarations at the
   top of the `DOMContentLoaded` callback.
5. In `executeScan()`, before the `fetch` call, set both badges to
   `"⟳ Refreshing…"` and add CSS class `badge-scanning`.
6. In `executeScan()` `finally` block (after scan completes or errors), remove
   `badge-scanning` class (so it clears even on error).
7. In `renderScanData()`, after the existing stats update block:
   - Set `lastScanTimestamp = Date.now()`.
   - Set both badge texts to `"Updated: " + new Date().toLocaleTimeString()`.
   - Replace `badge-scanning` with `badge-fresh` on both badges.
8. Add a `setInterval` (30-second tick) inside `init()` that:
   - Skips if `lastScanTimestamp` is null or scan is active.
   - Calculates `elapsed = (Date.now() - lastScanTimestamp) / 1000`.
   - If `elapsed > 2 * (activeConfig?.scan_interval_seconds || 300)`,
     replaces `badge-fresh` with `badge-stale` on both badges.

**index.css changes:**
9. Add base style `.last-updated-badge` — small font, subtle border-radius,
   muted foreground, padding.
10. Add `.badge-scanning` — uses a CSS opacity pulse animation to indicate
    active refresh.
11. Add `.badge-stale` — amber text/border color to indicate outdated data.
12. Add `.badge-fresh` — neutral/green-tinted style for recently updated data.

**Relevant Context:**
- BUY table card header in `index.html` lines 114-121
- SELL table card header in `index.html` lines 147-153
- `renderScanData` in `index.js` lines 337-442; stats block at 423-441
- `executeScan` in `index.js` lines 314-333; `isScanning` flag at line 9
- `init()` function in `index.js` lines 690-696

---

## Implementation Order

Recommended sequence based on scope and dependency:

| Order | Sub-Task | Risk | Scope |
|-------|----------|------|-------|
| 1 | Sub-Task 6 — Remove circular import | Very low | `signal_db.py`, `scanner.py` |
| 2 | Sub-Task 1 — Telegram config param | Very low | `telegram.py`, `scanner.py` |
| 3 | Sub-Task 4 — total_scanned in response | Low | `scanner.py`, `app.py`, `index.js` |
| 4 | Sub-Task 2 — Non-blocking scan route | Low | `app.py` only |
| 5 | Sub-Task 5 — Explicit zone return | Medium | `signals.py`, `scanner.py`, `app.py` |
| 6 | Sub-Task 3 — ruamel.yaml config save | Medium | `app.py` only |
| 7 | Sub-Task 7 — Auto-refresh reinit | Low | `index.js` only |
| 8 | Sub-Task 8 — Stale data indicator | Low | `index.js`, `index.html`, `index.css` |

---

## Non-Goals

- #13 chart button local chart modal — excluded per user decision
- #16 credentials in plaintext — excluded per user decision
- No changes to Pine Script replication logic
- No changes to the scanner's trading signal computation
