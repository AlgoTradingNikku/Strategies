from typing import Dict, Any, Tuple, Optional
from .models import Position

def evaluate_position_rules(pos: Position, cfg: Dict[str, Any]) -> Tuple[str, Optional[float], str]:
    """
    Evaluates stop loss, target, trailing SL, and profit lock for option position.
    Returns (action_type, new_sl_or_exit_price, reason)
    """
    tm_cfg = cfg.get("trade_management", {})
    ret_pct = pos.return_pct

    # Check hard Stop Loss
    sl_pct = float(tm_cfg.get("stop_loss_pct", 20.0))
    if ret_pct <= -sl_pct:
        return ("EXIT", pos.current_price, "STOP_LOSS_REACHED")

    # Check Target
    target_pct = float(tm_cfg.get("target_pct", 40.0))
    if ret_pct >= target_pct:
        return ("EXIT", pos.current_price, "TARGET_REACHED")

    # Check Trailing Stop Loss
    tsl_cfg = tm_cfg.get("trailing_sl", {})
    if tsl_cfg.get("enabled", True):
        activation = float(tsl_cfg.get("activation_pct", 15.0))
        if ret_pct >= activation:
            dist_pct = 10.0
            tiers = tsl_cfg.get("tiers", [])
            for t in sorted(tiers, key=lambda x: x.get("min_gain_pct", 0), reverse=True):
                if ret_pct >= float(t.get("min_gain_pct", 0)):
                    dist_pct = float(t.get("distance_pct", 10.0))
                    break

            new_sl_pct = ret_pct - dist_pct
            if pos.action == "BUY":
                calculated_sl = pos.entry_price * (1.0 + new_sl_pct / 100.0)
                if pos.trailing_sl is None or calculated_sl > pos.trailing_sl:
                    return ("UPDATE_SL", calculated_sl, "TRAILING_SL_UPDATED")

    return ("HOLD", None, "NO_ACTION")
