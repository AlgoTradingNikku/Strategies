"""
strategy_combos.py
==================
Builds ready-to-execute multi-leg option strategy candidates from scanner signals.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import risk_manager
except Exception:  # pragma: no cover
    risk_manager = None

log = logging.getLogger("UTBotSRChannelsScanner")
_GRADE = {"A": 4, "B": 3, "C": 2, "D": 1}


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return d


def _gv(g: Any) -> int:
    return _GRADE.get(str(g or "").upper(), 0)


def _strike(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _replace_strike(symbol: str, old: float, new: float, opt_type: str) -> str:
    old_s, new_s = _strike(old), _strike(new)
    if old_s and old_s in symbol:
        return symbol.replace(old_s, new_s, 1)
    return f"{symbol[:-2]}{new_s}{opt_type}" if symbol.endswith(("CE", "PE")) else symbol


def _cid(combo_type: str, legs: List[Dict[str, Any]]) -> str:
    raw = combo_type + "|" + "|".join(f"{l['action']}:{l['symbol']}:{l['quantity']}" for l in legs)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("strategy_combos", {}) or {}


def _enabled_types(cfg: Dict[str, Any]) -> set[str]:
    raw = _cfg(cfg).get("enabled_types") or ["bull_call_spread", "bear_put_spread", "long_straddle", "long_strangle"]
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def _normalise(sig: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(sig)
    s["price"] = _f(s.get("price"))
    s["strike"] = _f(s.get("strike"))
    s["setup_score"] = _f(s.get("setup_score"))
    s["lot_size"] = max(1, _i(s.get("lot_size"), 65))
    s["sized_quantity"] = _i(s.get("sized_quantity"), 0)
    s["grade"] = str(s.get("grade") or "D").upper()
    s["signal_type"] = str(s.get("signal_type") or "BUY").upper()
    s["option_type"] = str(s.get("option_type") or ("CE" if str(s.get("symbol", "")).endswith("CE") else "PE")).upper()
    return s


def _passes(sig: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    scfg = _cfg(cfg)
    min_score = _f(scfg.get("min_score", cfg.get("trading", {}).get("min_score", 60)), 60)
    min_grade = str(scfg.get("min_grade", cfg.get("trading", {}).get("min_grade", "B"))).upper()
    return sig["setup_score"] >= min_score and _gv(sig["grade"]) >= _gv(min_grade)


def _rr(max_profit_pts: Optional[float], max_loss_pts: Optional[float]) -> str:
    if not max_loss_pts or max_loss_pts <= 0:
        return "∞" if max_profit_pts is None or max_profit_pts > 0 else "—"
    if max_profit_pts is None:
        return "∞"
    return f"1:{max_profit_pts / max_loss_pts:.2f}"


def _find_signal(signals: List[Dict[str, Any]], option_type: str, strike: float) -> Optional[Dict[str, Any]]:
    for sig in signals:
        if sig.get("option_type") == option_type and sig.get("strike") == strike:
            return sig
    return None


def _vertical(sig: Dict[str, Any], all_signals: List[Dict[str, Any]], cfg: Dict[str, Any], combo_type: str) -> Optional[Dict[str, Any]]:
    scfg = _cfg(cfg)
    step = _f(cfg.get("options", {}).get("strike_gap"), 50.0)
    width_steps = max(1, _i(scfg.get("vertical_width_steps"), 1))
    hedge_price_pct = max(0.01, _f(scfg.get("hedge_price_pct"), 0.45))
    direction = 1 if combo_type == "bull_call_spread" else -1
    name = "Bull Call Spread" if combo_type == "bull_call_spread" else "Bear Put Spread"
    opt_type = sig["option_type"]
    strike = sig["strike"]
    hedge_strike = strike + direction * step * width_steps
    hedge = _find_signal(all_signals, opt_type, hedge_strike)
    hedge_symbol = hedge.get("symbol") if hedge else _replace_strike(str(sig.get("symbol")), strike, hedge_strike, opt_type)
    hedge_price = _f(hedge.get("price")) if hedge else round(sig["price"] * hedge_price_pct, 2)
    debit = max(0.0, sig["price"] - hedge_price)
    if sig["price"] <= 0 or debit <= 0:
        return None
    width = abs(hedge_strike - strike)
    max_profit_pts = max(0.0, width - debit)
    max_loss_pts = debit
    qty = max(1, _i(sig.get("sized_quantity") or sig.get("lot_size"), sig.get("lot_size", 65)))

    legs = [
        {"leg_role": "long_signal", "symbol": sig.get("symbol"), "option_type": opt_type, "strike": strike, "action": "BUY", "quantity": qty, "price": round(sig["price"], 2), "side": "debit"},
        {"leg_role": "short_hedge", "symbol": hedge_symbol, "option_type": opt_type, "strike": hedge_strike, "action": "SELL", "quantity": qty, "price": round(hedge_price, 2), "side": "credit"},
    ]
    breakeven = strike + debit if combo_type == "bull_call_spread" else strike - debit
    return {
        "combo_id": _cid(combo_type, legs), "combo_name": name, "combo_type": combo_type,
        "direction": "BULLISH" if combo_type == "bull_call_spread" else "BEARISH",
        "underlying": sig.get("underlying"), "expiry": sig.get("expiry"), "primary_symbol": sig.get("symbol"),
        "signal_source": "UTBot/S-R", "grade": sig.get("grade"), "setup_score": round(sig["setup_score"], 2),
        "confidence": round(min(100.0, sig["setup_score"] + 5.0), 2), "entry_debit": round(debit, 2),
        "entry_credit": 0.0, "net_premium": round(debit, 2), "max_profit": round(max_profit_pts * qty, 2),
        "max_loss": round(max_loss_pts * qty, 2), "max_profit_points": round(max_profit_pts, 2),
        "max_loss_points": round(max_loss_pts, 2), "breakeven": round(breakeven, 2),
        "risk_reward": _rr(max_profit_pts, max_loss_pts), "lot_size": qty, "legs": legs,
        "created_at": datetime.now().strftime("%H:%M:%S"),
        "notes": f"Buy signal hedged with short {opt_type} {int(width)} points away.",
    }


def _vol_combo(ce: Dict[str, Any], pe: Dict[str, Any], combo_type: str) -> Optional[Dict[str, Any]]:
    qty = max(1, min(_i(ce.get("sized_quantity") or ce.get("lot_size"), 65), _i(pe.get("sized_quantity") or pe.get("lot_size"), 65)))
    debit = ce["price"] + pe["price"]
    if debit <= 0:
        return None
    ce_strike, pe_strike = ce["strike"], pe["strike"]
    lower, upper = min(ce_strike, pe_strike), max(ce_strike, pe_strike)
    legs = [
        {"leg_role": "long_ce", "symbol": ce.get("symbol"), "option_type": "CE", "strike": ce_strike, "action": "BUY", "quantity": qty, "price": round(ce["price"], 2), "side": "debit"},
        {"leg_role": "long_pe", "symbol": pe.get("symbol"), "option_type": "PE", "strike": pe_strike, "action": "BUY", "quantity": qty, "price": round(pe["price"], 2), "side": "debit"},
    ]
    score = (ce["setup_score"] + pe["setup_score"]) / 2.0
    return {
        "combo_id": _cid(combo_type, legs), "combo_name": "Long Straddle" if combo_type == "long_straddle" else "Long Strangle",
        "combo_type": combo_type, "direction": "VOLATILITY", "underlying": ce.get("underlying") or pe.get("underlying"),
        "expiry": ce.get("expiry") or pe.get("expiry"), "primary_symbol": f"{ce.get('symbol')} + {pe.get('symbol')}",
        "signal_source": "UTBot/S-R dual-side momentum", "grade": ce.get("grade") if _gv(ce.get("grade")) <= _gv(pe.get("grade")) else pe.get("grade"),
        "setup_score": round(score, 2), "confidence": round(min(100.0, score + 3.0), 2),
        "entry_debit": round(debit, 2), "entry_credit": 0.0, "net_premium": round(debit, 2),
        "max_profit": None, "max_loss": round(debit * qty, 2), "max_profit_points": None,
        "max_loss_points": round(debit, 2), "breakeven": [round(lower - debit, 2), round(upper + debit, 2)],
        "risk_reward": "∞", "lot_size": qty, "legs": legs, "created_at": datetime.now().strftime("%H:%M:%S"),
        "notes": "Defined max loss with convex move payoff before expiry.",
    }


def generate_strategy_combos(scan_results: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    scfg = _cfg(cfg)
    if not scfg.get("enabled", True):
        return []
    max_candidates = max(1, _i(scfg.get("max_candidates"), 8))
    enabled = _enabled_types(cfg)
    signals = [_normalise(s) for s in (list(scan_results.get("buy_results") or []) + list(scan_results.get("sell_results") or []))]
    qualified = [s for s in signals if _passes(s, cfg)]
    ce_buys = sorted([s for s in qualified if s["signal_type"] == "BUY" and s["option_type"] == "CE"], key=lambda s: (_gv(s["grade"]), s["setup_score"]), reverse=True)
    pe_buys = sorted([s for s in qualified if s["signal_type"] == "BUY" and s["option_type"] == "PE"], key=lambda s: (_gv(s["grade"]), s["setup_score"]), reverse=True)
    combos: List[Dict[str, Any]] = []
    if "bull_call_spread" in enabled:
        combos.extend(c for c in (_vertical(s, signals, cfg, "bull_call_spread") for s in ce_buys) if c)
    if "bear_put_spread" in enabled:
        combos.extend(c for c in (_vertical(s, signals, cfg, "bear_put_spread") for s in pe_buys) if c)
    if ce_buys and pe_buys:
        step = max(1.0, _f(cfg.get("options", {}).get("strike_gap"), 50.0))
        max_gap = max(0, _i(scfg.get("volatility_max_gap_steps"), 1)) * step
        for ce in ce_buys[:3]:
            pe = min(pe_buys, key=lambda p: abs(p["strike"] - ce["strike"]))
            gap = abs(pe["strike"] - ce["strike"])
            ctype = "long_straddle" if gap == 0 else "long_strangle"
            if gap <= max_gap and ctype in enabled:
                c = _vol_combo(ce, pe, ctype)
                if c:
                    combos.append(c)
    seen, out = set(), []
    for c in sorted(combos, key=lambda x: (_f(x.get("confidence")), _f(x.get("max_profit") or 0)), reverse=True):
        if c["combo_id"] in seen:
            continue
        seen.add(c["combo_id"])
        out.append(c)
        if len(out) >= max_candidates:
            break
    return out



def _combo_gate(cfg: Dict[str, Any], combo: Dict[str, Any]) -> Tuple[bool, str]:
    if risk_manager is None:
        return True, "OK"
    for leg in combo.get("legs") or []:
        allowed, reason = risk_manager.can_place_order(
            cfg=cfg,
            symbol=str(leg.get("symbol")),
            option_type=str(leg.get("option_type") or ""),
            signal_type=str(leg.get("action") or "BUY"),
            grade=str(combo.get("grade") or "B"),
            score=_f(combo.get("setup_score"), 0.0),
        )
        if not allowed:
            return False, f"{leg.get('symbol')}: {reason}"
    return True, "OK"


def execute_strategy_combo(cfg: Dict[str, Any], combo: Dict[str, Any], *, trading_adapter: Any, trade_db: Any, quantity_multiplier: int = 1, dry_run: bool = False) -> Dict[str, Any]:
    scfg = _cfg(cfg)
    if not scfg.get("execution_enabled", True):
        return {"status": "blocked", "message": "Strategy-combo execution disabled", "combo_id": combo.get("combo_id")}
    ok, reason = _combo_gate(cfg, combo)
    if not ok:
        return {"status": "blocked", "message": reason, "combo_id": combo.get("combo_id")}

    mult = max(1, int(quantity_multiplier or 1))
    product = str(cfg.get("trading", {}).get("options", {}).get("product", "NRML"))
    price_type = str(cfg.get("trading", {}).get("options", {}).get("price_type", "MARKET"))
    strategy = f"{cfg.get('trading', {}).get('strategy_name', 'UTBot_Options')}_Combo"
    executed = []
    for leg in combo.get("legs") or []:
        qty = max(1, _i(leg.get("quantity"), combo.get("lot_size", 65))) * mult
        req = {"symbol": leg.get("symbol"), "exchange": leg.get("exchange", "NFO"), "action": str(leg.get("action", "BUY")).upper(), "quantity": qty, "product": product, "price_type": price_type, "price": _f(leg.get("price")), "strategy": strategy}
        order_res = {"status": "dry_run", "order_id": f"DRY_{combo.get('combo_id')}_{leg.get('leg_role')}"} if dry_run else trading_adapter.place_order(cfg, req)
        entry = _f(leg.get("price"))
        if not dry_run:
            try:
                ltp = trading_adapter.get_ltp(cfg, req["symbol"], req["exchange"])
                if ltp > 0:
                    entry = ltp
            except Exception:
                pass
        trade_id = None
        if not dry_run:
            trade_id = trade_db.add_trade({
                "order_id": order_res.get("order_id") or f"COMBO_{combo.get('combo_id')}_{leg.get('leg_role')}_{int(datetime.now().timestamp()*1000)}",
                "symbol": req["symbol"], "exchange": req["exchange"], "action": req["action"], "quantity": qty,
                "entry_price": entry, "product": product, "combo_id": combo.get("combo_id"), "combo_name": combo.get("combo_name"),
                "combo_type": combo.get("combo_type"), "combo_leg_role": leg.get("leg_role"), "combo_net_premium": combo.get("net_premium"),
            })
        executed.append({"leg": leg, "order_request": req, "order_response": order_res, "trade_id": trade_id})
    return {"status": "dry_run" if dry_run else "success", "combo_id": combo.get("combo_id"), "combo_name": combo.get("combo_name"), "legs_executed": len(executed), "executions": executed}
