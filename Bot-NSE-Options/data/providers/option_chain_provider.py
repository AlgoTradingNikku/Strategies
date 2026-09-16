"""
data/providers/option_chain_provider.py
========================================
Single entry point for all market data ingestion.
Wraps OpenAlgo SDK calls and normalises responses into canonical Pydantic models.
All methods are fail-open — errors are logged, never raised to callers.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

_bot_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import (
    OptionChainSnapshot, OptionStrike, OptionLeg, OISignals,
)

log = logging.getLogger("UTBotSRChannelsScanner")


def _get_oa_client(cfg: dict):
    """Reuse trading_adapter's cached OpenAlgo client."""
    from trading_adapter import _get_oa_client as _ta_client
    return _ta_client(cfg.get("openalgo", {}))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_snapshot(underlying_config: dict, cfg: dict) -> Optional[OptionChainSnapshot]:
    """
    Fetch a full, timestamped option chain snapshot for the given underlying.
    Returns None on any failure (fail-open).
    """
    symbol = underlying_config.get("symbol", "NIFTY")
    exchange = underlying_config.get("exchange", "NSE_INDEX")
    strikes_each_side = int(underlying_config.get("strikes_each_side", 10))
    expiry = underlying_config.get("expiry_date", "") or ""

    # Auto-detect nearest expiry if not configured in underlyings block
    if not expiry:
        expiry = get_nearest_expiry(symbol, exchange, cfg) or ""

    # Last-resort fallback: use the top-level options.expiry_date from config
    if not expiry:
        expiry = cfg.get("options", {}).get("expiry_date", "") or ""
        if expiry:
            log.info("[%s] Using options.expiry_date fallback: %s", symbol, expiry)

    if not expiry:
        log.warning("[%s] Could not determine expiry date — skipping snapshot fetch", symbol)
        return None

    try:
        from broker_retry import with_retry
        import time as _time
        client = _get_oa_client(cfg)
        strike_count = strikes_each_side * 2  # total strikes (both sides)

        def _do_fetch():
            return client.optionchain(
                underlying=symbol,
                exchange=exchange,
                expiry_date=expiry,
                strike_count=strike_count,
            )

        # Retry up to 3 times with short back-off — the scanner may be holding
        # the connection momentarily during its scan cycle.
        last_exc = None
        for attempt in range(3):
            if attempt > 0:
                _time.sleep(1.5 * attempt)
                log.debug("[%s] fetch_snapshot retry %d", symbol, attempt)
            try:
                raw = with_retry(_do_fetch, cfg=cfg, op_name=f"optionchain[{symbol}]")
                result = _normalise_chain_response(raw, symbol, exchange, expiry)
                if result is not None:
                    return result
                # status was error but not an exception — wait and retry
                last_exc = Exception(f"optionchain returned None on attempt {attempt+1}")
            except Exception as exc:
                last_exc = exc

        log.warning("[%s] fetch_snapshot failed after 3 attempts: %s", symbol, last_exc)
        return None

    except Exception as exc:
        log.warning("[%s] fetch_snapshot failed: %s", symbol, exc)
        return None


