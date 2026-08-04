import time
import logging
import threading
from datetime import datetime
from openalgo import api as oa_api
import trade_db
from trading_adapter import place_order as adapter_place_order, get_ltp as adapter_get_ltp
from telegram import send_telegram_alert

log = logging.getLogger("UTBotSRChannelsScanner")

class PositionMonitor:
    def __init__(self):
        self.active_positions = {}  # {pos_id: pos_dict}
        self.ws_connected = False
        self.client = None
        self.monitor_thread = None
        self.ws_thread = None
        self.running = False
        self.lock = threading.Lock()
        self.config = {}

    def start(self, config: dict):
        self.config = config
        tm_cfg = config.get("trade_management", {})
        if not tm_cfg.get("enabled", False):
            log.info("Trade Management is disabled in config.yml. Monitoring will not start.")
            return

        self.running = True
        
        # Load any existing open positions from DB
        open_pos = trade_db.get_open_positions()
        with self.lock:
            for pos in open_pos:
                self.active_positions[pos["id"]] = pos
        log.info("Trade Manager initialized. Loaded %d open positions from database.", len(open_pos))

        # Start background monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()

        # Connect to OpenAlgo WebSocket in a separate thread
        self.ws_thread = threading.Thread(target=self._ws_connect_loop, daemon=True)
        self.ws_thread.start()

    def stop(self):
        self.running = False
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        log.info("Trade Manager stopped.")

    def _ws_connect_loop(self):
        oa_cfg = self.config.get("openalgo", {})
        ws_url = oa_cfg.get("ws_url", "ws://127.0.0.1:8765")
        base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
        api_key = oa_cfg.get("apikey", "")

        while self.running:
            if not self.ws_connected:
                try:
                    log.info("Connecting to OpenAlgo WebSocket: %s", ws_url)
                    self.client = oa_api(api_key=api_key, host=base_url, ws_url=ws_url)
                    self.client.connect()
                    
                    # Connection callback doesn't exist directly, but we can verify status
                    self.ws_connected = True
                    log.info("✅ OpenAlgo WebSocket connected successfully.")

                    # Register LTP subscription callback
                    self.client.subscribe_ltp([], on_data_received=self._on_ltp_tick)
                    
                    # Re-subscribe to all active positions
                    with self.lock:
                        instruments = [{"exchange": p["exchange"], "symbol": p["symbol"]} for p in self.active_positions.values()]
                    if instruments:
                        self.client.subscribe_ltp(instruments, on_data_received=self._on_ltp_tick)

                except Exception as e:
                    self.ws_connected = False
                    log.error("WebSocket connection error: %s. Retrying in 10s...", e)
                    time.sleep(10)
            else:
                # Ping check or sleep to maintain thread active
                time.sleep(5)

    def _on_ltp_tick(self, data):
        # Callback format: {"symbol": "INFY", "exchange": "NSE", "ltp": 1423.55}
        if not data or not isinstance(data, dict):
            return
        symbol = data.get("symbol")
        ltp = data.get("ltp")
        if symbol is None or ltp is None:
            return
        
        try:
            ltp = float(ltp)
        except ValueError:
            return

        with self.lock:
            for pos_id, pos in list(self.active_positions.items()):
                if pos["symbol"] == symbol:
                    self._process_price_update(pos, ltp)

    def _monitoring_loop(self):
        tm_cfg = self.config.get("trade_management", {})
        interval = float(tm_cfg.get("poll_interval_seconds", 5))

        while self.running:
            # Fallback polling logic when WS is down
            if not self.ws_connected:
                with self.lock:
                    positions_to_poll = list(self.active_positions.values())
                
                for pos in positions_to_poll:
                    if not self.running:
                        break
                    try:
                        ltp = adapter_get_ltp(self.config, pos["symbol"], pos["exchange"])
                        with self.lock:
                            # Re-verify position is still active
                            if pos["id"] in self.active_positions:
                                self._process_price_update(self.active_positions[pos["id"]], ltp)
                    except Exception as e:
                        log.error("Fallback LTP fetch failed for %s: %s", pos["symbol"], e)
            
            time.sleep(interval)

    def _process_price_update(self, pos, ltp):
        pos_id = pos["id"]
        direction = pos["direction"]
        entry = pos["entry_price"]
        hwm = pos["high_water_mark"]
        current_sl = pos["current_sl"]
        target = pos["target_price"]

        # 1. Update High/Low Water Mark
        if direction == "BUY":
            if ltp > hwm:
                hwm = ltp
                trade_db.update_position(pos_id, high_water_mark=hwm)
                pos["high_water_mark"] = hwm
        else:  # SELL
            # For short positions, HWM acts as "low water mark" (best price is lowest)
            if ltp < hwm:
                hwm = ltp
                trade_db.update_position(pos_id, high_water_mark=hwm)
                pos["high_water_mark"] = hwm

        # 2. Check Exits (SL / Target)
        # Target Exit Check
        is_target_hit = (direction == "BUY" and ltp >= target) or (direction == "SELL" and ltp <= target)
        if is_target_hit:
            self._execute_exit(pos, ltp, "TARGET")
            return

        # Stop Loss Exit Check
        is_sl_hit = (direction == "BUY" and ltp <= current_sl) or (direction == "SELL" and ltp >= current_sl)
        if is_sl_hit:
            self._execute_exit(pos, ltp, "STOP_LOSS")
            return

        # 3. Check Rule Adjustments (Partial Exit, Profit Lock, Trailing SL)
        tm_cfg = self.config.get("trade_management", {})
        
        # Calculate current gain percentage
        gain_pct = ((ltp - entry) / entry * 100) if direction == "BUY" else ((entry - ltp) / entry * 100)

        # A. Partial Exit
        pe_cfg = tm_cfg.get("partial_exit", {})
        if pe_cfg.get("enabled", False) and not pos.get("partial_exit_done", 0):
            pe_target = float(pe_cfg.get("target1_pct", 1.0))
            if gain_pct >= pe_target:
                self._execute_partial_exit(pos, ltp, pe_cfg)

        # B. Profit Lock
        pl_cfg = tm_cfg.get("profit_lock", {})
        if pl_cfg.get("enabled", False) and not pos.get("profit_locked", 0):
            pl_thresh = float(pl_cfg.get("threshold_pct", 1.5))
            if gain_pct >= pl_thresh:
                lock_fraction = float(pl_cfg.get("lock_fraction", 0.5))
                gain_val = (ltp - entry) if direction == "BUY" else (entry - ltp)
                locked_profit = gain_val * lock_fraction
                
                new_sl = entry + locked_profit if direction == "BUY" else entry - locked_profit
                
                # Update SL if it improves protection
                if (direction == "BUY" and new_sl > current_sl) or (direction == "SELL" and new_sl < current_sl):
                    trade_db.update_position(pos_id, current_sl=new_sl, profit_locked=1)
                    trade_db.log_event(pos_id, "PROFIT_LOCKED", current_sl, new_sl, f"Profit locked at threshold {pl_thresh}%")
                    pos["current_sl"] = new_sl
                    pos["profit_locked"] = 1
                    self._alert_sl_update(pos, "Profit Locked")

        # C. Trailing SL
        tsl_cfg = tm_cfg.get("trailing_sl", {})
        if tsl_cfg.get("enabled", False):
            tsl_act = float(tsl_cfg.get("activation_pct", 1.0))
            if gain_pct >= tsl_act:
                dist_pct = float(tsl_cfg.get("distance_pct", 0.5))
                if direction == "BUY":
                    new_sl = hwm * (1.0 - dist_pct / 100.0)
                    if new_sl > current_sl:
                        trade_db.update_position(pos_id, current_sl=new_sl, trailing_active=1)
                        trade_db.log_event(pos_id, "SL_MOVED", current_sl, new_sl, f"Trailing SL adjusted to {dist_pct}% below HWM {hwm:.2f}")
                        pos["current_sl"] = new_sl
                        pos["trailing_active"] = 1
                else:  # SELL
                    new_sl = hwm * (1.0 + dist_pct / 100.0)
                    if new_sl < current_sl:
                        trade_db.update_position(pos_id, current_sl=new_sl, trailing_active=1)
                        trade_db.log_event(pos_id, "SL_MOVED", current_sl, new_sl, f"Trailing SL adjusted to {dist_pct}% above Low HWM {hwm:.2f}")
                        pos["current_sl"] = new_sl
                        pos["trailing_active"] = 1

    def _execute_exit(self, pos, exit_price, reason):
        pos_id = pos["id"]
        log.info("Executing exit order for %s (%s). Reason: %s", pos["symbol"], pos["direction"], reason)

        # Opposite direction action
        exit_action = "SELL" if pos["direction"] == "BUY" else "BUY"

        # Mock OrderRequest class for the place_order adapter
        class ExitRequest:
            def __init__(self, symbol, exchange, action, quantity):
                self.symbol = symbol
                self.exchange = exchange
                self.action = action
                self.quantity = quantity
                self.strategy = "UTBotSR_TradeManager"
                self.price_type = "MARKET"
                self.product = pos.get("product", "MIS")
                self.price = 0.0
                self.trigger_price = 0.0

        req = ExitRequest(pos["symbol"], pos["exchange"], exit_action, pos["quantity"])
        
        try:
            res = adapter_place_order(self.config, req)
            if res.get("status") == "success":
                pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["direction"] == "BUY" else ((pos["entry_price"] - exit_price) / pos["entry_price"] * 100)
                
                trade_db.update_position(
                    pos_id,
                    status="CLOSED",
                    close_reason=reason,
                    close_price=exit_price,
                    close_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    pnl_pct=round(pnl_pct, 2)
                )
                trade_db.log_event(pos_id, "EXIT_TRIGGERED", None, exit_price, f"Position closed via {reason} at {exit_price}")
                
                # Send alert
                self._alert_exit(pos, exit_price, reason, pnl_pct)

                # Remove from active list
                with self.lock:
                    self.active_positions.pop(pos_id, None)

                # Unsubscribe
                if self.ws_connected and self.client:
                    try:
                        self.client.unsubscribe_ltp([{"exchange": pos["exchange"], "symbol": pos["symbol"]}])
                    except Exception:
                        pass
            else:
                log.error("Failed to place exit order for position %d: %s", pos_id, res.get("message"))
                trade_db.update_position(pos_id, status="ERROR", close_reason="EXIT_FAILED")
                trade_db.log_event(pos_id, "ERROR", None, None, f"Exit order placement failed: {res.get('message')}")
        except Exception as e:
            log.error("Exception executing exit for position %d: %s", pos_id, e)

    def _execute_partial_exit(self, pos, price, pe_cfg):
        pos_id = pos["id"]
        qty_fraction = float(pe_cfg.get("exit_qty_fraction", 0.5))
        exit_qty = int(pos["quantity"] * qty_fraction)
        if exit_qty <= 0:
            return

        exit_action = "SELL" if pos["direction"] == "BUY" else "BUY"
        log.info("Executing partial exit of %d shares for %s. Qty remaining: %d", exit_qty, pos["symbol"], pos["quantity"] - exit_qty)

        class PartialExitRequest:
            def __init__(self, symbol, exchange, action, quantity):
                self.symbol = symbol
                self.exchange = exchange
                self.action = action
                self.quantity = quantity
                self.strategy = "UTBotSR_TradeManager"
                self.price_type = "MARKET"
                self.product = pos.get("product", "MIS")
                self.price = 0.0
                self.trigger_price = 0.0

        req = PartialExitRequest(pos["symbol"], pos["exchange"], exit_action, exit_qty)

        try:
            res = adapter_place_order(self.config, req)
            if res.get("status") == "success":
                new_qty = pos["quantity"] - exit_qty
                
                # Modify SL to breakeven if configured
                updates = {"quantity": new_qty, "partial_exit_done": 1}
                note_suffix = ""
                if pe_cfg.get("move_sl_to_breakeven", True):
                    updates["current_sl"] = pos["entry_price"]
                    pos["current_sl"] = pos["entry_price"]
                    note_suffix = " & SL adjusted to breakeven entry"

                trade_db.update_position(pos_id, **updates)
                trade_db.log_event(pos_id, "PARTIAL_EXIT", pos["quantity"], new_qty, f"Partial exit of {exit_qty} shares completed{note_suffix}")
                
                pos["quantity"] = new_qty
                pos["partial_exit_done"] = 1
                
                # Send alert
                self._alert_partial_exit(pos, exit_qty, price)
        except Exception as e:
            log.error("Partial exit execution error for position %d: %s", pos_id, e)

    def open_position(self, order_result, req, config):
        # Set up default SL & target from config
        tm_cfg = config.get("trade_management", {})
        sl_pct = float(tm_cfg.get("stop_loss_pct", 1.0))
        tgt_pct = float(tm_cfg.get("target_pct", 2.0))

        entry_price = float(req.price) if (req.price and req.price_type == "LIMIT") else float(order_result.get("order", {}).get("price", 0.0))
        if entry_price <= 0:
            # Fallback if execution report doesn't contain entry price
            try:
                entry_price = adapter_get_ltp(config, req.symbol, req.exchange)
            except Exception:
                entry_price = 0.0

        if entry_price <= 0:
            log.error("Could not determine valid entry price for symbol %s. Skipping monitoring.", req.symbol)
            return

        direction = req.action.upper()
        
        # Calculate SL and Target: Priority S/R indicators from results if available, else % fallbacks
        sl_val = None
        target_val = None
        
        # Parse computed SL/Tgt values from frontend or scanner context if passed (e.g. req.stop_loss)
        # Fallback to percentage calculation:
        if direction == "BUY":
            sl_val = entry_price * (1.0 - sl_pct / 100.0)
            target_val = entry_price * (1.0 + tgt_pct / 100.0)
        else:
            sl_val = entry_price * (1.0 + sl_pct / 100.0)
            target_val = entry_price * (1.0 - tgt_pct / 100.0)

        pos_dict = {
            "order_id":        order_result.get("orderid"),
            "symbol":          req.symbol,
            "exchange":        req.exchange,
            "direction":       direction,
            "quantity":        req.quantity,
            "entry_price":     entry_price,
            "current_sl":      round(sl_val, 2),
            "initial_sl":      round(sl_val, 2),
            "target_price":    round(target_val, 2),
            "high_water_mark": entry_price,        # starts at entry, updated on each tick
            "profit_locked":   0,
            "trailing_active": 0,
            "partial_exit_done": 0,
            "timeframe":       config.get("scan_timeframe"),
            "product":         req.product
        }

        try:
            pos_id = trade_db.open_position_db(pos_dict)
            pos_dict["id"] = pos_id
            
            with self.lock:
                self.active_positions[pos_id] = pos_dict
            
            # Subscribe WebSocket to the new instrument
            if self.ws_connected and self.client:
                self.client.subscribe_ltp([{"exchange": req.exchange, "symbol": req.symbol}], on_data_received=self._on_ltp_tick)
            
            log.info("Registered position %s for auto-trade monitoring. ID: %d, SL: %.2f, Target: %.2f", req.symbol, pos_id, sl_val, target_val)
        except Exception as e:
            log.error("Failed to register position for monitoring: %s", e)

    # ---------------------------------------------------------------------------
    # Alerts and Notifications
    # ---------------------------------------------------------------------------
    def _alert_exit(self, pos, exit_price, reason, pnl):
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"🔔 <b>Trade Closed ({reason})</b>\n"
            f"Symbol: <code>{pos['symbol']}</code> | {pos['direction']}\n"
            f"Entry: ₹{pos['entry_price']:.2f} | Exit: ₹{exit_price:.2f}\n"
            f"PnL: {emoji} <b>{pnl:.2f}%</b>"
        )
        if self.config.get("trade_management", {}).get("notifications", {}).get("on_exit", True):
            send_telegram_alert(msg, priority=8, config=self.config)

    def _alert_partial_exit(self, pos, qty, price):
        msg = (
            f"⚠️ <b>Partial Exit Executed</b>\n"
            f"Symbol: <code>{pos['symbol']}</code> | Sold {qty} shares @ ₹{price:.2f}\n"
            f"Remaining Qty: {pos['quantity']}"
        )
        send_telegram_alert(msg, priority=8, config=self.config)

    def _alert_sl_update(self, pos, reason):
        msg = (
            f"⚙️ <b>Stop Loss Adjusted ({reason})</b>\n"
            f"Symbol: <code>{pos['symbol']}</code> | New SL: ₹{pos['current_sl']:.2f}"
        )
        if self.config.get("trade_management", {}).get("notifications", {}).get("on_sl_move", False):
            send_telegram_alert(msg, priority=8, config=self.config)
