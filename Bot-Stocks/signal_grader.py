"""
signal_grader.py
================
Sprint 3: Signal Grading — converts a scanner signal dict into a letter grade
(A / B / C / D) from five weighted, independently-computed quality factors.

Why a separate grade when ``setup_score`` already exists?
--------------------------------------------------------
``setup_score`` (built in ``signals.py`` + adjusted in ``scanner._build_result``)
is a *rule-hit accumulator*: it answers "how many of my configured conditions
fired?". It is deliberately noisy and config-sensitive — enabling one more
filter shifts every score.

The grade answers a different question: "given the market context, how good is
this entry?" It reads only *continuous context measures* (ADX magnitude, RS
ratio, distance to the nearest S/R boundary, higher-timeframe agreement, and
whether both engines concur) and normalises each to 0..1 before weighting. That
makes grades comparable across config changes and across time, which is what
you need to (a) gate trades by quality and (b) scale position size by
conviction.

The two are complementary and both are persisted: ``setup_score`` tells you
*which rules* fired, ``grade`` tells you *how favourable the context* was.

Factors and default weights (sum = 100)
---------------------------------------
============  ======  =============================================================
Factor        Weight  Full credit when ...
============  ======  =============================================================
adx            25     ADX >= ``adx_strong`` (default 35). Zero below ``adx_floor``
                      (default 20); linear in between.
rs             20     BUY: RS >= ``rs_strong`` (1.10). SELL: RS <= 2 - rs_strong
                      (0.90). Zero at RS == 1.0 (market-neutral); linear between.
sr_proximity   20     Close is within ``sr_near_pct`` (0.5%) of the nearest S/R
                      boundary → tight, well-defined stop. Zero beyond
                      ``sr_far_pct`` (3.0%); linear between.
mtf            20     Higher-timeframe trend confirms the signal direction.
                      Neutral HTF earns ``mtf_neutral_credit`` (0.6). Counter-
                      trend earns 0.
engine         15     Both UT-Bot and S/R channel engines triggered (composite).
                      Single engine earns 0.5, unrecognised earns 0.25.
============  ======  =============================================================

Grade thresholds (default): **A >= 75, B >= 60, C >= 45, D < 45**. These match
the sibling ``Bot-NSE-Options`` convention so operators reading both dashboards
interpret letters identically.

Fail-open contract
------------------
Every factor independently degrades to ``unknown_credit`` (default 0.5) when
its input is missing or unparseable — a stock with no NIFTY benchmark loaded
gets *half* the RS weight rather than being punished to zero. ``grade_signal``
never raises; on catastrophic failure it returns a neutral C with
``reason="GRADER_ERROR: ..."`` so the scanner keeps trading.

Config schema (all optional, under top-level ``signal_grading``)
----------------------------------------------------------------
signal_grading:
  enabled: true
  min_grade_to_trade: "D"      # D → grade never blocks (observe-only default)
  weights:
    adx: 25
    rs: 20
    sr_proximity: 20
    mtf: 20
    engine: 15
  thresholds:                  # score floors, checked high → low
    A: 75
    B: 60
    C: 45
  adx_floor: 20.0
  adx_strong: 35.0
  rs_strong: 1.10
  sr_near_pct: 0.5
  sr_far_pct: 3.0
  mtf_neutral_credit: 0.6
  unknown_credit: 0.5
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("UTBotSRChannelsScanner")

# Grade ordering, best → worst. Used by ``meets_min_grade`` for comparison and
# by ``grade_signal`` when walking thresholds.
GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D")

_DEFAULT_WEIGHTS: dict[str, float] = {
    "adx": 25.0,
    "rs": 20.0,
    "sr_proximity": 20.0,
    "mtf": 20.0,
    "engine": 15.0,
}

_DEFAULT_THRESHOLDS: dict[str, float] = {"A": 75.0, "B": 60.0, "C": 45.0}


def _cfg(config: Optional[dict]) -> dict:
    """Return the ``signal_grading`` sub-dict (never None)."""
    return (config or {}).get("signal_grading", {}) or {}


def is_grading_enabled(config: Optional[dict]) -> bool:
    """True unless explicitly disabled.

    Grading is ON by default because it is *observational* until
    ``min_grade_to_trade`` is raised above ``"D"`` — computing a grade has no
    effect on order flow, it only annotates signals and (when the multiplier
    is enabled) scales size.
    """
    return bool(_cfg(config).get("enabled", True))


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Best-effort float coercion; returns *default* on failure or None input."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    # Reject NaN / inf — they poison every downstream comparison.
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _clamp01(x: float) -> float:
    """Clamp to the unit interval."""
    return max(0.0, min(1.0, x))


def _lerp_credit(value: float, zero_at: float, full_at: float) -> float:
    """Linearly map *value* into 0..1 across [zero_at, full_at].

    Handles the descending case (``full_at < zero_at``) so callers can express
    "lower is better" without inverting arithmetic at the call site.
    """
    span = full_at - zero_at
    if abs(span) < 1e-12:
        # Degenerate config (floor == strong): treat as a step function.
        return 1.0 if value >= full_at else 0.0
    return _clamp01((value - zero_at) / span)


# ---------------------------------------------------------------------------
# Individual factor scorers
#
# Each returns (credit_0_to_1, human_readable_detail). They never raise — a
# missing or unusable input yields ``unknown_credit`` so one absent data point
# cannot veto an otherwise-good setup.
# ---------------------------------------------------------------------------

def _score_adx(signal: dict, gc: dict, unknown: float) -> tuple[float, str]:
    """Trend strength. ADX is direction-agnostic, so BUY and SELL share it."""
    adx = _f(signal.get("adx"))
    if adx is None or adx <= 0:
        return unknown, "ADX unavailable"
    floor  = _f(gc.get("adx_floor"), 20.0) or 20.0
    strong = _f(gc.get("adx_strong"), 35.0) or 35.0
    credit = _lerp_credit(adx, floor, strong)
    return credit, f"ADX {adx:.1f} (floor {floor:.0f}, strong {strong:.0f})"


def _score_rs(signal: dict, gc: dict, unknown: float) -> tuple[float, str]:
    """Relative strength vs NIFTY50, evaluated in the signal's direction.

    ``rs_ratio`` is (1+stock_return)/(1+index_return) over ``filters.rs_period``
    bars, so 1.0 means "moved exactly with the index". For a BUY we want > 1
    (leader); for a SELL we want < 1 (laggard). The neutral point is 1.0 for
    both directions, which keeps the factor symmetric.
    """
    rs = _f(signal.get("rs_ratio"))
    if rs is None:
        return unknown, "RS unavailable (no benchmark)"
    strong = _f(gc.get("rs_strong"), 1.10) or 1.10
    direction = str(signal.get("signal", "")).upper()

    if direction == "SELL":
        # Mirror ``strong`` about 1.0 → rs_strong 1.10 implies 0.90 for shorts.
        weak_target = 2.0 - strong
        credit = _lerp_credit(rs, 1.0, weak_target)
        return credit, f"RS {rs:.3f} (short target <={weak_target:.2f})"

    credit = _lerp_credit(rs, 1.0, strong)
    return credit, f"RS {rs:.3f} (long target >={strong:.2f})"


def _score_sr_proximity(signal: dict, gc: dict, unknown: float) -> tuple[float, str]:
    """Distance from entry to the nearest S/R zone boundary, as % of price.

    Rationale: entering *at* a zone edge gives a tight, structurally-justified
    stop (small risk-per-share -> bigger size for the same rupee risk).
    Entering mid-range means the nearest structure is far away, so any stop is
    arbitrary.

    ``sr_zones`` arrives from ``signals.compute_sr_signals`` as a list of
    ``[high, low]`` pairs (sometimes ``[strength, high, low]``); we tolerate
    both plus dict form ``{"high":.., "low":..}``.
    """
    close = _f(signal.get("close"))
    zones = signal.get("sr_zones") or []
    if close is None or close <= 0 or not zones:
        return unknown, "S/R zones unavailable"

    near_pct = _f(gc.get("sr_near_pct"), 0.5) or 0.5
    far_pct  = _f(gc.get("sr_far_pct"), 3.0) or 3.0

    best_dist_pct: Optional[float] = None
    for z in zones:
        bounds: list[float] = []
        if isinstance(z, dict):
            for key in ("high", "low"):
                v = _f(z.get(key))
                if v is not None:
                    bounds.append(v)
        elif isinstance(z, (list, tuple)):
            # [hi, lo] or [strength, hi, lo] -- take the trailing two numbers.
            nums = [_f(x) for x in z]
            nums = [n for n in nums if n is not None]
            bounds = nums[-2:] if len(nums) >= 2 else nums
        else:
            continue

        for b in bounds:
            if b <= 0:
                continue
            d = abs(close - b) / close * 100.0
            if best_dist_pct is None or d < best_dist_pct:
                best_dist_pct = d

    if best_dist_pct is None:
        return unknown, "S/R zones unparseable"

    # Descending: near_pct earns full credit, far_pct earns zero.
    credit = _lerp_credit(best_dist_pct, far_pct, near_pct)
    return credit, f"{best_dist_pct:.2f}% from nearest S/R (near <={near_pct}%)"


def _score_mtf(signal: dict, gc: dict, unknown: float) -> tuple[float, str]:
    """Higher-timeframe agreement.

    ``mtf`` is the dict produced by ``signals.check_mtf_confirmation``:
    ``{"trend": "bullish"|"bearish"|"neutral", ...}``.
    """
    mtf = signal.get("mtf")
    trend = None
    if isinstance(mtf, dict):
        trend = mtf.get("trend")
    elif isinstance(mtf, str):
        trend = mtf
    # Fall back to the flattened column name used by signal_db rows.
    if not trend:
        trend = signal.get("mtf_trend")

    if not trend:
        return unknown, "MTF unavailable"

    trend = str(trend).lower()
    direction = str(signal.get("signal", "")).upper()
    neutral_credit = _clamp01(_f(gc.get("mtf_neutral_credit"), 0.6) or 0.6)

    if trend == "neutral":
        return neutral_credit, "MTF neutral"

    confirms = (
        (direction == "BUY" and trend == "bullish")
        or (direction == "SELL" and trend == "bearish")
    )
    if confirms:
        return 1.0, f"MTF confirms ({trend})"
    return 0.0, f"MTF counter-trend ({trend})"


def _score_engine(signal: dict, gc: dict, unknown: float) -> tuple[float, str]:
    """Engine agreement -- did both independent engines fire on this bar?

    A composite UT-Bot + S/R signal is the highest-conviction case: a
    trend-following trigger landing exactly at a structural level. One engine
    alone is a normal signal (0.5). We read ``triggered`` rather than the
    ``engine`` tag because the scanner collapses composites to "utbot" for
    regime-gating purposes.
    """
    triggered = signal.get("triggered") or []
    if isinstance(triggered, str):
        triggered = [triggered]
    if not triggered:
        # Fall back to the scanner's engine tag when triggered is absent.
        eng = str(signal.get("engine", "")).lower()
        if eng in ("utbot", "sr"):
            return 0.5, f"single engine ({eng})"
        return unknown, "engine unknown"

    blob = " ".join(str(t).upper() for t in triggered)
    has_ut = "UT" in blob
    has_sr = "S/R" in blob or "SR" in blob

    if has_ut and has_sr:
        return 1.0, "both engines (UT + S/R)"
    if has_ut:
        return 0.5, "UT-Bot only"
    if has_sr:
        return 0.5, "S/R only"
    return 0.25, f"unrecognised triggers ({blob[:40]})"


_FACTOR_FUNCS = {
    "adx": _score_adx,
    "rs": _score_rs,
    "sr_proximity": _score_sr_proximity,
    "mtf": _score_mtf,
    "engine": _score_engine,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _resolve_weights(gc: dict) -> dict[str, float]:
    """Merge user weights over defaults, dropping non-positive entries.

    A weight of 0 (or a negative typo) removes the factor entirely rather than
    silently contributing — this is how an operator disables, say, the RS
    factor when running on a universe with no sensible benchmark.
    """
    merged = dict(_DEFAULT_WEIGHTS)
    user = gc.get("weights") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            if k not in _DEFAULT_WEIGHTS:
                log.debug("signal_grader: ignoring unknown weight key %r", k)
                continue
            fv = _f(v)
            if fv is not None:
                merged[k] = fv
    return {k: v for k, v in merged.items() if v > 0}


def _resolve_thresholds(gc: dict) -> dict[str, float]:
    """Merge user grade thresholds over defaults."""
    merged = dict(_DEFAULT_THRESHOLDS)
    user = gc.get("thresholds") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            key = str(k).upper()
            if key not in _DEFAULT_THRESHOLDS:
                continue
            fv = _f(v)
            if fv is not None:
                merged[key] = fv
    return merged


def _letter_for(score: float, thresholds: dict[str, float]) -> str:
    """Map a 0-100 score to a letter, walking A -> B -> C and defaulting to D."""
    for letter in ("A", "B", "C"):
        if score >= thresholds.get(letter, _DEFAULT_THRESHOLDS[letter]):
            return letter
    return "D"


def grade_signal(signal: dict, config: Optional[dict] = None) -> dict:
    """Grade a single scanner signal dict.

    Parameters
    ----------
    signal
        A signal dict as produced by ``scanner.scan_symbol`` / ``_build_result``.
        Reads ``adx``, ``rs_ratio``, ``sr_zones``, ``close``, ``mtf``,
        ``triggered``, and ``signal`` (direction). All are optional.
    config
        Full config dict; reads the ``signal_grading`` sub-section.

    Returns
    -------
    dict
        grade      : "A" | "B" | "C" | "D"
        score      : float 0..100 — the weighted composite
        breakdown  : dict[factor] -> {weight, credit, earned, detail}
        reason     : "OK" | "GRADING_DISABLED" | "GRADER_ERROR: ..."

    Never raises. On unexpected failure returns a neutral ``"C"`` so a grader
    bug can never halt live trading.
    """
    gc = _cfg(config)

    if not is_grading_enabled(config):
        # Return the most permissive grade so any min-grade gate is a no-op.
        return {
            "grade": "A",
            "score": 100.0,
            "breakdown": {},
            "reason": "GRADING_DISABLED",
        }

    try:
        weights = _resolve_weights(gc)
        if not weights:
            return {
                "grade": "C", "score": 50.0, "breakdown": {},
                "reason": "NO_ACTIVE_WEIGHTS",
            }

        unknown = _clamp01(_f(gc.get("unknown_credit"), 0.5) or 0.0)
        total_weight = sum(weights.values())

        breakdown: dict[str, dict] = {}
        earned_total = 0.0

        for factor, weight in weights.items():
            fn = _FACTOR_FUNCS[factor]
            try:
                credit, detail = fn(signal, gc, unknown)
            except Exception as exc:      # pragma: no cover — defensive
                log.debug("signal_grader: factor %s failed: %s", factor, exc)
                credit, detail = unknown, f"factor error: {exc}"
            credit = _clamp01(_f(credit, unknown) or 0.0)
            earned = weight * credit
            earned_total += earned
            breakdown[factor] = {
                "weight": round(weight, 2),
                "credit": round(credit, 4),
                "earned": round(earned, 2),
                "detail": detail,
            }

        # Normalise to 0..100 so custom weight sets (which may not total 100)
        # still map onto the same threshold scale.
        score = (earned_total / total_weight) * 100.0 if total_weight > 0 else 0.0
        score = round(max(0.0, min(100.0, score)), 1)

        return {
            "grade": _letter_for(score, _resolve_thresholds(gc)),
            "score": score,
            "breakdown": breakdown,
            "reason": "OK",
        }

    except Exception as exc:
        log.warning("signal_grader: grade_signal failed (%s) — defaulting to C", exc)
        return {
            "grade": "C",
            "score": 50.0,
            "breakdown": {},
            "reason": f"GRADER_ERROR: {exc}",
        }


def get_min_grade(config: Optional[dict] = None) -> str:
    """Return the configured minimum tradeable grade (default ``"D"``).

    ``"D"`` means "never block" since D is the lowest grade — this is the
    deliberate default so Sprint 3 ships in observe-only mode and operators can
    collect win-rate-by-grade data before tightening.
    """
    raw = str(_cfg(config).get("min_grade_to_trade", "D")).strip().upper()
    if raw not in GRADE_ORDER:
        log.warning(
            "signal_grader: min_grade_to_trade=%r invalid (expected one of %s) "
            "— falling back to 'D' (no gating).",
            raw, ", ".join(GRADE_ORDER),
        )
        return "D"
    return raw


def meets_min_grade(grade: str, config: Optional[dict] = None) -> tuple[bool, str]:
    """Check a grade against ``signal_grading.min_grade_to_trade``.

    Returns
    -------
    (ok, reason) : tuple[bool, str]
        Matches the ``(ok, reason)`` contract used by
        ``risk_limits.check_can_open_new`` and ``regime_gate.check_signal_allowed``
        so all three gates compose identically in the scanner.

    Notes
    -----
    Grades are ordered A > B > C > D. Because ``GRADE_ORDER`` is best-first, a
    *lower* index means a *better* grade, so the pass condition is
    ``index(grade) <= index(minimum)``.
    """
    if not is_grading_enabled(config):
        return True, ""

    minimum = get_min_grade(config)
    if minimum == "D":
        # Lowest possible bar — nothing can fail it. Short-circuit so we don't
        # emit misleading log lines for unrecognised grades.
        return True, ""

    g = str(grade or "").strip().upper()
    if g not in GRADE_ORDER:
        # Unknown grade → fail open. A grader hiccup must not block trading.
        log.debug("signal_grader: unknown grade %r — allowing (fail-open).", grade)
        return True, ""

    if GRADE_ORDER.index(g) <= GRADE_ORDER.index(minimum):
        return True, ""

    return False, f"grade {g} below min_grade_to_trade ({minimum})"