def fetch_greeks_for_chain(
    snapshot: OptionChainSnapshot,
    cfg: dict,
    strikes_around_atm: int = 5,
) -> OptionChainSnapshot:
    """
    Enrich snapshot legs with Greeks from client.optiongreeks().
    Only fetches for ±strikes_around_atm around ATM to limit API calls.
    Returns the same snapshot with legs updated in-place (fail-open).
    """
    if not cfg.get("toggles", {}).get("data_option_greeks", True):
        log.debug("[Greeks] data_option_greeks toggle is OFF — skipping")
        return snapshot

    try:
        from broker_retry import with_retry
        client = _get_oa_client(cfg)
        atm = snapshot.atm_strike
        option_exchange = cfg.get("underlyings", [{}])[0].get("option_exchange", "NFO")

        # Find underlying symbol for this snapshot
        underlying_sym = snapshot.underlying
        underlying_exch = snapshot.exchange

        # Determine which strikes to enrich (±strikes_around_atm from ATM)
        target_strikes = [
            s.strike for s in snapshot.chain
            if abs(s.strike - atm) <= strikes_around_atm * _guess_strike_gap(snapshot)
        ]

        for strike_row in snapshot.chain:
            if strike_row.strike not in target_strikes:
                continue
            for opt_type, leg_attr in [("CE", "ce"), ("PE", "pe")]:
                leg: Optional[OptionLeg] = getattr(strike_row, leg_attr, None)
                if leg is None or not leg.symbol:
                    continue
                try:
                    def _do_greeks(sym=leg.symbol):
                        return client.optiongreeks(
                            symbol=sym,
                            exchange=option_exchange,
                            interest_rate=0.0,
                            underlying_symbol=underlying_sym,
                            underlying_exchange=underlying_exch,
                        )
                    resp = with_retry(_do_greeks, cfg=cfg, op_name=f"greeks[{leg.symbol}]")
                    if isinstance(resp, dict) and resp.get("status") == "success":
                        greeks = resp.get("greeks", {})
                        leg.delta = float(greeks.get("delta") or 0.0)
                        leg.gamma = float(greeks.get("gamma") or 0.0)
                        leg.theta = float(greeks.get("theta") or 0.0)
                        leg.vega = float(greeks.get("vega") or 0.0)
                        leg.iv = float(resp.get("implied_volatility") or 0.0)
                except Exception as _exc:
                    log.debug("[%s] Greeks fetch failed: %s", leg.symbol, _exc)
                    continue

    except Exception as exc:
        log.warning("[%s] fetch_greeks_for_chain failed: %s", snapshot.underlying, exc)

    return snapshot


def validate_freshness(snapshot: OptionChainSnapshot, cfg: dict) -> tuple[bool, float]:
    """
    Check if snapshot is fresh enough for analysis.
    Returns (is_fresh: bool, age_seconds: float).
    Updates snapshot.freshness_status in place.
    """
    max_sec = float(cfg.get("ai", {}).get("freshness_max_sec", 10))
    age = snapshot.age_seconds

    if age <= max_sec:
        snapshot.freshness_status = "fresh"
        return True, age
    elif age <= max_sec * 2:
        snapshot.freshness_status = "stale"
        log.warning("[%s] Snapshot is stale (%.1fs > %.1fs)", snapshot.underlying, age, max_sec)
        return False, age
    else:
        snapshot.freshness_status = "expired"
        log.warning("[%s] Snapshot expired (%.1fs > %.1fs)", snapshot.underlying, age, max_sec * 2)
        return False, age


def fetch_vix(cfg: dict) -> float:
    """
    Fetch India VIX level. Returns 0.0 on failure (fail-open).
    Reuses trading_adapter.get_ltp to avoid code duplication.
    """
    if not cfg.get("toggles", {}).get("data_india_vix", True):
        log.debug("[VIX] data_india_vix toggle is OFF")
        return 0.0
    try:
        from trading_adapter import get_ltp
        vix_cfg = cfg.get("alpha_enhancers", {}).get("vix_regime", {})
        vix_symbol = vix_cfg.get("vix_symbol", "INDIAVIX")
        vix_exchange = vix_cfg.get("vix_exchange", "NSE_INDEX")
        vix = get_ltp(cfg, vix_symbol, exchange=vix_exchange)
        log.debug("[VIX] India VIX = %.2f", vix)
        return vix
    except Exception as exc:
        log.warning("[VIX] fetch_vix failed: %s", exc)
        return 0.0


def fetch_spot_history(
    symbol: str,
    timeframe: str,
    cfg: dict,
    exchange: str = "NSE_INDEX",
) -> Optional[pd.DataFrame]:
    """
    Fetch spot OHLCV history. Thin wrapper around scanner.fetch_history.
    Returns None on failure (fail-open).
    """
    if not cfg.get("toggles", {}).get("data_spot_futures", True):
        log.debug("[SpotHistory] data_spot_futures toggle is OFF")
        return None
    try:
        from scanner import fetch_history
        return fetch_history(symbol, timeframe, cfg, exchange=exchange)
    except Exception as exc:
        log.warning("[SpotHistory] fetch_spot_history(%s, %s) failed: %s", symbol, timeframe, exc)
        return None


