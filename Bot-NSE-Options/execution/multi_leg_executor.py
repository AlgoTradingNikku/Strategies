"""
execution/multi_leg_executor.py
================================
Multi-leg option order execution via OpenAlgo optionsmultiorder.
Converts ScoredCandidate legs to the optionsmultiorder legs format.
Falls back to optionsorder for single-leg strategies.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

log = logging.getLogger("UTBotSRChannelsScanner")


def execute_strategy(approved_strategy, cfg: dict, settings=None) -> dict:
    """
    Execute the approved multi-leg strategy via OpenAlgo.
    If paper_trade is enabled → simulate only.
    Returns execution result dict.
    """
    if approved_strategy is None:
        return {"status": "error", "message": "No strategy to execute"}

    # Support both Pydantic model and plain dict (DB-restored)
    if hasattr(approved_strategy, "candidate"):
        candidate = approved_strategy.candidate
    elif isinstance(approved_strategy, dict):
        cand_data = approved_strategy.get("candidate", {})
        if isinstance(cand_data, dict):
            from ai.schemas.strategy import StrategyCandidate
            try:
                candidate = StrategyCandidate(**cand_data)
            except Exception as exc:
                return {"status": "error", "message": f"Could not reconstruct candidate from dict: {exc}"}
        else:
            candidate = cand_data
    else:
        return {"status": "error", "message": "Unrecognised approved_strategy type"}

    # Normalise settings
    if settings and isinstance(settings, dict):
        from ai.schemas.settings import PlatformSettings
        try:
            settings = PlatformSettings(**{k: v for k, v in settings.items() if k in PlatformSettings.model_fields})
        except Exception:
            settings = PlatformSettings()

    # Paper trade mode — simulate only
    if settings and settings.risk_paper_trade:
        log.info("[Executor] PAPER TRADE: %s %s", candidate.strategy_type, candidate.underlying)
        legs_out = []
        for l in candidate.legs:
            legs_out.append(l.model_dump() if hasattr(l, "model_dump") else (l.dict() if hasattr(l, "dict") else l))
        return {
            "status": "paper_trade",
            "message": "Paper trade — no real order placed",
            "strategy_type": candidate.strategy_type,
            "legs": legs_out,
        }

    try:
        from trading_adapter import _get_oa_client
        from broker_retry import with_retry
        oa_cfg = cfg.get("openalgo", {})
        client = _get_oa_client(oa_cfg)

        underlying = candidate.underlying
        expiry_date = candidate.expiry_date
        uc = next(
            (u for u in cfg.get("underlyings", []) if u.get("symbol") == underlying),
            {}
        )
        exchange = uc.get("exchange", "NSE_INDEX")
        product = cfg.get("trading", {}).get("options", {}).get("product", "NRML")
        strategy_name = cfg.get("trading", {}).get("strategy_name", "AI_Options_Platform")

        # Build legs for optionsmultiorder
        legs = _build_multiorder_legs(candidate, uc)

        if not legs:
            return {"status": "error", "message": "Could not build order legs"}

        def _do_execute():
            if len(legs) == 1:
                # Single leg — use optionsorder
                leg = legs[0]
                return client.optionsorder(
                    strategy=strategy_name,
                    underlying=underlying,
                    exchange=exchange,
                    expiry_date=expiry_date,
                    offset=leg.get("offset", "ATM"),
                    option_type=leg.get("option_type", "CE"),
                    action=leg.get("action", "BUY"),
                    quantity=leg.get("quantity", uc.get("lot_size", 75)),
                    price_type="MARKET",
                    product=product,
                )
            else:
                # Multi-leg — use optionsmultiorder
                return client.optionsmultiorder(
                    strategy=strategy_name,
                    underlying=underlying,
                    exchange=exchange,
                    expiry_date=expiry_date,
                    legs=legs,
                )

        result = with_retry(_do_execute, cfg=cfg, op_name=f"execute[{candidate.strategy_type}]")
        log.info("[Executor] Execution result: %s", result)

        if isinstance(result, dict):
            return result
        return {"status": "success", "response": str(result)}

    except Exception as exc:
        log.error("[Executor] execute_strategy failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def _build_multiorder_legs(candidate, underlying_config: dict) -> list:
    """
    Convert StrategyCandidate legs to optionsmultiorder legs format.
    Uses atm_offset from each StrategyLeg.
    """
    lot_size = underlying_config.get("lot_size", 75)
    legs = []
    for leg in candidate.legs:
        offset = leg.atm_offset or "ATM"
        legs.append({
            "offset": offset,
            "option_type": leg.option_type,
            "action": leg.action,
            "quantity": lot_size * candidate.lot_count,
        })
    return legs
