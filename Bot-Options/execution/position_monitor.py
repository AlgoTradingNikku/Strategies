"""
===============================================================================
  Bot-Options / execution / position_monitor.py
  Option Position Monitor — background thread that polls live option premiums,
  manages stops/targets, trailing SLs, partial lot exits, profit locks,
  and expiry square-offs.
===============================================================================
"""

import time
import logging
import threading
from datetime import datetime, date
from typing import dict, Any, Optional

log = logging.getLogger(__name__)

# Import db operations
try:
    from db.option_trade_db import (
        get_open_positions,
        update_position_db,
        log_event
    )
    from execution.order_engine import place_direct_options_order
    from notifications.notifier import (
        notify_execution,
        notify_exit,
        notify_partial_exit,
        notify_profit_lock
    )
except ImportError as e:
    log.error("Import error in position_monitor: %s", e)

class OptionPositionMonitor:
    """
    Manages active options positions. Polling based LTP updates to monitor stops,
    trailing logic, profit locks, and automatic expiry exits.
    """
    def __init__(self):
        self.active_positions: dict[int, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.config: dict = {}
        self.oa_client = None

    def start(self, config: dict, oa_client):
        """Start the background monitoring thread."""
        self.config = config
        self.oa_client = oa_client
        self.running = True
        
        # Load any existing open positions from database into cache
        try:
            db_positions = get_open_positions()
            with self.lock:
                for pos in db_positions:
                    self.active_positions[pos["id"]] = pos
                log.info("Position Monitor loaded %d active positions from database.", len(self.active_positions))
        except Exception as e:
            log.error("Failed to load open positions during monitor start: %s", e)

        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread.start()
        log.info("Option Position Monitor background thread started.")

    def stop(self):
        """Stop the background monitoring thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
            log.info("Option Position Monitor background thread stopped.")

    def register_new_position(self, pos: dict[str, Any]):
        """Add a newly created position to the active tracking dict."""
        with self.lock:
            self.active_positions[pos["id"]] = pos
            log.info("New position registered for tracking: %s (ID %d)", pos["symbol"], pos["id"])
        
        # Send execution alert
        try:
            notify_execution(pos, self.config, self.oa_client)
        except Exception as e:
            log.error("Failed to send execution alert: %s", e)

    def _monitoring_loop(self):
        """Main loop that polls LTPs and processes risk triggers on each active contract."""
        poll_interval = int(self.config.get("trade_management", {}).get("poll_interval_seconds", 5))
        
        while self.running:
            try:
                # 1. Fetch active positions list copy
                with self.lock:
                    positions_to_process = list(self.active_positions.values())

                if positions_to_process:
                    # 2. Bulk fetch LTP quotes from OpenAlgo to save requests
                    symbols_query = [
                        {"symbol": pos["symbol"], "exchange": pos["exchange"]} 
                        for pos in positions_to_process
                    ]
                    
                    quotes_resp = self.oa_client.multiquotes(symbols=symbols_query)
                    
                    ltp_map = {}
                    if isinstance(quotes_resp, dict) and quotes_resp.get("status") == "success":
                        results = quotes_resp.get("results", [])
                        for res in results:
                            sym = res.get("symbol")
                            ltp = res.get("data", {}).get("ltp")
                            if ltp is not None:
                                ltp_map[sym] = float(ltp)
                                
                    # 3. Process each position with the latest LTP
                    for pos in positions_to_process:
                        sym = pos["symbol"]
                        ltp = ltp_map.get(sym)
                        
                        if ltp is not None:
                            self._process_position_tick(pos, ltp)
                        else:
                            # Try single quotes fetch fallback
                            try:
                                resp = self.oa_client.quotes(symbol=sym, exchange=pos["exchange"])
                                ltp = float(resp.get("data", {}).get("ltp") or resp.get("ltp"))
                                self._process_position_tick(pos, ltp)
                            except Exception as e:
                                log.debug("Failed to fetch LTP fallback for %s: %s", sym, e)
                                
            except Exception as e:
                log.error("Error in position monitor cycle: %s", e)
                
            time.sleep(poll_interval)

    def _process_position_tick(self, pos: dict[str, Any], ltp: float):
        """Evaluate targets, trailing, locked, and expiry exits for a position."""
        pos_id = pos["id"]
        entry_premium = float(pos["entry_premium"])
        current_sl = float(pos["current_sl_premium"])
        target_premium = float(pos["target_premium"])
        peak_premium = float(pos.get("peak_premium", entry_premium))
        
        # Update peak premium if price hits new high
        if ltp > peak_premium:
            peak_premium = ltp
            update_position_db(pos_id, peak_premium=peak_premium)
            pos["peak_premium"] = peak_premium

        # Calculate metrics
        pnl_pct = ((ltp - entry_premium) / entry_premium) * 100.0
        
        # 1. EXPIRY DAY AUTO EXIT CHECK
        tm_cfg = self.config.get("trade_management", {})
        exp_cfg = tm_cfg.get("expiry_management", {})
        if exp_cfg.get("auto_exit_on_expiry", True) and pos.get("expiry_exit_triggered", 0) == 0:
            try:
                expiry_dt = datetime.strptime(pos["expiry"], "%d%b%y").date()
            except Exception:
                try:
                    expiry_dt = datetime.strptime(pos["expiry"], "%Y-%m-%d").date()
                except Exception:
                    expiry_dt = None
                    
            if expiry_dt and expiry_dt == date.today():
                now_time = datetime.now().time()
                # Expiry check time (e.g. 15:20 IST)
                exit_minutes = int(exp_cfg.get("exit_minutes_before_close", 10))
                # Market closes at 15:30
                cutoff_hour = 15
                cutoff_minute = 30 - exit_minutes
                
                if now_time.hour > cutoff_hour or (now_time.hour == cutoff_hour and now_time.minute >= cutoff_minute):
                    log.info("[%s] Expiry day cutoff reached. Auto squaring off.", pos["symbol"])
                    self._execute_exit(pos, ltp, "EXPIRY")
                    return

        # 2. HARD STOP LOSS CHECK
        if ltp <= current_sl:
            log.info("[%s] Stop loss hit: LTP %.2f <= SL %.2f", pos["symbol"], ltp, current_sl)
            self._execute_exit(pos, ltp, "SL")
            return

        # 3. HARD TARGET CHECK
        if ltp >= target_premium:
            log.info("[%s] Hard target hit: LTP %.2f >= Target %.2f", pos["symbol"], ltp, target_premium)
            self._execute_exit(pos, ltp, "TARGET")
            return

        # 4. PARTIAL EXIT CHECK
        pe_cfg = tm_cfg.get("partial_exit", {})
        if pe_cfg.get("enabled", True) and pos.get("partial_exit_done", 0) == 0:
            target1_pct = float(pe_cfg.get("target1_pct", 30.0))
            if pnl_pct >= target1_pct:
                self._execute_partial_exit(pos, ltp, pe_cfg)

        # 5. MULTI-LEVEL PROFIT LOCK
        pl_cfg = tm_cfg.get("profit_lock", {})
        if pl_cfg.get("enabled", True):
            levels = pl_cfg.get("levels", [])
            # Sort levels ascending by threshold
            levels = sorted(levels, key=lambda x: x.get("threshold_pct", 0.0))
            
            current_lock_level = int(pos.get("profit_locked", 0))
            new_lock_level = current_lock_level
            
            for idx, lvl in enumerate(levels):
                thresh = float(lvl.get("threshold_pct", 0.0))
                fraction = float(lvl.get("lock_fraction", 0.5))
                
                # If we cross threshold and haven't locked this level yet
                if pnl_pct >= thresh and (idx + 1) > current_lock_level:
                    peak_gain = peak_premium - entry_premium
                    locked_gain = peak_gain * fraction
                    proposed_sl = entry_premium + locked_gain
                    
                    if proposed_sl > current_sl:
                        current_sl = proposed_sl
                        new_lock_level = idx + 1
                        log.info("[%s] Profit Lock Level %d triggered: SL locked at %.2f (LTP: %.2f)", 
                                 pos["symbol"], new_lock_level, current_sl, ltp)

            if new_lock_level > current_lock_level:
                update_position_db(pos_id, current_sl_premium=current_sl, profit_locked=new_lock_level)
                pos["current_sl_premium"] = current_sl
                pos["profit_locked"] = new_lock_level
                log_event(pos_id, "PROFIT_LOCK", current_sl, current_sl, f"Lock Level {new_lock_level} activated")
                try:
                    notify_profit_lock(pos, levels[new_lock_level-1]["threshold_pct"], current_sl, self.config, self.oa_client)
                except Exception as e:
                    log.error("Failed to send profit lock notification: %s", e)

        # 6. TRAILING SL CHECK (runs if profit lock didn't exit, or as supplement)
        trail_cfg = tm_cfg.get("trailing_sl", {})
        if trail_cfg.get("enabled", True):
            activation_pct = float(trail_cfg.get("activation_pct", 25.0))
            if pnl_pct >= activation_pct:
                dist_pct = float(trail_cfg.get("distance_pct", 15.0))
                proposed_sl = peak_premium * (1 - dist_pct / 100.0)
                
                if proposed_sl > current_sl:
                    log.info("[%s] Trailing SL updated: SL moved from %.2f to %.2f (Peak: %.2f)", 
                             pos["symbol"], current_sl, proposed_sl, peak_premium)
                    update_position_db(pos_id, current_sl_premium=proposed_sl, trailing_active=1)
                    pos["current_sl_premium"] = proposed_sl
                    pos["trailing_active"] = 1
                    log_event(pos_id, "TRAILING_SL", current_sl, proposed_sl, "Trailing SL adjusted")

    def _execute_exit(self, pos: dict[str, Any], exit_premium: float, reason: str):
        """Submit the exit square-off order to OpenAlgo and close position in database."""
        pos_id = pos["id"]
        qty = int(pos["quantity"])
        
        log.info("[%s] Executing square-off exit. Reason: %s | Qty: %d", pos["symbol"], reason, qty)
        
        # Place market SELL order (since we bought/longed)
        resp = place_direct_options_order(
            config=self.config,
            symbol=pos["symbol"],
            action="SELL",
            quantity=qty,
            price=0.0,  # Market order
            oa_client=self.oa_client
        )
        
        if resp.get("status") == "success":
            # Position exited successfully
            close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry_premium = float(pos["entry_premium"])
            
            pnl_premium = exit_premium - entry_premium
            pnl_pct = (pnl_premium / entry_premium) * 100.0
            pnl_amount = pnl_premium * qty
            
            update_position_db(
                pos_id,
                status="CLOSED",
                close_reason=reason,
                close_premium=exit_premium,
                close_time=close_time,
                pnl_premium=pnl_premium,
                pnl_pct=pnl_pct,
                pnl_amount=pnl_amount
            )
            
            # Log audit event
            log_event(pos_id, "CLOSE", entry_premium, exit_premium, f"Closed via {reason}")
            
            # Remove from local active tracking dict
            with self.lock:
                if pos_id in self.active_positions:
                    del self.active_positions[pos_id]
            
            # Update cache copy for notification details
            closed_pos = pos.copy()
            closed_pos.update({
                "close_premium": exit_premium,
                "close_reason": reason,
                "close_time": close_time,
                "pnl_amount": pnl_amount,
                "pnl_pct": pnl_pct
            })
            
            try:
                notify_exit(closed_pos, self.config, self.oa_client)
            except Exception as e:
                log.error("Failed to send exit notification: %s", e)
        else:
            log.error("[%s] Square-off exit order failed: %s", pos["symbol"], resp.get("message"))
            log_event(pos_id, "EXIT_FAILED", exit_premium, exit_premium, f"Exit order failed: {resp.get('message')}")

    def _execute_partial_exit(self, pos: dict[str, Any], exit_premium: float, pe_cfg: dict):
        """Exit a fraction of the option lots and move SL to break-even."""
        pos_id = pos["id"]
        qty = int(pos["quantity"])
        lot_size = int(pos.get("lot_size", 75))
        
        fraction = float(pe_cfg.get("exit_qty_fraction", 0.5))
        exit_qty = int(qty * fraction)
        
        # Round exit quantity to nearest lot size
        exit_qty = max(1, round(exit_qty / lot_size)) * lot_size
        
        if exit_qty >= qty:
            log.warning("[%s] Partial exit qty %d >= remaining qty %d. Skipping partial exit.", 
                        pos["symbol"], exit_qty, qty)
            return

        log.info("[%s] Executing partial exit. Qty: %d / %d", pos["symbol"], exit_qty, qty)
        
        resp = place_direct_options_order(
            config=self.config,
            symbol=pos["symbol"],
            action="SELL",
            quantity=exit_qty,
            price=0.0,
            oa_client=self.oa_client
        )
        
        if resp.get("status") == "success":
            remaining_qty = qty - exit_qty
            entry_premium = float(pos["entry_premium"])
            
            # Update DB parameters
            fields = {
                "quantity": remaining_qty,
                "partial_exit_done": 1
            }
            
            if pe_cfg.get("move_sl_to_breakeven", True):
                fields["current_sl_premium"] = entry_premium
                pos["current_sl_premium"] = entry_premium
                log_event(pos_id, "SL_UPDATE", None, entry_premium, "SL moved to break-even on partial exit")
                
            update_position_db(pos_id, **fields)
            
            pos["quantity"] = remaining_qty
            pos["partial_exit_done"] = 1
            
            log_event(pos_id, "PARTIAL_EXIT", qty, remaining_qty, f"Exited {exit_qty} @ {exit_premium}")
            
            try:
                notify_partial_exit(pos, exit_qty, exit_premium, self.config, self.oa_client)
            except Exception as e:
                log.error("Failed to send partial exit notification: %s", e)
        else:
            log.error("[%s] Partial exit order failed: %s", pos["symbol"], resp.get("message"))
            log_event(pos_id, "PARTIAL_EXIT_FAILED", exit_premium, exit_premium, f"Partial exit failed: {resp.get('message')}")
