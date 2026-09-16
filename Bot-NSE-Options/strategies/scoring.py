"""
strategies/scoring.py
======================
Transparent 9-factor weighted scoring for strategy candidates.
Produces ScoredCandidate list sorted by score descending.
Weights are configurable and redistribute when quant components are disabled.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import MarketDataModel
from ai.schemas.regime import MarketRegime
from ai.schemas.strategy import StrategyCandidate, ScoredCandidate, ScoreBreakdown
from ai.schemas.settings import PlatformSettings

log = logging.getLogger("UTBotSRChannelsScanner")

# Default scoring weights (must sum to 100)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "trend_alignment": 22.0,
    "oi_structure": 18.0,
    "risk_reward": 15.0,
    "iv_environment": 12.0,
    "probability_metrics": 10.0,
    "liquidity": 10.0,
    "expected_move_alignment": 8.0,
    "event_risk": 3.0,
    "slippage": 2.0,
}


def score_candidates(
    candidates: list[StrategyCandidate],
    market_data: MarketDataModel,
    regime: MarketRegime,
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> list[ScoredCandidate]:
    """
    Score all candidates using the 9-factor weighted model.
    Returns list sorted by score descending with rank assigned (1 = best).
    """
    if not candidates:
        return []

    base_weights = _load_weights(cfg)

    if settings:
        effective_weights = settings.redistribute_weights(base_weights)
    else:
        effective_weights = base_weights.copy()

    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        try:
            breakdown = _compute_breakdown(
                candidate, market_data, regime, effective_weights
            )
            scored.append(ScoredCandidate(
                candidate=candidate,
                score=round(breakdown.total, 1),
                score_breakdown=breakdown,
                rank=0,          # assigned below after sort
                disqualified=False,
                disqualification_reason="",
            ))
        except Exception as exc:
            log.warning("[Scoring] Failed to score %s: %s", candidate.strategy_type, exc)

    scored.sort(key=lambda x: x.score, reverse=True)
    for i, sc in enumerate(scored):
        sc.rank = i + 1

    if scored:
        log.info(
            "[Scoring] Scored %d candidates. Top: %s (%.1f)",
            len(scored), scored[0].candidate.strategy_type, scored[0].score,
        )
    return scored


def _compute_breakdown(
    candidate: StrategyCandidate,
    market_data: MarketDataModel,
    regime: MarketRegime,
    weights: dict[str, float],
) -> ScoreBreakdown:
    """Compute per-factor scores and weighted total."""
    p = candidate.payoff
    vol = market_data.volatility
    oi = market_data.oi_signals

    # ── Factor 1: Trend Alignment ─────────────────────────────────────────────
    if candidate.regime_aligned:
        trend_score = 80.0 + regime.trend_strength * 0.2
    elif regime.regime == "NEUTRAL" and candidate.strategy_type in (
        "iron_condor", "iron_fly", "short_strangle"
    ):
        trend_score = 70.0
    elif regime.regime == "NEUTRAL":
        trend_score = 40.0
    else:
        trend_score = 30.0

    if regime.timeframe_alignment.aligned:
        trend_score += 10.0
    trend_score = min(100.0, trend_score)

    # ── Factor 2: OI Structure ────────────────────────────────────────────────
    oi_score = 50.0
    if oi.max_put_oi_strike and candidate.strategy_type in (
        "bull_put_spread", "iron_condor"
    ) and candidate.legs:
        dist = abs(candidate.legs[0].strike - oi.max_put_oi_strike)
        oi_score = max(40.0, 90.0 - dist * 0.1)
    elif oi.pcr > 1.1 and candidate.strategy_type == "bear_call_spread":
        oi_score = 75.0
    elif 0.8 <= oi.pcr <= 1.2:
        oi_score = 65.0

    # ── Factor 3: Risk / Reward ───────────────────────────────────────────────
    rr = p.risk_reward_ratio
    rr_score = min(100.0, rr * 40.0) if rr > 0 else 20.0  # 2.5 R:R → 100

    # ── Factor 4: IV Environment ──────────────────────────────────────────────
    iv_score = 50.0
    _premium_sellers = {
        "bull_put_spread", "bear_call_spread",
        "iron_condor", "iron_fly", "short_strangle",
    }
    _premium_buyers = {"bull_call_spread", "bear_put_spread", "long_straddle"}
    if vol.iv_regime == "HIGH" and candidate.strategy_type in _premium_sellers:
        iv_score = 85.0
    elif vol.iv_regime == "LOW" and candidate.strategy_type in _premium_buyers:
        iv_score = 80.0
    elif vol.iv_regime == "NORMAL":
        iv_score = 60.0

    # ── Factor 5: Probability Metrics ─────────────────────────────────────────
    prob_score = min(100.0, p.probability_profit * 100.0)

    # ── Factor 6: Liquidity ───────────────────────────────────────────────────
    liq_score = p.liquidity_score

    # ── Factor 7: Expected-Move Alignment ─────────────────────────────────────
    em_score = 50.0
    if vol.expected_move_abs > 0 and candidate.legs:
        short_legs = [l for l in candidate.legs if l.action == "SELL"]
        if short_legs:
            # Distance of the nearest short strike from ATM approximation
            # (use first long leg strike as proxy for ATM)
            long_legs = [l for l in candidate.legs if l.action == "BUY"]
            atm_proxy = (
                sum(l.strike for l in long_legs) / len(long_legs)
                if long_legs
                else candidate.legs[0].strike
            )
            min_short_dist = min(abs(l.strike - atm_proxy) for l in short_legs)
            if min_short_dist >= vol.expected_move_abs:
                em_score = 85.0
            elif min_short_dist >= vol.expected_move_abs * 0.8:
                em_score = 65.0
            else:
                em_score = 35.0

    # ── Factor 8: Event Risk ──────────────────────────────────────────────────
    event_score = 80.0  # default — no external event calendar in MVP 1
    try:
        from quant.volatility import _estimate_dte
        dte = _estimate_dte(candidate.expiry_date)
        if dte <= 1:
            event_score = 30.0  # 0DTE premium-compression risk
        elif dte <= 3:
            event_score = 55.0
    except Exception:
        pass  # keep default if import fails

    # ── Factor 9: Slippage ────────────────────────────────────────────────────
    slip = p.slippage_estimate
    slip_score = max(0.0, 100.0 - slip * 5.0)  # 20 pts slippage → 0 score

    # ── Weighted total ────────────────────────────────────────────────────────
    factor_scores: dict[str, float] = {
        "trend_alignment": trend_score,
        "oi_structure": oi_score,
        "risk_reward": rr_score,
        "iv_environment": iv_score,
        "probability_metrics": prob_score,
        "liquidity": liq_score,
        "expected_move_alignment": em_score,
        "event_risk": event_score,
        "slippage": slip_score,
    }
    total = sum(
        factor_scores.get(k, 0.0) * (w / 100.0)
        for k, w in weights.items()
        if w > 0
    )

    return ScoreBreakdown(
        trend_alignment=round(trend_score, 1),
        oi_structure=round(oi_score, 1),
        risk_reward=round(rr_score, 1),
        iv_environment=round(iv_score, 1),
        probability_metrics=round(prob_score, 1),
        liquidity=round(liq_score, 1),
        expected_move_alignment=round(em_score, 1),
        event_risk=round(event_score, 1),
        slippage=round(slip_score, 1),
        total=round(total, 1),
        effective_weights=weights,
    )


def _load_weights(cfg: dict) -> dict[str, float]:
    """Load scoring weights from config, normalise to 100, fall back to defaults."""
    w = cfg.get("ai", {}).get("scoring_weights", {})
    if not w or sum(w.values()) == 0:
        return _DEFAULT_WEIGHTS.copy()
    total = sum(w.values())
    return {k: float(v) / total * 100.0 for k, v in w.items()}
