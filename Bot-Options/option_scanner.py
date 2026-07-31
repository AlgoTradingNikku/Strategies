"""
===============================================================================
  Bot-Options / option_scanner.py
  Option Scanner Orchestrator — links underlying index evaluation (Stage 1),
  strike selection and option filters (Stage 2), and premium chart confirmation
  (Stage 3). Coordinates risk checks, DB persistence, and execution routing.
===============================================================================
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, date
from typing import list, dict, Any, tuple

log = logging.getLogger(__name__)

# Add Bot-Stocks path
bot_stocks_dir = Path(__file__).resolve().parent / "Bot-Stocks"
if str(bot_stocks_dir) not in sys.path:
    sys.path.insert(0, str(bot_stocks_dir))

# Imports
from core.expiry_manager import select_expiry, days_to_expiry
from data.option_chain import fetch_option_chain
from core.strike_selector import select_strike
from core.option_filters import (
    calculate_iv_score,
    calculate_oi_momentum_score,
    calculate_time_decay_penalty
)
from core.option_signals import evaluate_underlying_signals, evaluate_option_chart_confirmation
from core.option_risk import check_risk_circuit_breakers, validate_capital_allocation
from db.option_signal_db import save_option_signal, update_signal_status
from db.option_trade_db import get_open_positions, get_closed_positions, open_position_db
from execution.order_engine import place_direct_options_order
from notifications.notifier import notify_new_signal

def _is_market_hours(config: dict) -> bool:
    """Helper to check if currently within configured trading hours."""
    bot_cfg = config.get("bot", {})
    if not bot_cfg.get("market_hours_check", True):
        return True
        
    now = datetime.now()
    # Market days are Monday to Friday (0 to 4)
    if now.weekday() > 4:
        return False
        
    open_str = bot_cfg.get("market_open", "09:15")
    close_str = bot_cfg.get("market_close", "15:30")
    
    try:
        t_now = now.time()
        t_open = datetime.strptime(open_str, "%H:%M").time()
        t_close = datetime.strptime(close_str, "%H:%M").time()
        return t_open <= t_now <= t_close
    except Exception as e:
        log.error("Error parsing market hours: %s", e)
        return True


def get_daily_metrics() -> tuple[int, float, int]:
    """
    Calculate daily statistics for risk checks:
    - Number of trades today
    - Daily P&L (₹)
    - Consecutive losses count
    """
    closed_today = 0
    daily_pnl = 0.0
    consec_losses = 0
    
    today_str = date.today().strftime("%Y-%m-%d")
    
    # 1. Fetch closed positions
    closed_positions = get_closed_positions(limit=100)
    for pos in closed_positions:
        close_time = pos.get("close_time", "")
        if close_time.startswith(today_str):
            closed_today += 1
            daily_pnl += float(pos.get("pnl_amount", 0.0))
            
    # 2. Count consecutive losses (ordered newest first)
    for pos in closed_positions:
        pnl = float(pos.get("pnl_amount", 0.0))
        if pnl < 0:
            consec_losses += 1
        else:
            break
            
    # Total trades today = open positions count + closed positions count today
    open_positions = get_open_positions()
    trades_today = len(open_positions) + closed_today
    
    return trades_today, daily_pnl, consec_losses


def execute_options_trade(
    sig: dict[str, Any],
    config: dict,
    oa_client,
    monitor
) -> bool:
    """Submit entry order to broker, create position tracker, and monitor trade."""
    exec_cfg = config.get("execution", {})
    quantity = int(exec_cfg.get("num_lots", 1)) * int(sig.get("lot_size", 75))
    
    # Place entry BUY limit/market order
    resp = place_direct_options_order(
        config=config,
        symbol=sig["symbol"],
        action="BUY",
        quantity=quantity,
        price=float(sig["entry_premium"]) if exec_cfg.get("order_type") == "LIMIT" else 0.0,
        oa_client=oa_client
    )
    
    if resp.get("status") == "success":
        order_id = resp.get("orderid")
        entry_premium = float(sig["entry_premium"])
        
        # Calculate SL & Target prices
        tm_cfg = config.get("trade_management", {})
        sl_pct = float(tm_cfg.get("stop_loss_pct", 30.0))
        target_pct = float(tm_cfg.get("target_pct", 50.0))
        
        sl_premium = entry_premium * (1.0 - sl_pct / 100.0)
        target_premium = entry_premium * (1.0 + target_pct / 100.0)
        
        # Format database record
        pos_rec = {
            "order_id": order_id,
            "underlying": sig["underlying"],
            "symbol": sig["symbol"],
            "exchange": sig["exchange"],
            "expiry": sig["expiry"],
            "strike": sig["strike"],
            "option_type": sig["option_type"],
            "direction": "BUY",
            "lot_size": sig["lot_size"],
            "num_lots": int(exec_cfg.get("num_lots", 1)),
            "quantity": quantity,
            "entry_premium": entry_premium,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "underlying_price_at_entry": sig["underlying_price"],
            "current_sl_premium": sl_premium,
            "target_premium": target_premium,
            "timeframe": sig["timeframe"]
        }
        
        pos_id = open_position_db(pos_rec)
        if pos_id > 0:
            pos_rec["id"] = pos_id
            # Register with live monitor thread
            monitor.register_new_position(pos_rec)
            # Update signal table
            update_signal_status(sig["id"], "EXECUTED")
            return True
            
    return False


def run_option_scan(
    config: dict,
    oa_client,
    monitor
) -> tuple[list[dict], list[dict], int]:
    """
    Orchestrate full options scanner pipeline:
    Stage 1: Scan Index
    Stage 2: Select strike contract + filter checks
    Stage 3: Confirm signal using option contract premium chart
    """
    buy_signals = []
    sell_signals = []
    total_scanned = 0
    
    # 1. Market Hours Check
    if not _is_market_hours(config):
        log.info("Outside market hours. Options scan skipped.")
        return [], [], 0

    underlyings_cfg = config.get("underlyings", [])
    timeframe = config.get("scan_timeframe", "5m")
    
    # For each enabled index constituent
    for und in underlyings_cfg:
        if not und.get("enabled", True):
            continue
            
        underlying_name = und["name"]
        lot_size = int(und.get("lot_size", 75))
        strike_step = float(und.get("strike_step", 50.0))
        total_scanned += 1
        
        log.info("[%s] Starting scan cycle...", underlying_name)
        
        # STAGE 1: Scan underlying index
        index_signals = evaluate_underlying_signals(underlying_name, timeframe, config)
        if not index_signals:
            log.info("[%s] No underlying trend signals generated.", underlying_name)
            continue
            
        # For each signal (direction maps to CE/PE option contract)
        for sig in index_signals:
            option_type = sig["option_type"]
            direction = sig["direction"]
            underlying_score = sig["underlying_score"]
            
            # Skip if score is below Gate 1 threshold
            min_gate1 = float(config.get("min_underlying_score", 60))
            if underlying_score < min_gate1:
                log.info("[%s] Signal %s skipped: Score %.1f below Gate 1 (%.1f)", 
                         underlying_name, direction, underlying_score, min_gate1)
                continue
                
            # Expiry selection
            sel_cfg = config.get("strike_selection", {})
            pref = sel_cfg.get("expiry_preference", "WEEKLY")
            roll_days = int(sel_cfg.get("auto_roll_days", 1))
            
            expiry_res = select_expiry(underlying_name, oa_client, pref, roll_days)
            if not expiry_res:
                log.warning("[%s] Expiry selector failed to resolve a date.", underlying_name)
                continue
                
            expiry_date_obj, expiry_str = expiry_res
            days_left = days_to_expiry(expiry_date_obj)
            
            # Fetch option chain
            chain = fetch_option_chain(underlying_name, expiry_str, oa_client)
            if not chain:
                continue
                
            # STAGE 2: Strike Selection
            contract = select_strike(chain, option_type, sel_cfg, strike_step)
            if not contract:
                log.info("[%s] Strike selector could not find contract matching criteria.", underlying_name)
                continue
                
            symbol = contract["symbol"]
            entry_premium = float(contract["ltp"])
            iv = float(contract.get("iv", 0))
            oi = int(contract.get("oi", 0))
            
            # Calculate option-specific filter adjustments
            filters_cfg = config.get("filters", {})
            
            iv_adj, iv_reason = calculate_iv_score(iv, filters_cfg)
            oi_adj, oi_reason = calculate_oi_momentum_score(oi, oi, 0.0, filters_cfg) # prev tracking can be implemented later
            decay_adj, decay_reason = calculate_time_decay_penalty(days_left, filters_cfg)
            
            # STAGE 3: Option Premium Chart Scan
            s3_res = evaluate_option_chart_confirmation(symbol, timeframe, config, oa_client)
            s3_adj = s3_res.get("score_adjustment", 0.0)
            
            # Combined score calculation
            final_score = underlying_score + iv_adj + oi_adj + decay_adj + s3_adj
            final_score = max(0.0, min(100.0, final_score))
            
            score_reasons = sig.get("reasons", [])
            if iv_reason: score_reasons.append(iv_reason)
            if oi_reason: score_reasons.append(oi_reason)
            if decay_reason: score_reasons.append(decay_reason)
            score_reasons.extend(s3_res.get("reasons", []))
            
            filter_status = {
                "iv": "pass" if iv_adj >= 0 else "warn",
                "decay": "pass" if decay_adj >= 0 else "warn",
                "stage3": s3_res.get("status")
            }
            
            # Create consolidated signal record
            sig_rec = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "underlying": underlying_name,
                "symbol": symbol,
                "exchange": "NFO",
                "expiry": expiry_str,
                "strike": float(contract.get("strike", 0.0)),
                "option_type": option_type,
                "direction": direction,
                "strategy_name": sig.get("triggered_engines", ["UTBot+SR"]),
                "entry_premium": entry_premium,
                "confidence_score": final_score,
                "score_reasons": score_reasons,
                "filter_status": filter_status,
                "iv_proxy": iv,
                "oi_at_signal": oi,
                "underlying_price": float(sig["underlying_close"]),
                "timeframe": timeframe,
                "lot_size": lot_size,
                "status": "SIGNAL"
            }
            
            # Clean strategy name for DB compatibility
            if isinstance(sig_rec["strategy_name"], list):
                sig_rec["strategy_name"] = "+".join(sig_rec["strategy_name"])
                
            # Gate 2: Final combined score filter
            min_score = float(filters_cfg.get("min_alert_score", 60))
            if final_score < min_score:
                log.info("[%s] Option Signal %s rejected: Final score %.1f below Min Alert Score (%.1f)", 
                         symbol, direction, final_score, min_score)
                continue
                
            # Save signal record
            sig_id = save_option_signal(sig_rec)
            if sig_id > 0:
                sig_rec["id"] = sig_id
            
            log.info("🎯 Options signal triggered: %s @ ₹%.2f | Score: %.1f", symbol, entry_premium, final_score)
            
            # Send Notification Alert
            try:
                notify_new_signal(sig_rec, config, oa_client)
            except Exception as e:
                log.error("Failed to notify options signal: %s", e)

            # Add to return list
            if direction == "BUY":
                buy_signals.append(sig_rec)
            else:
                sell_signals.append(sig_rec)
                
            # 4. RISK CIRCUIT BREAKERS AND EXECUTION
            active_positions = get_open_positions()
            trades_today, daily_pnl, consec_losses = get_daily_metrics()
            
            is_risk_ok, risk_reason = check_risk_circuit_breakers(
                config=config,
                active_positions_count=len(active_positions),
                trades_today=trades_today,
                daily_pnl=daily_pnl,
                consecutive_losses=consec_losses
            )
            
            if not is_risk_ok:
                log.warning("Order execution blocked by Risk Circuit Breakers: %s", risk_reason)
                continue
                
            # Capital validation
            quantity = int(config.get("execution", {}).get("num_lots", 1)) * lot_size
            est_cost = entry_premium * quantity
            current_deployed = sum(float(p["entry_premium"]) * int(p["quantity"]) for p in active_positions)
            
            is_cap_ok, cap_reason = validate_capital_allocation(config, est_cost, current_deployed)
            if not is_cap_ok:
                log.warning("Order execution blocked by Capital Allocation: %s", cap_reason)
                continue
                
            # EXECUTION (If auto mode is enabled)
            if config.get("execution", {}).get("order_mode") == "auto":
                try:
                    success = execute_options_trade(sig_rec, config, oa_client, monitor)
                    if success:
                        log.info("[%s] Auto order executed successfully.", symbol)
                except Exception as e:
                    log.error("Failed executing auto options order for %s: %s", symbol, e)
                    
    return buy_signals, sell_signals, total_scanned
