# Setup Score — Walkthrough

The **Setup Score** (0–100) measures the overall quality of a buy or sell signal. It is built by summing points from up to eight independent components, and then hard-capped at 100 at the very end.

The score is **always computed** regardless of whether individual filters are enabled. Filters only gate whether the signal appears in the results — they never suppress score contributions.

---

## Component Breakdown

### 1. S/R Zone Strength & Proximity — up to 45 pts

**Source:** `signals.py → evaluate_composite_signals` (S/R Zone scoring block)

Only the **single best matching zone** contributes points.

Points are the sum of two sub-components:

#### Zone Strength — 10 to 30 pts

Zones are scored based on their actual computed strength (number of pivot touches + density) relative to the strongest zone detected for that symbol.
*   **Strongest zone:** 30 pts
*   **Weaker zones:** Scaled proportionally between 10 and 30 pts.

#### Proximity to Zone — up to 15 pts

| Price Position | Points |
|----------------|--------|
| **Inside** the zone | 15 pts (full) |
| **Near** zone (within `proximity_pct` %) | 0–15 pts, linear — decreases as distance increases |
| Outside proximity range | 0 pts |

> `proximity_pct` is configured in `config.yml → sr_channels → proximity_pct` (default `0.2`).

**Maximum combined:** 30 + 15 = **45 pts**

---

### 2. Volume Surge — up to 15 pts (Directional)

**Source:** `signals.py → evaluate_composite_signals` (Volume Spike block)

```
vol_pts = min(15.0, 10.0 × (current_volume / volume_SMA))
```

| Volume vs SMA | Points |
|---------------|--------|
| 1.0× average | 10 pts |
| 1.5× average or more | 15 pts (capped) |

**Directional Rule:** Volume points are only awarded to the direction that matches the candle color. A volume surge on a bullish (green) candle boosts the BUY score; a surge on a bearish (red) candle boosts the SELL score.

> Configured via `config.yml → filters → volume_sma_period` (default 20 bars).
> Always scored even when `volume_filter_enabled: false`.

---

### 3. EMA Trend Confluence — 10 to 20 pts (Proportional)

**Source:** `signals.py → evaluate_composite_signals` (EMA Trend Confluence block)

Scoring scales proportionally based on how far the price is from the EMA.

| Condition | Points awarded to |
|-----------|-------------------|
| Close **above** EMA | +10 to +20 pts → **BUY score** |
| Close **below** EMA | +10 to +20 pts → **SELL score** |

*   Price within 0% distance of EMA = 10 pts
*   Price ≥ 2% away from EMA = 20 pts (capped)

Only one direction receives points per scan — a signal that aligns with the EMA trend is rewarded; one against the trend is not.

> Configured via `config.yml → filters → ema_period` (default 200).
> Always scored even when `ema_filter_enabled: false`.

---

### 4. RSI Momentum Confluence — 10 pts (Directional)

**Source:** `signals.py → evaluate_composite_signals` (RSI Momentum block)

| Condition | Points |
|-----------|--------|
| Bullish Candle + RSI within `rsi_buy_min`–`rsi_buy_max` | +10 pts → BUY score |
| Bearish Candle + RSI within `rsi_sell_min`–`rsi_sell_max` | +10 pts → SELL score |
| Outside the configured range | 0 pts |

**Directional Rule:** Points are exclusive. A bullish candle only evaluates the BUY range, and a bearish candle only evaluates the SELL range.

> Defaults: BUY range `40–60`, SELL range `40–60`.
> Configured in `config.yml → filters → rsi_buy_min / rsi_buy_max / rsi_sell_min / rsi_sell_max`.
> Always scored even when `rsi_filter_enabled: false`.

---

### 5. ADX Trend Strength — up to 10 pts (Directional)

**Source:** `signals.py → evaluate_composite_signals` (ADX Trend Strength block)

ADX points are awarded based on trend strength, but allocated based on the Directional Indicators (+DI and -DI).

| ADX Value | Points |
|-----------|--------|
| ADX ≥ `adx_strong_threshold` (default 25) | 10 pts |
| ADX ≥ `adx_moderate_threshold` (default 20) | 5 pts |
| ADX below moderate threshold | 0 pts |

**Directional Allocation:**
*   If `+DI > -DI`: Points are awarded to the **BUY score**.
*   If `-DI > +DI`: Points are awarded to the **SELL score**.

> Configured via `config.yml → filters → adx_strong_threshold` / `adx_moderate_threshold`.
> Always scored even when `adx_filter_enabled: false`.

---

### 6. Candlestick Pattern Recognition — up to 8 pts (Best Only)

**Source:** `signals.py → evaluate_composite_signals` (Candlestick Pattern block)

Points are awarded for the **single highest-scoring pattern detected**. Multiple patterns do not stack.

| Pattern | At S/R Zone | Away from S/R |
|---------|-------------|---------------|
| Bullish / Bearish Engulfing | 8 pts | 3 pts |
| Pin Bar / Shooting Star | 6 pts | 3 pts |
| Morning Star (bullish) | 5 pts | 3 pts |
| Evening Star (bearish) | 5 pts | 3 pts |

> Enabled via `config.yml → filters → candle_patterns_enabled` (default `true`).

---

### 7. Multi-Timeframe (MTF) Confirmation — −10 to +15 pts

**Source:** `scanner.py → _build_result` (MTF score adjustment block)

Applied as an adjustment based on the trend of a higher timeframe:

| Higher-TF Trend | Condition | Adjustment |
|-----------------|-----------|------------|
| **Bullish** and signal is BUY | Confirms | +15 pts |
| **Bearish** and signal is SELL | Confirms | +15 pts |
| **Neutral** (price within `mtf_neutral_pct` of trail) | Neither | +5 pts |
| **Bullish** and signal is SELL | Counter-trend | −10 pts |
| **Bearish** and signal is BUY | Counter-trend | −10 pts |

> Configured via `config.yml → filters → mtf_enabled`, `mtf_timeframe`, `mtf_neutral_pct`, and `mtf_atr_period`.
> If `require_mtf_alignment: true`, counter-trend signals are suppressed entirely (not just penalized).

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

The score is accumulated throughout both Stage 1 (`signals.py`) and Stage 2 (`scanner.py`) adjustments, and is only hard-capped to 100 at the very end. This ensures that MTF and RS bonuses fully impact the final score, even for setups that were already very strong.

---

## Theoretical Maximum

| # | Component | Max pts |
|---|-----------|---------|
| 1 | S/R Zone (strength, inside) | 45 |
| 2 | Volume Surge (1.5× SMA) | 15 |
| 3 | EMA Trend Confluence (distance) | 20 |
| 4 | RSI Momentum | 10 |
| 5 | ADX Trend Strength | 10 |
| 6 | Candlestick Pattern (best, at S/R) | 8 |
| 7 | MTF Confirmation | 15 |
| 8 | RS vs NIFTY | 10 |
| | **Raw total** | **133** |
| | **Effective maximum (capped)** | **100** |

---

## Score Tiers

These tiers are used in the dashboard badge coloring and for the priority Telegram alert threshold:

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
