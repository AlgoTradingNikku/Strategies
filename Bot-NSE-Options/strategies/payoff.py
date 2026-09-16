"""
strategies/payoff.py
=====================
Deterministic payoff calculations for all 8 option strategy types.
Python is the SOLE calculator — LLM never touches these numbers.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.strategy import PayoffResult, StrategyLeg

log = logging.getLogger("UTBotSRChannelsScanner")


# ─────────────────────────────────────────────────────────────────────────────
# Credit Spreads
# ─────────────────────────────────────────────────────────────────────────────

def calc_bull_put_spread(
    short_put: StrategyLeg, long_put: StrategyLeg, lot_size: int
) -> PayoffResult:
    """SELL higher-strike PE + BUY lower-strike PE.  Net credit strategy."""
    credit = short_put.ltp - long_put.ltp
    spread_width = short_put.strike - long_put.strike
    max_profit = max(0.0, credit)
    max_loss = max(0.0, spread_width - credit)
    breakeven = short_put.strike - credit
    rr = (max_profit / max_loss) if max_loss > 0 else 0.0
    pop = 1.0 - abs(short_put.delta) if short_put.delta != 0 else 0.5
    net_delta = -short_put.delta + long_put.delta
    net_theta = short_put.theta - long_put.theta
    net_vega = -short_put.vega + long_put.vega
    return PayoffResult(
        entry_credit=round(credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=round(breakeven, 2),
        breakeven_upper=None,
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([short_put, long_put]),
        liquidity_score=_liquidity_score([short_put, long_put]),
    )


def calc_bear_call_spread(
    short_call: StrategyLeg, long_call: StrategyLeg, lot_size: int
) -> PayoffResult:
    """SELL lower-strike CE + BUY higher-strike CE.  Net credit strategy."""
    credit = short_call.ltp - long_call.ltp
    spread_width = long_call.strike - short_call.strike
    max_profit = max(0.0, credit)
    max_loss = max(0.0, spread_width - credit)
    breakeven = short_call.strike + credit
    rr = (max_profit / max_loss) if max_loss > 0 else 0.0
    pop = 1.0 - abs(short_call.delta) if short_call.delta != 0 else 0.5
    net_delta = -short_call.delta + long_call.delta
    net_theta = short_call.theta - long_call.theta
    net_vega = -short_call.vega + long_call.vega
    return PayoffResult(
        entry_credit=round(credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=None,
        breakeven_upper=round(breakeven, 2),
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([short_call, long_call]),
        liquidity_score=_liquidity_score([short_call, long_call]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Debit Spreads
# ─────────────────────────────────────────────────────────────────────────────

def calc_bull_call_spread(
    long_call: StrategyLeg, short_call: StrategyLeg, lot_size: int
) -> PayoffResult:
    """BUY lower-strike CE + SELL higher-strike CE.  Net debit strategy."""
    debit = long_call.ltp - short_call.ltp
    spread_width = short_call.strike - long_call.strike
    max_profit = max(0.0, spread_width - debit)
    max_loss = max(0.0, debit)
    breakeven = long_call.strike + debit
    rr = (max_profit / max_loss) if max_loss > 0 else 0.0
    pop = abs(long_call.delta) if long_call.delta != 0 else 0.5
    net_delta = long_call.delta - short_call.delta
    net_theta = long_call.theta - short_call.theta
    net_vega = long_call.vega - short_call.vega
    return PayoffResult(
        entry_credit=round(-debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=round(breakeven, 2),
        breakeven_upper=None,
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([long_call, short_call]),
        liquidity_score=_liquidity_score([long_call, short_call]),
    )


def calc_bear_put_spread(
    long_put: StrategyLeg, short_put: StrategyLeg, lot_size: int
) -> PayoffResult:
    """BUY higher-strike PE + SELL lower-strike PE.  Net debit strategy."""
    debit = long_put.ltp - short_put.ltp
    spread_width = long_put.strike - short_put.strike
    max_profit = max(0.0, spread_width - debit)
    max_loss = max(0.0, debit)
    breakeven = long_put.strike - debit
    rr = (max_profit / max_loss) if max_loss > 0 else 0.0
    pop = abs(long_put.delta) if long_put.delta != 0 else 0.5
    net_delta = long_put.delta - short_put.delta
    net_theta = long_put.theta - short_put.theta
    net_vega = long_put.vega - short_put.vega
    return PayoffResult(
        entry_credit=round(-debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=round(breakeven, 2),
        breakeven_upper=None,
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([long_put, short_put]),
        liquidity_score=_liquidity_score([long_put, short_put]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Four-Leg Strategies
# ─────────────────────────────────────────────────────────────────────────────

def calc_iron_condor(
    long_put: StrategyLeg, short_put: StrategyLeg,
    short_call: StrategyLeg, long_call: StrategyLeg,
    lot_size: int,
) -> PayoffResult:
    """BUY far PE + SELL near PE + SELL near CE + BUY far CE."""
    put_credit = short_put.ltp - long_put.ltp
    call_credit = short_call.ltp - long_call.ltp
    total_credit = put_credit + call_credit
    put_width = short_put.strike - long_put.strike
    call_width = long_call.strike - short_call.strike
    max_loss = max(put_width, call_width) - total_credit
    max_profit = max(0.0, total_credit)
    be_lower = short_put.strike - total_credit
    be_upper = short_call.strike + total_credit
    rr = (max_profit / max_loss) if max_loss > 0 else 0.0
    pop = max(0.0, 1.0 - abs(short_put.delta) - abs(short_call.delta))
    all_legs = [long_put, short_put, short_call, long_call]
    net_delta = -short_put.delta + long_put.delta - short_call.delta + long_call.delta
    net_theta = (short_put.theta + short_call.theta) - (long_put.theta + long_call.theta)
    net_vega = -(short_put.vega + short_call.vega) + (long_put.vega + long_call.vega)
    return PayoffResult(
        entry_credit=round(total_credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max(0.0, max_loss), 2),
        breakeven_lower=round(be_lower, 2),
        breakeven_upper=round(be_upper, 2),
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage(all_legs),
        liquidity_score=_liquidity_score(all_legs),
    )


def calc_iron_fly(
    long_put: StrategyLeg, short_put: StrategyLeg,
    short_call: StrategyLeg, long_call: StrategyLeg,
    lot_size: int,
) -> PayoffResult:
    """Iron Fly: short strikes at ATM.  Uses same formula as Iron Condor."""
    return calc_iron_condor(long_put, short_put, short_call, long_call, lot_size)


def calc_long_straddle(
    long_call: StrategyLeg, long_put: StrategyLeg, lot_size: int
) -> PayoffResult:
    """BUY ATM call + BUY ATM put.  Profits from large moves either way."""
    debit = long_call.ltp + long_put.ltp
    be_upper = long_call.strike + debit
    be_lower = long_put.strike - debit
    max_profit = debit * 5   # unlimited in practice; cap at 5× debit for display
    max_loss = debit
    rr = max_profit / max_loss if max_loss > 0 else 0.0
    pop = 0.35  # typical straddle PoP
    net_delta = long_call.delta + long_put.delta
    net_theta = long_call.theta + long_put.theta
    net_vega = long_call.vega + long_put.vega
    return PayoffResult(
        entry_credit=round(-debit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=round(be_lower, 2),
        breakeven_upper=round(be_upper, 2),
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([long_call, long_put]),
        liquidity_score=_liquidity_score([long_call, long_put]),
    )


def calc_short_strangle(
    short_call: StrategyLeg, short_put: StrategyLeg, lot_size: int
) -> PayoffResult:
    """SELL OTM call + SELL OTM put.  Profits from range-bound market."""
    credit = short_call.ltp + short_put.ltp
    be_upper = short_call.strike + credit
    be_lower = short_put.strike - credit
    max_profit = credit
    max_loss = credit * 5   # unlimited in practice; cap at 5× for display
    rr = max_profit / max_loss if max_loss > 0 else 0.0
    pop = max(0.0, 1.0 - abs(short_call.delta) - abs(short_put.delta))
    net_delta = -short_call.delta - short_put.delta
    net_theta = short_call.theta + short_put.theta
    net_vega = -(short_call.vega + short_put.vega)
    return PayoffResult(
        entry_credit=round(credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakeven_lower=round(be_lower, 2),
        breakeven_upper=round(be_upper, 2),
        risk_reward_ratio=round(rr, 3),
        probability_profit=round(pop, 3),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        slippage_estimate=_slippage([short_call, short_put]),
        liquidity_score=_liquidity_score([short_call, short_put]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slippage(legs: list[StrategyLeg]) -> float:
    """Estimate total slippage as 0.5 × avg bid-ask spread × num legs."""
    spreads = [(leg.ask - leg.bid) for leg in legs if leg.ask > leg.bid]
    if not spreads:
        return 0.0
    avg_spread = sum(spreads) / len(spreads)
    return round(0.5 * avg_spread * len(legs), 2)


def _liquidity_score(legs: list[StrategyLeg]) -> float:
    """Score liquidity 0-100.  Penalise wide bid-ask spreads."""
    scores = []
    for leg in legs:
        if leg.ltp > 0 and leg.ask > leg.bid:
            spread_pct = (leg.ask - leg.bid) / leg.ltp * 100
            score = max(0.0, 100.0 - spread_pct * 10)
            scores.append(score)
    return round(sum(scores) / len(scores), 1) if scores else 50.0
