# Setup Score — Walkthrough

The **Setup Score** (0–100) measures the overall quality of a buy or sell signal. It is built by summing points from up to eight independent components, then hard-capped at 100.

The score is **always computed** regardless of whether individual filters are enabled. Filters only gate whether the signal appears in the results — they never suppress score contributions.

---

## Component Breakdown

### 1. S/R Zone Strength & Proximity — up to 50 pts

**Source:** `signals.py → evaluate_composite_signals` (S/R Zone scoring block)

Only the **single best matching zone** contributes points — they are not cumulative across multiple zones.

Points are the sum of two sub-components:

#### Zone Rank (strength) — up to 30 pts

| Zone Rank | Points |
|-----------|--------|
| Rank 1 (strongest) | 30 pts |
| Rank 2 | 25 pts |
| Rank 3 | 20 pts |
| Rank 4+ | 15 pts |

Zones are ranked by the scanner's S/R clustering algorithm — more pivot touches and higher pivot density = higher rank.

#### Proximity to Zone — up to 20 pts

| Price Position | Points |
|----------------|--------|
| **Inside** the zone | 20 pts (full) |
| **Near** zone (within `proximity_pct` %) | 0–20 pts, linear — decreases as distance increases |
| Outside proximity range | 0 pts |

> `proximity_pct` is configured in `config.yml → sr_channels → proximity_pct` (default `0.2`).

**Maximum combined:** 30 + 20 = **50 pts**

---

### 2. Volume Surge — up to 20 pts

**Source:** `signals.py → evaluate_composite_signals` (Volume Spike block)

```
vol_pts = min(20.0, 10.0 × (current_volume / volume_SMA))
```

| Volume vs SMA | Points |
|---------------|--------|
| 0.5× average | 5 pts |
| 1.0× average | 10 pts |
| 2.0× average or more | 20 pts (capped) |

Applies to both BUY and SELL scores simultaneously.

> Configured via `config.yml → filters → volume_sma_period` (default 20 bars).
> Always scored even when `volume_filter_enabled: false`.

---

### 3. EMA Trend Confluence — 20 pts (all or nothing)

**Source:** `signals.py → evaluate_composite_signals` (EMA Trend Confluence block)

| Condition | Points awarded to |
|-----------|-------------------|
| Close **above** EMA | +20 pts → **BUY score** |
| Close **below** EMA | +20 pts → **SELL score** |

Only one direction receives the 20 pts per scan — a signal that aligns with the EMA trend is rewarded; one that goes against the trend is not.

> Configured via `config.yml → filters → ema_period` (default 200).
> Always scored even when `ema_filter_enabled: false`.

---

### 4. RSI Momentum Confluence — 10 pts (all or nothing)

**Source:** `signals.py → evaluate_composite_signals` (RSI Momentum block)

| Condition | Points |
|-----------|--------|
| BUY: RSI within `rsi_buy_min`–`rsi_buy_max` | +10 pts → BUY score |
| SELL: RSI within `rsi_sell_min`–`rsi_sell_max` | +10 pts → SELL score |
| Outside the configured range | 0 pts |

> Defaults: BUY range `40–65`, SELL range `35–60`.
> Configured in `config.yml → filters → rsi_buy_min / rsi_buy_max / rsi_sell_min / rsi_sell_max`.
> Always scored even when `rsi_filter_enabled: false`.

---

### 5. ADX Trend Strength — up to 10 pts

**Source:** `signals.py → evaluate_composite_signals` (ADX Trend Strength block)

| ADX Value | Points |
|-----------|--------|
| ADX ≥ `adx_strong_threshold` (default 25) | +10 pts |
| ADX ≥ `adx_moderate_threshold` (default 20) | +5 pts |
| ADX below moderate threshold | 0 pts |

Applies to **both** BUY and SELL scores simultaneously — ADX measures trend strength regardless of direction.

> Configured via `config.yml → filters → adx_strong_threshold` / `adx_moderate_threshold`.
> Always scored even when `adx_filter_enabled: false`.

---

### 6. Candlestick Pattern Recognition — up to 8 pts

**Source:** `signals.py → evaluate_composite_signals` (Candlestick Pattern block)

Points are awarded per detected pattern and boosted when price is near an S/R zone:

