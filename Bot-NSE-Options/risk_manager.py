"""
===============================================================================
  risk_manager.py — Centralized Risk & Guardrails Engine (Sprint 1)
===============================================================================
Provides pre-trade gates and continuous risk monitoring for Bot-NSE-Options.

Guardrails implemented:
  1. Kill Switch          — global manual OFF blocking all new orders
  2. Duplicate-Entry Guard — block same-symbol re-entry while OPEN + cool-down
  3. Directional Gate     — CE only when spot UP-trending, PE only when DOWN
  4. Min-Grade Gate       — reject signals below configured grade / score
  5. Market Hours         — enforce trading window + entry cutoff time
  6. Daily Loss Limit     — auto square-off + halt when day P&L breaches limit

All checks are configurable via config.yml (`risk:` / `trading:` / `bot:` sections)
and via the dashboard Quick Filter toggles + Settings tab.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = None

log = logging.getLogger("UTBotSRChannelsScanner")

_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}

_state_lock = threading.Lock()
_last_exit_times: Dict[str, datetime] = {}  # symbol -> last exit datetime (IST)


def _now_ist() -> datetime:
    return datetime.now(_IST) if _IST else datetime.now()


def _parse_hhmm(s: str, default: Tuple[int, int]) -> Tuple[int, int]:
    """Parse 'HH:MM' or 'HHMM'; fall back to default on error."""
    try:
        s = str(s).strip()
        if ":" in s:
            hh, mm = s.split(":", 1)
        else:
            if len(s) == 3:
                hh, mm = s[0], s[1:]
            else:
                hh, mm = s[:2], s[2:]
        return int(hh), int(mm)
    except Exception:
        return default


def record_exit(symbol: str, when: Optional[datetime] = None) -> None:
    """Record the time a position was exited, for cool-down enforcement."""
    with _state_lock:
        _last_exit_times[symbol] = when or _now_ist()


def get_last_exit_time(symbol: str) -> Optional[datetime]:
    with _state_lock:
        return _last_exit_times.get(symbol)


def reset_state() -> None:
    with _state_lock:
        _last_exit_times.clear()


# ============================================================================
# 1. Kill Switch
# ============================================================================



# ============================================================================
# 2. Duplicate-Entry Guard
# ============================================================================

def check_duplicate_entry(cfg: dict, symbol: str) -> Tuple[bool, str]:
    """
    Returns (allowed, reason).
    Blocks if:
      - Same symbol already has an OPEN trade
      - OR symbol was exited within cool-down window
    """
    ded = cfg.get("trading", {}).get("dedup", {})
    if not ded.get("enabled", True):
        return True, ""

    try:
        import trade_db
        open_trades = trade_db.get_active_trades()
        for t in open_trades:
            if t.get("symbol") == symbol:
                return False, f"DUP_OPEN_POSITION({symbol})"
    except Exception as exc:
        log.debug("[risk] dup check trade_db error: %s", exc)

    cooldown = int(ded.get("cooldown_minutes", 5))
    last_exit = get_last_exit_time(symbol)
    if last_exit and cooldown > 0:
        elapsed = (_now_ist() - last_exit).total_seconds() / 60.0
        if elapsed < cooldown:
            return False, f"COOLDOWN({int(cooldown - elapsed)}m_left)"

    return True, ""


# ============================================================================
# 3. Directional Gate
# ============================================================================

def check_directional_gate(cfg: dict, option_type: str, signal_type: str) -> Tuple[bool, str]:
    """
    Ensure CE trades align with UP-trending spot and PE with DOWN-trending spot.
    Uses UT-Bot on the underlying spot's LTF candles to detect trend.
    Only enforced for BUY-side option trades (buying premium).
    """
    dg = cfg.get("trading", {}).get("directional_gate", {})
    if not dg.get("enabled", True):
        return True, ""

    if signal_type.upper() != "BUY":
        return True, ""

    option_type = (option_type or "").upper()
    if option_type not in ("CE", "PE"):
        return True, ""

    try:
        from scanner import fetch_history
        from signals import compute_utbot_signals

        opt_cfg = cfg.get("options", {})
        underlying = opt_cfg.get("underlying", "NIFTY")
        index_exchange = opt_cfg.get("index_exchange", "NSE_INDEX")
        timeframe = opt_cfg.get("timeframe", "5m")

        df = fetch_history(underlying, timeframe, cfg, exchange=index_exchange)
        if df is None or df.empty or len(df) < 5:
            return True, "SPOT_UNAVAILABLE_ALLOW"

        ut_cfg = cfg.get("strategy", {})
        df = compute_utbot_signals(
            df,
            key_value=float(ut_cfg.get("key_value", 2.0)),
            atr_period=int(ut_cfg.get("atr_period", 1)),
            use_heikin_ashi=bool(ut_cfg.get("use_heikin_ashi", False)),
        )
        spot_pos = int(df["ut_pos"].iloc[-1])
    except Exception as exc:
        log.debug("[risk] directional gate error: %s", exc)
        return True, "SPOT_ERROR_ALLOW"

    if option_type == "CE" and spot_pos < 0:
        return False, "SPOT_DOWNTREND_BLOCKS_CE"
    if option_type == "PE" and spot_pos > 0:
        return False, "SPOT_UPTREND_BLOCKS_PE"

    return True, ""


# ============================================================================
# 4. Min-Grade / Min-Score Gate
# ============================================================================

def check_min_grade(cfg: dict, grade: str, score: float) -> Tuple[bool, str]:
    tcfg = cfg.get("trading", {})
    min_grade = str(tcfg.get("min_grade", "B")).upper().strip()
    min_score = float(tcfg.get("min_score", 60))

    g_val = _GRADE_ORDER.get(str(grade).upper(), 0)
    min_val = _GRADE_ORDER.get(min_grade, 3)

    if g_val < min_val:
        return False, f"GRADE_{grade}_BELOW_{min_grade}"
    if float(score) < min_score:
        return False, f"SCORE_{score:.1f}_BELOW_{min_score:.1f}"
    return True, ""

def is_kill_switch_on(cfg: dict) -> bool:
    return bool(cfg.get("risk", {}).get("kill_switch", False))


# ============================================================================
# 5. Market Hours Enforcement
# ============================================================================

def check_market_hours(cfg: dict) -> Tuple[bool, str]:
    bot_cfg = cfg.get("bot", {})
    if not bot_cfg.get("market_hours_check", True):
        return True, ""

    open_h, open_m = _parse_hhmm(bot_cfg.get("market_open", "09:15"), (9, 15))
    close_h, close_m = _parse_hhmm(bot_cfg.get("market_close", "15:30"), (15, 30))
    cutoff_h, cutoff_m = _parse_hhmm(bot_cfg.get("entry_cutoff_time", "14:45"), (14, 45))

    now = _now_ist()
    now_min = now.hour * 60 + now.minute
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    cutoff_min = cutoff_h * 60 + cutoff_m

    if now.weekday() >= 5:
        return False, "WEEKEND"
    if now_min < open_min:
        return False, "PRE_MARKET"
    if now_min >= close_min:
        return False, "POST_MARKET"
    if now_min >= cutoff_min:
        return False, f"AFTER_ENTRY_CUTOFF({bot_cfg.get('entry_cutoff_time','14:45')})"

    return True, ""


# ============================================================================
# 6. Daily Loss Limit
# ============================================================================

def compute_day_pnl(cfg: dict) -> Dict[str, float]:
    """
    Returns dict with realized/unrealized/total P&L for today (IST) and % of equity.
    """
    result = {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0, "pct_of_equity": 0.0}
    try:
        import trade_db
        conn = trade_db.get_connection()
        cur = conn.cursor()

        today_ist = _now_ist().date().isoformat()

        cur.execute(
            "SELECT COALESCE(SUM(pnl_amount), 0.0) AS s FROM trades "
            "WHERE status = 'CLOSED' AND substr(COALESCE(closed_at, ''), 1, 10) = ?",
            (today_ist,),
        )
        row = cur.fetchone()
        result["realized_pnl"] = float(row["s"] if row and row["s"] is not None else 0.0)

        cur.execute(
            "SELECT entry_price, current_price, quantity, action FROM trades WHERE status = 'OPEN'"
        )
        unreal = 0.0
        for r in cur.fetchall():
            entry = float(r["entry_price"] or 0.0)
            cur_p = float(r["current_price"] or entry)
            qty = int(r["quantity"] or 0)
            if str(r["action"]).upper() == "BUY":
                unreal += (cur_p - entry) * qty
            else:
                unreal += (entry - cur_p) * qty
        result["unrealized_pnl"] = unreal
        conn.close()
    except Exception as exc:
        log.debug("[risk] compute_day_pnl error: %s", exc)

    result["total_pnl"] = result["realized_pnl"] + result["unrealized_pnl"]
    equity = float(cfg.get("risk", {}).get("account_equity", 100000) or 1.0)
    if equity > 0:
        result["pct_of_equity"] = (result["total_pnl"] / equity) * 100.0
    return result


def check_daily_loss_limit(cfg: dict) -> Tuple[bool, str, Dict[str, float]]:
    dll = cfg.get("risk", {}).get("daily_loss_limit", {})
    pnl = compute_day_pnl(cfg)
    if not dll.get("enabled", True):
        return True, "", pnl

    max_loss_pct = float(dll.get("max_loss_pct", 3.0))
    if pnl["pct_of_equity"] <= -abs(max_loss_pct):
        return False, f"DAILY_LOSS_BREACH({pnl['pct_of_equity']:.2f}%<=-{max_loss_pct:.2f}%)", pnl
    return True, "", pnl



# ============================================================================
# Master gate — call before every order placement
# ============================================================================

def can_place_order(
    cfg: dict,
    symbol: str,
    option_type: str,
    signal_type: str,
    grade: str,
    score: float,
) -> Tuple[bool, str]:
    """
    Master pre-trade gate. Returns (allowed, reason).
    Runs all Sprint-1 guardrails in order; short-circuits on first block.
    """
    if is_kill_switch_on(cfg):
        return False, "KILL_SWITCH_ON"

    ok, reason = check_market_hours(cfg)
    if not ok:
        return False, reason

    ok, reason, _pnl = check_daily_loss_limit(cfg)
    if not ok:
        return False, reason

    # Sprint-2: consecutive-loss circuit breaker
    try:
        import circuit_breaker
        tripped, cb_reason, _cb_info = circuit_breaker.is_tripped(cfg)
        if tripped:
            return False, cb_reason
    except Exception as exc:
        log.debug("[risk] circuit_breaker check error: %s", exc)

    # Sprint-3: portfolio-level caps (concurrent positions + exposure)
    try:
        import position_sizer
        ok_conc, r_conc = position_sizer.check_concurrent_positions(cfg)
        if not ok_conc:
            return False, r_conc
        ok_exp, r_exp = position_sizer.check_portfolio_exposure(cfg, extra_premium=0.0)
        if not ok_exp:
            return False, r_exp
    except Exception as exc:
        log.debug("[risk] position_sizer portfolio check error: %s", exc)

    ok, reason = check_min_grade(cfg, grade, score)
    if not ok:
        return False, reason

    ok, reason = check_directional_gate(cfg, option_type, signal_type)
    if not ok:
        return False, reason

    ok, reason = check_duplicate_entry(cfg, symbol)
    if not ok:
        return False, reason

    return True, "OK"


def _get_alpha_status(cfg: dict) -> Dict[str, Any]:
    """[Sprint-4] Snapshot of alpha-enhancer live state for dashboard."""
    ae_cfg = cfg.get("alpha_enhancers", {}) or {}
    out: Dict[str, Any] = {
        "enabled": bool(ae_cfg.get("enabled", True)),
        "regime": "UNKNOWN",
        "vix": 0.0,
        "regime_multiplier": 1.0,
        "session": "prime",
        "session_bonus": 0.0,
        "vix_regime_enabled": bool(ae_cfg.get("vix_regime", {}).get("enabled", True)),
        "session_weighting_enabled": bool(ae_cfg.get("session_weighting", {}).get("enabled", True)),
        "volume_profile_enabled": bool(ae_cfg.get("volume_profile", {}).get("enabled", True)),
        "greeks_enabled": bool(ae_cfg.get("greeks", {}).get("enabled", True)),
        "strict_mtf_enabled": bool(ae_cfg.get("strict_mtf", {}).get("enabled", False)),
    }
    try:
        import alpha_enhancers
        regime, vix = alpha_enhancers.get_vix_regime(cfg)
        out["regime"] = regime
        out["vix"] = round(float(vix), 2)
        out["regime_multiplier"] = alpha_enhancers.get_regime_multiplier(cfg, regime)
        bucket = alpha_enhancers.get_session_bucket(cfg)
        out["session"] = bucket
        out["session_bonus"] = alpha_enhancers.get_session_bonus(cfg, bucket)
    except Exception as exc:
        log.debug("[risk_manager] alpha status snapshot skipped: %s", exc)
    return out


def get_status(cfg: dict) -> Dict[str, Any]:
    """Snapshot for dashboard status strip / /api/risk/status endpoint."""
    pnl = compute_day_pnl(cfg)
    mh_ok, mh_reason = check_market_hours(cfg)
    dll_ok, dll_reason, _ = check_daily_loss_limit(cfg)

    # Sprint-2: circuit-breaker status
    cb_tripped, cb_reason, cb_info = False, "", {"enabled": False, "streak": 0, "max_losses": 3}
    try:
        import circuit_breaker
        cb_tripped, cb_reason, cb_info = circuit_breaker.is_tripped(cfg)
    except Exception as exc:
        log.debug("[risk] circuit_breaker status error: %s", exc)

    # Sprint-3: portfolio-sizing snapshot
    ps_snap = {"open_positions": 0, "total_premium": 0.0, "exposure_pct": 0.0}
    ps_cfg = cfg.get("position_sizing", {})
    try:
        import position_sizer
        ps_snap = position_sizer.get_portfolio_snapshot(cfg)
    except Exception as exc:
        log.debug("[risk] portfolio snapshot error: %s", exc)

    return {
        "kill_switch": is_kill_switch_on(cfg),
        "market_hours_ok": mh_ok,
        "market_hours_reason": mh_reason,
        "daily_loss_ok": dll_ok,
        "daily_loss_reason": dll_reason,
        "day_pnl": pnl,
        "circuit_breaker": {
            "tripped": cb_tripped,
            "reason": cb_reason,
            **cb_info,
        },
        "position_sizing": {
            "enabled": bool(ps_cfg.get("enabled", True)),
            "mode": str(ps_cfg.get("mode", "fixed_fractional")),
            "risk_per_trade_pct": float(ps_cfg.get("risk_per_trade_pct", 1.0)),
            "max_portfolio_exposure_pct": float(ps_cfg.get("max_portfolio_exposure_pct", 15.0)),
            "max_concurrent_positions": int(ps_cfg.get("max_concurrent_positions", 3)),
            **ps_snap,
        },
        "alpha_enhancers": _get_alpha_status(cfg),
        "trading_allowed": (
            not is_kill_switch_on(cfg) and mh_ok and dll_ok and not cb_tripped
        ),
        "account_equity": float(cfg.get("risk", {}).get("account_equity", 100000)),
        "min_grade": cfg.get("trading", {}).get("min_grade", "B"),
        "min_score": cfg.get("trading", {}).get("min_score", 60),
    }

