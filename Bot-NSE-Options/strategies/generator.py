"""
strategies/generator.py
========================
Generates candidate option strategies from the chain snapshot.
Critical rule: only strikes present in the chain may be used.
The LLM must never see a strike that wasn't in the supplied chain.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import OptionChainSnapshot, OptionStrike
from ai.schemas.regime import MarketRegime
from ai.schemas.strategy import StrategyCandidate, StrategyLeg, STRATEGY_TYPES
from ai.schemas.settings import PlatformSettings
from strategies.payoff import (
    calc_bull_put_spread, calc_bear_call_spread,
    calc_bull_call_spread, calc_bear_put_spread,
    calc_iron_condor, calc_iron_fly,
    calc_long_straddle, calc_short_strangle,
)

log = logging.getLogger("UTBotSRChannelsScanner")

_MAX_SPREAD_PCT = 2.0   # max bid-ask spread as % of LTP
_MIN_OI = 500           # minimum OI on any leg


def generate_candidates(
    snapshot: OptionChainSnapshot,
    regime: MarketRegime,
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> list[StrategyCandidate]:
    """
    Generate all applicable strategy candidates from the chain snapshot.
    Returns list of StrategyCandidate (may be empty if no valid setups found).
    """
    if settings is None:
        settings = PlatformSettings()

    enabled_types = settings.enabled_strategy_types()
    lot_size = _get_lot_size(snapshot)
    atm = snapshot.atm_strike
    gap = _strike_gap(snapshot)
    strikes = sorted(snapshot.available_strikes())

    candidates: list[StrategyCandidate] = []

    # ── Inner helpers ────────────────────────────────────────────────────────

    def get_row(strike: float) -> Optional[OptionStrike]:
        return snapshot.get_strike(strike)

    def leg_ok(leg: Optional[StrategyLeg]) -> bool:
        """Return True only if this leg passes all quality filters."""
        if leg is None:
            return False
        if leg.ltp <= 0:
            return False
        if leg.oi > 0 and leg.oi < _MIN_OI:
            return False
        if leg.ask > leg.bid > 0 and leg.ltp > 0:
            spread_pct = (leg.ask - leg.bid) / leg.ltp * 100
            if spread_pct > _MAX_SPREAD_PCT:
                return False
        return True

    def make_leg(
        strike: float, opt_type: str, action: str, offset: str
    ) -> Optional[StrategyLeg]:
        """Build a StrategyLeg from a chain row.  Returns None if not found."""
        row = get_row(strike)
        if row is None:
            return None
        src = row.ce if opt_type == "CE" else row.pe
        if src is None:
            return None
        return StrategyLeg(
            action=action,
            option_type=opt_type,
            strike=strike,
            symbol=src.symbol,
            ltp=src.ltp,
            bid=src.bid,
            ask=src.ask,
            delta=src.delta,
            gamma=src.gamma,
            theta=src.theta,
            vega=src.vega,
            iv=src.iv,
            oi=src.oi,
            lot_size=lot_size,
            atm_offset=offset,
        )

    def otm_offset(strike: float, opt_type: str) -> str:
        """Return ATM-relative offset string like 'ATM', 'OTM2', 'ITM1'."""
        diff = abs(strike - atm)
        steps = int(round(diff / gap)) if gap > 0 else 0
        if steps == 0:
            return "ATM"
        direction = "OTM" if (
            (opt_type == "CE" and strike > atm) or
            (opt_type == "PE" and strike < atm)
        ) else "ITM"
        return f"{direction}{steps}"

    # ── Bull Put Spread (mildly bullish / neutral) ───────────────────────────
    if "bull_put_spread" in enabled_types and regime.regime not in (
        "STRONG_BEARISH", "BEARISH", "NO_TRADE"
    ):
        short_strike = _nearest_strike(strikes, atm - gap)
        long_strike = _nearest_strike(strikes, atm - 2 * gap)
        if short_strike and long_strike and short_strike != long_strike:
            sl = make_leg(short_strike, "PE", "SELL", otm_offset(short_strike, "PE"))
            ll = make_leg(long_strike, "PE", "BUY", otm_offset(long_strike, "PE"))
            if sl and ll and leg_ok(sl) and leg_ok(ll):
                payoff = calc_bull_put_spread(sl, ll, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="bull_put_spread",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[sl, ll],
                        payoff=payoff,
                        regime_aligned=regime.regime in (
                            "BULLISH", "STRONG_BULLISH", "NEUTRAL"
                        ),
                        lot_count=1,
                    ))

    # ── Bear Call Spread (mildly bearish / neutral) ──────────────────────────
    if "bear_call_spread" in enabled_types and regime.regime not in (
        "STRONG_BULLISH", "BULLISH", "NO_TRADE"
    ):
        short_strike = _nearest_strike(strikes, atm + gap)
        long_strike = _nearest_strike(strikes, atm + 2 * gap)
        if short_strike and long_strike and short_strike != long_strike:
            sl = make_leg(short_strike, "CE", "SELL", otm_offset(short_strike, "CE"))
            ll = make_leg(long_strike, "CE", "BUY", otm_offset(long_strike, "CE"))
            if sl and ll and leg_ok(sl) and leg_ok(ll):
                payoff = calc_bear_call_spread(sl, ll, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="bear_call_spread",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[sl, ll],
                        payoff=payoff,
                        regime_aligned=regime.regime in (
                            "BEARISH", "STRONG_BEARISH", "NEUTRAL"
                        ),
                        lot_count=1,
                    ))

    # ── Bull Call Spread (strongly bullish) ──────────────────────────────────
    if "bull_call_spread" in enabled_types and regime.regime in (
        "BULLISH", "STRONG_BULLISH"
    ):
        long_strike = _nearest_strike(strikes, atm)
        short_strike = _nearest_strike(strikes, atm + 2 * gap)
        if long_strike and short_strike and long_strike != short_strike:
            ll = make_leg(long_strike, "CE", "BUY", otm_offset(long_strike, "CE"))
            sl = make_leg(short_strike, "CE", "SELL", otm_offset(short_strike, "CE"))
            if ll and sl and leg_ok(ll) and leg_ok(sl):
                payoff = calc_bull_call_spread(ll, sl, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="bull_call_spread",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[ll, sl],
                        payoff=payoff,
                        regime_aligned=True,
                        lot_count=1,
                    ))

    # ── Bear Put Spread (strongly bearish) ───────────────────────────────────
    if "bear_put_spread" in enabled_types and regime.regime in (
        "BEARISH", "STRONG_BEARISH"
    ):
        long_strike = _nearest_strike(strikes, atm)
        short_strike = _nearest_strike(strikes, atm - 2 * gap)
        if long_strike and short_strike and long_strike != short_strike:
            ll = make_leg(long_strike, "PE", "BUY", otm_offset(long_strike, "PE"))
            sl = make_leg(short_strike, "PE", "SELL", otm_offset(short_strike, "PE"))
            if ll and sl and leg_ok(ll) and leg_ok(sl):
                payoff = calc_bear_put_spread(ll, sl, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="bear_put_spread",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[ll, sl],
                        payoff=payoff,
                        regime_aligned=True,
                        lot_count=1,
                    ))

    # ── Iron Condor (neutral / mild trend) ───────────────────────────────────
    if "iron_condor" in enabled_types and regime.regime in (
        "NEUTRAL", "BULLISH", "BEARISH"
    ):
        lp_s = _nearest_strike(strikes, atm - 4 * gap)
        sp_s = _nearest_strike(strikes, atm - 2 * gap)
        sc_s = _nearest_strike(strikes, atm + 2 * gap)
        lc_s = _nearest_strike(strikes, atm + 4 * gap)
        if lp_s and sp_s and sc_s and lc_s and len({lp_s, sp_s, sc_s, lc_s}) == 4:
            lp = make_leg(lp_s, "PE", "BUY", "OTM4")
            sp = make_leg(sp_s, "PE", "SELL", "OTM2")
            sc = make_leg(sc_s, "CE", "SELL", "OTM2")
            lc = make_leg(lc_s, "CE", "BUY", "OTM4")
            if all(x is not None for x in [lp, sp, sc, lc]) and all(
                leg_ok(x) for x in [lp, sp, sc, lc]
            ):
                payoff = calc_iron_condor(lp, sp, sc, lc, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="iron_condor",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[lp, sp, sc, lc],
                        payoff=payoff,
                        regime_aligned=regime.regime == "NEUTRAL",
                        lot_count=1,
                    ))

    # ── Iron Fly (very tight range / very neutral) ───────────────────────────
    if "iron_fly" in enabled_types and regime.regime == "NEUTRAL":
        lp_s = _nearest_strike(strikes, atm - 3 * gap)
        lc_s = _nearest_strike(strikes, atm + 3 * gap)
        atm_s = _nearest_strike(strikes, atm)
        if lp_s and lc_s and atm_s and lp_s != atm_s and lc_s != atm_s:
            lp = make_leg(lp_s, "PE", "BUY", "OTM3")
            sp = make_leg(atm_s, "PE", "SELL", "ATM")
            sc = make_leg(atm_s, "CE", "SELL", "ATM")
            lc = make_leg(lc_s, "CE", "BUY", "OTM3")
            if all(x is not None for x in [lp, sp, sc, lc]) and all(
                leg_ok(x) for x in [lp, sp, sc, lc]
            ):
                payoff = calc_iron_fly(lp, sp, sc, lc, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="iron_fly",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[lp, sp, sc, lc],
                        payoff=payoff,
                        regime_aligned=True,
                        lot_count=1,
                    ))

    # ── Long Straddle (high vol / event) ─────────────────────────────────────
    if "long_straddle" in enabled_types and regime.vol_regime == "HIGH":
        atm_s = _nearest_strike(strikes, atm)
        if atm_s:
            atm_call = make_leg(atm_s, "CE", "BUY", "ATM")
            atm_put = make_leg(atm_s, "PE", "BUY", "ATM")
            if atm_call and atm_put and leg_ok(atm_call) and leg_ok(atm_put):
                payoff = calc_long_straddle(atm_call, atm_put, lot_size)
                candidates.append(StrategyCandidate(
                    strategy_type="long_straddle",
                    underlying=snapshot.underlying,
                    expiry_date=snapshot.expiry_date,
                    legs=[atm_call, atm_put],
                    payoff=payoff,
                    regime_aligned=True,
                    lot_count=1,
                ))

    # ── Short Strangle (low-to-normal vol / range-bound) ─────────────────────
    if "short_strangle" in enabled_types and regime.vol_regime != "HIGH" and regime.regime not in (
        "STRONG_BULLISH", "STRONG_BEARISH", "NO_TRADE"
    ):
        sc_s = _nearest_strike(strikes, atm + 2 * gap)
        sp_s = _nearest_strike(strikes, atm - 2 * gap)
        if sc_s and sp_s and sc_s != sp_s:
            sc = make_leg(sc_s, "CE", "SELL", "OTM2")
            sp = make_leg(sp_s, "PE", "SELL", "OTM2")
            if sc and sp and leg_ok(sc) and leg_ok(sp):
                payoff = calc_short_strangle(sc, sp, lot_size)
                if payoff.max_profit > 0:
                    candidates.append(StrategyCandidate(
                        strategy_type="short_strangle",
                        underlying=snapshot.underlying,
                        expiry_date=snapshot.expiry_date,
                        legs=[sc, sp],
                        payoff=payoff,
                        regime_aligned=regime.regime == "NEUTRAL",
                        lot_count=1,
                    ))

    log.info(
        "[Generator] Generated %d candidates for %s %s (regime=%s)",
        len(candidates), snapshot.underlying, snapshot.expiry_date, regime.regime,
    )
    return candidates


# ── Module-level helpers ──────────────────────────────────────────────────────

def _strike_gap(snapshot: OptionChainSnapshot) -> float:
    """Return the smallest gap between consecutive strikes."""
    strikes = sorted(snapshot.available_strikes())
    return abs(strikes[1] - strikes[0]) if len(strikes) >= 2 else 50.0


def _get_lot_size(snapshot: OptionChainSnapshot) -> int:
    """Infer lot size from first chain row that has it populated."""
    for s in snapshot.chain:
        if s.ce and s.ce.lot_size > 0:
            return s.ce.lot_size
        if s.pe and s.pe.lot_size > 0:
            return s.pe.lot_size
    return 75  # NIFTY default


def _nearest_strike(strikes: list[float], target: float) -> Optional[float]:
    """Return the strike in the list nearest to target."""
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - target))