| Pattern | At S/R Zone | Away from S/R |
|---------|-------------|---------------|
| Bullish / Bearish Engulfing | 8 pts | 3 pts |
| Pin Bar / Shooting Star | 6 pts | 3 pts |
| Morning Star (bullish) | 5 pts | 3 pts |
| Evening Star (bearish) | 5 pts | 3 pts |

Multiple patterns can stack if detected simultaneously (e.g., Engulfing + Pin Bar at S/R = 14 pts).

> Enabled via `config.yml → filters → candle_patterns_enabled` (default `true`).

---

### 7. Multi-Timeframe (MTF) Confirmation — −10 to +15 pts

**Source:** `scanner.py → _build_result` (MTF score adjustment block)

Applied **after** the base score is computed in `signals.py`, as a final adjustment:

| Higher-TF Trend | Condition | Adjustment |
|-----------------|-----------|------------|
| **Bullish** and signal is BUY | Confirms | +15 pts |
| **Bearish** and signal is SELL | Confirms | +15 pts |
| **Neutral** (price within `mtf_neutral_pct` of trail) | Neither | +5 pts |
| **Bullish** and signal is SELL | Counter-trend | −10 pts |
| **Bearish** and signal is BUY | Counter-trend | −10 pts |

> Configured via `config.yml → filters → mtf_enabled`, `mtf_timeframe`, `mtf_neutral_pct`.
> If `require_mtf_alignment: true`, counter-trend signals are suppressed entirely (not just penalised).

---

### 8. Relative Strength vs NIFTY — +10 pts

**Source:** `scanner.py → _build_result` (Relative Strength score adjustment)

| Condition | Points awarded to |
|-----------|-------------------|
| RS ratio > `rs_buy_threshold` (stock outperforming NIFTY) | +10 pts → BUY score |
| RS ratio < `rs_sell_threshold` (stock underperforming NIFTY) | +10 pts → SELL score |

RS ratio is calculated as: `(1 + stock_return) / (1 + nifty_return)` over the last `rs_period` bars.

> Defaults: `rs_buy_threshold: 1.1`, `rs_sell_threshold: 0.9`, `rs_period: 20`.

---

## Final Score Cap

```python
score = min(100.0, round(raw_score, 1))
```

The raw score can theoretically reach **143 pts** if every component fires at maximum. The cap at 100 is applied in `signals.py` (base score) and then again in `scanner.py` after MTF and RS adjustments.

---

## Theoretical Maximum

| # | Component | Max pts |
|---|-----------|---------|
| 1 | S/R Zone (rank 1, inside) | 50 |
| 2 | Volume Surge (2× SMA) | 20 |
| 3 | EMA Trend Confluence | 20 |
| 4 | RSI Momentum | 10 |
| 5 | ADX Trend Strength | 10 |
| 6 | Candlestick Pattern (at S/R) | 8 |
| 7 | MTF Confirmation | 15 |
| 8 | RS vs NIFTY | 10 |
| | **Raw total** | **143** |
| | **Effective maximum (capped)** | **100** |

---

## Score Tiers

These tiers are used in the dashboard badge colouring and for the priority Telegram alert threshold:

| Score | Tier | Dashboard Badge | Telegram Alert |
|-------|------|-----------------|----------------|
| 70–100 | 🔥 High | Green | Priority (with sound) |
| 40–69 | Medium | Amber | Silent |
| 0–39 | Low | Red | Silent |

> The priority alert threshold is configurable: `config.yml → filters → min_alert_score` (default `70`).

---

## Filter vs Score — Key Distinction

| Toggle | Effect when ON | Effect when OFF |
|--------|----------------|-----------------|
| `ema_filter_enabled` | Rejects signals that go against the EMA trend | Signals pass regardless — but EMA still **scores** |
| `volume_filter_enabled` | Rejects signals with below-average volume | Signals pass regardless — but volume still **scores** |
| `adx_filter_enabled` | Hard-rejects signals when ADX < threshold | ADX still **scores** (moderate/strong bonus applies) |
| `rsi_filter_enabled` | Hard-rejects signals when RSI is outside range | RSI still **scores** when in optimal range |
| `candle_patterns_enabled` | Enables pattern detection and scoring | Patterns are not detected or scored |
| `mtf_enabled` | Enables MTF score adjustment and suppression | No MTF adjustment applied to score |

**Rule:** Filters decide *whether a signal is shown*. The score always reflects *how good the setup is*, independently.