def get_nearest_expiry(symbol: str, exchange: str, cfg: dict) -> Optional[str]:
    """
    Auto-detect the nearest weekly expiry for the given underlying.
    Uses client.expiry() API with instrumenttype=OPTIDX (index options).
    Returns expiry string like '24JUL25' or None.
    """
    try:
        from broker_retry import with_retry
        client = _get_oa_client(cfg)
        # Option exchange for expiry lookup (NFO for NSE index options)
        uc_list = cfg.get("underlyings", [])
        uc = next((u for u in uc_list if u.get("symbol") == symbol), {})
        option_exchange = uc.get("option_exchange", "NFO")

        def _do_expiry():
            return client.expiry(
                symbol=symbol,
                exchange=option_exchange,
                instrumenttype="options",
            )

        resp = with_retry(_do_expiry, cfg=cfg, op_name=f"expiry[{symbol}]")
        if isinstance(resp, dict) and resp.get("status") == "success":
            expiries = resp.get("data", [])
            if expiries:
                # expiry() returns "22-SEP-26"; optionchain needs "22SEP26" — strip dashes
                raw = expiries[0] if isinstance(expiries[0], str) else str(expiries[0])
                nearest = raw.replace("-", "")
                log.info("[%s] Auto-detected nearest expiry: %s (raw: %s)", symbol, nearest, raw)
                return nearest
    except Exception as exc:
        log.warning("[%s] get_nearest_expiry failed: %s", symbol, exc)
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_chain_response(
    raw: dict,
    symbol: str,
    exchange: str,
    expiry: str,
) -> Optional[OptionChainSnapshot]:
    """
    Normalise the raw optionchain API response into an OptionChainSnapshot.
    Only includes strikes where BOTH ce and pe data are present.
    """
    if not isinstance(raw, dict):
        log.warning("[%s] optionchain returned non-dict: %s", symbol, type(raw))
        return None
    if raw.get("status") != "success":
        log.warning("[%s] optionchain status not success: %s", symbol, raw.get("status"))
        return None

    underlying_ltp = float(raw.get("underlying_ltp") or 0.0)
    atm_strike = float(raw.get("atm_strike") or 0.0)
    raw_chain = raw.get("chain", [])

    if not raw_chain:
        log.warning("[%s] optionchain returned empty chain", symbol)
        return None

    strikes: list[OptionStrike] = []
    for row in raw_chain:
        strike_price = float(row.get("strike", 0))
        ce_data = row.get("ce")
        pe_data = row.get("pe")

        # Only include strikes with both legs present
        if not ce_data or not pe_data:
            continue

        ce_leg = _build_option_leg(ce_data)
        pe_leg = _build_option_leg(pe_data)
        strikes.append(OptionStrike(strike=strike_price, ce=ce_leg, pe=pe_leg))

    if not strikes:
        log.warning("[%s] No complete strikes (CE+PE) in chain response", symbol)
        return None

    snapshot = OptionChainSnapshot(
        underlying=symbol,
        exchange=exchange,
        expiry_date=expiry,
        underlying_ltp=underlying_ltp,
        atm_strike=atm_strike,
        chain=strikes,
        fetched_at=datetime.now(),
        freshness_status="fresh",
    )
    log.info(
        "[%s] Snapshot fetched: LTP=%.2f ATM=%.0f Strikes=%d Expiry=%s",
        symbol, underlying_ltp, atm_strike, len(strikes), expiry,
    )
    return snapshot


def _build_option_leg(data: dict) -> OptionLeg:
    """Build an OptionLeg from a raw chain CE/PE dict."""
    return OptionLeg(
        symbol=str(data.get("symbol") or ""),
        ltp=float(data.get("ltp") or 0.0),
        bid=float(data.get("bid") or 0.0),
        ask=float(data.get("ask") or 0.0),
        volume=int(data.get("volume") or 0),
        oi=int(data.get("oi") or 0),
        oi_change=int(data.get("oi_change") or 0),
        lot_size=int(data.get("lotsize") or 0),
        label=str(data.get("label") or ""),
        # Greeks enriched later by fetch_greeks_for_chain
        iv=0.0, delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
    )


def _guess_strike_gap(snapshot: OptionChainSnapshot) -> float:
    """Infer strike gap from the chain (difference between first two strikes)."""
    strikes = sorted(snapshot.available_strikes())
    if len(strikes) >= 2:
        return abs(strikes[1] - strikes[0])
    return 50.0  # safe default for NIFTY
