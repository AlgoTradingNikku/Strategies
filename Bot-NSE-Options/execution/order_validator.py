"""
execution/order_validator.py
=============================
Pre-execution order safety validation layer.
Runs safety checks before any order reaches the broker.
Reuses existing risk_manager checks.
"""
from __future__ import annotations
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


def validate_order(
    approved_strategy,
    cfg: dict,
    state: dict,
    settings=None,
) -> "OrderValidationResult":
    from ai.schemas.recommendation import OrderValidationResult

    errors = []
    warnings = []

    if approved_strategy is None:
        return OrderValidationResult(valid=False, errors=["No approved strategy to validate"])

    # ── 1. Kill switch ────────────────────────────────────────────
    # NOTE: risk_manager exposes `is_kill_switch_on(cfg) -> bool`, not
    # `check_kill_switch`. This is a safety-critical gate, so a failure
    # here must fail CLOSED (block the order) rather than be swallowed
    # into a warning.
    try:
        import risk_manager
        if risk_manager.is_kill_switch_on(cfg):
            errors.append("Kill switch active: risk.kill_switch is ON in config.yml")
    except Exception as exc:
        log.error("[order_validator] Kill switch check failed — failing closed: %s", exc)
        errors.append(f"Kill switch check failed (fail-closed): {exc}")

    # ── 2. Market hours ───────────────────────────────────────────
    market_open = True
    if settings and settings.risk_market_hours_check:
        try:
            allowed, reason = risk_manager.check_market_hours(cfg)
            market_open = allowed
            if not allowed:
                errors.append(f"Market hours: {reason}")
        except Exception as exc:
            warnings.append(f"Market hours check skipped: {exc}")

    # ── 3. Quote freshness (re-fetch spot) ────────────────────────
    spot_at_validation = 0.0
    spot_drift_pct = 0.0
    quotes_fresh = False
    try:
        from trading_adapter import get_ltp
        underlying = state.get("underlying", "NIFTY")
        uc = state.get("underlying_config", {})
        current_spot = get_ltp(cfg, underlying, exchange=uc.get("exchange", "NSE_INDEX"))
        spot_at_validation = current_spot
        spot_at_rec = state.get("spot_at_recommendation", 0.0)
        if spot_at_rec > 0 and current_spot > 0:
            spot_drift_pct = abs(current_spot - spot_at_rec) / spot_at_rec * 100
            stale_threshold = float(cfg.get("ai", {}).get("stale_price_threshold_pct", 0.3))
            if spot_drift_pct > stale_threshold:
                errors.append(f"Spot drifted {spot_drift_pct:.2f}% since recommendation. Re-analyse required.")
            else:
                quotes_fresh = True
        else:
            quotes_fresh = current_spot > 0
    except Exception as exc:
        warnings.append(f"Quote freshness check skipped: {exc}")

    # ── 4. Recommendation TTL ─────────────────────────────────────
    if settings and settings.risk_recommendation_ttl:
        rec_until = state.get("recommendation_valid_until")
        if rec_until:
            if isinstance(rec_until, str):
                try:
                    rec_until = datetime.fromisoformat(rec_until)
                except Exception:
                    rec_until = None
            if rec_until and datetime.now() > rec_until:
                errors.append("Recommendation TTL expired. Re-analyse required.")

    # ── 5. Position limit check ───────────────────────────────────
    position_limit_ok = True
    try:
        import trade_db
        open_trades = trade_db.get_active_trades()
        max_positions = int(cfg.get("position_sizing", {}).get("max_concurrent_positions", 3))
        if len(open_trades) >= max_positions:
            errors.append(f"Position limit reached ({len(open_trades)}/{max_positions} open positions)")
            position_limit_ok = False
    except Exception as exc:
        warnings.append(f"Position limit check skipped: {exc}")

    # ── 6. Duplicate guard ────────────────────────────────────────
    duplicate_check_ok = True
    if settings and settings.risk_duplicate_guard:
        try:
            candidate = approved_strategy.candidate if hasattr(approved_strategy, "candidate") else approved_strategy.get("candidate", {})
            legs = candidate.legs if hasattr(candidate, "legs") else candidate.get("legs", [])
            leg_symbols = [l.symbol if hasattr(l, "symbol") else l.get("symbol", "") for l in legs if (l.symbol if hasattr(l, "symbol") else l.get("symbol"))]
            import trade_db
            open_syms = {t["symbol"] for t in trade_db.get_active_trades()}
            overlap = [s for s in leg_symbols if s in open_syms]
            if overlap:
                errors.append(f"Duplicate position: {overlap} already open")
                duplicate_check_ok = False
        except Exception as exc:
            warnings.append(f"Duplicate check skipped: {exc}")

    # ── 7. Lot size / quantity validation ─────────────────────────
    try:
        candidate = approved_strategy.candidate if hasattr(approved_strategy, "candidate") else approved_strategy.get("candidate", {})
        lot_count = candidate.lot_count if hasattr(candidate, "lot_count") else candidate.get("lot_count", 1)
        if lot_count < 1:
            errors.append("Invalid lot count (< 1)")
        legs = candidate.legs if hasattr(candidate, "legs") else candidate.get("legs", [])
        for leg in legs:
            ls = leg.lot_size if hasattr(leg, "lot_size") else leg.get("lot_size", 0)
            sym = leg.symbol if hasattr(leg, "symbol") else leg.get("symbol", "")
            if ls < 1:
                warnings.append(f"Lot size not set for {sym}")
    except Exception as exc:
        warnings.append(f"Lot size check skipped: {exc}")

    # ── 8. Paper trade mode ───────────────────────────────────────
    if settings and settings.risk_paper_trade:
        warnings.append("Paper trade mode active — order will be simulated, not sent to broker")

    # ── Compile result ────────────────────────────────────────────
    valid = len(errors) == 0
    return OrderValidationResult(
        valid=valid,
        errors=errors,
        warnings=warnings,
        market_open=market_open,
        quotes_fresh=quotes_fresh,
        position_limit_ok=position_limit_ok,
        duplicate_check_ok=duplicate_check_ok,
        spot_price_at_validation=spot_at_validation,
        spot_drift_pct=spot_drift_pct,
    )
