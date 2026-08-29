"""
trade_management/monitor.py
============================
PositionMonitor — the infrastructure layer.

Responsibilities:
  - Lifecycle management (start / stop)
  - OpenAlgo WebSocket connection + reconnect loop
  - Fallback HTTP-polling loop (when WS is unavailable)
  - Routing LTP ticks → rules_engine → executor
  - Registering new positions after order placement

Business logic has been moved to rules_engine.py.
Order placement and DB updates live in executor.py.
Alerts are in alerts.py.
"""

from __future__ import annotations
import time
import logging
import threading
from datetime import datetime

import trade_db
from trading_adapter import get_ltp as adapter_get_ltp
from .models import calc_sl_price, calc_target_price, ExitOrderRequest
from . import rules_engine, executor, alerts

log = logging.getLogger("UTBotSRChannelsScanner")


class PositionMonitor:
    """
    Thread-safe monitor that watches open positions and automatically manages
    them according to the rules configured in config.yml under `trade_management`.

    Usage (from app.py — unchanged)
    --------------------------------
        monitor = PositionMonitor()
        monitor.start(cfg)        # called on FastAPI startup
        monitor.stop()            # called on FastAPI shutdown
        monitor.open_position(order_result, req, cfg)   # called after order placement
    """

    def __init__(self):
        self.active_positions: dict = {}   # {pos_id: pos_dict}
        self.ws_connected: bool = False
        self.client = None
        self.monitor_thread: threading.Thread | None = None
        self.ws_thread: threading.Thread | None = None
        self.running: bool = False
        self.lock: threading.Lock = threading.Lock()
        self.config: dict = {}

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self, config: dict) -> None:
        self.config = config
        tm_cfg = config.get("trade_management", {})
        if not tm_cfg.get("enabled", False):
            log.info("Trade Management is disabled in config.yml. Position monitoring will not start.")
            return

        self.running = True

        # Determine allowed direction from config
        oa_cfg = config.get("openalgo", {})
        allowed_actions = str(oa_cfg.get("allowed_actions", "BOTH")).upper()

        # Restore open positions from database on startup
        # Skip positions whose direction violates allowed_actions so stale
        # positions from a previous session don't get managed or exited.
        open_pos = trade_db.get_open_positions()
        skipped = 0
        with self.lock:
            for pos in open_pos:
                direction = str(pos.get("direction", "BUY")).upper()
                if allowed_actions == "BUY_ONLY" and direction != "BUY":
                    log.warning(
                        "Skipping stale %s %s position (id=%s) — allowed_actions=BUY_ONLY",
                        direction, pos.get("symbol"), pos.get("id"),
                    )
                    skipped += 1
                    continue
                if allowed_actions == "SELL_ONLY" and direction != "SELL":
                    log.warning(
                        "Skipping stale %s %s position (id=%s) — allowed_actions=SELL_ONLY",
                        direction, pos.get("symbol"), pos.get("id"),
                    )
                    skipped += 1
                    continue
                self.active_positions[pos["id"]] = pos
        log.info(
            "Trade Manager started. Loaded %d open position(s) from database (%d skipped — direction filter).",
            len(open_pos) - skipped, skipped,
        )

        # Background threads
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop, daemon=True, name="TM-PollLoop"
        )
        self.monitor_thread.start()

        self.ws_thread = threading.Thread(
            target=self._ws_connect_loop, daemon=True, name="TM-WSLoop"
        )
        self.ws_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        log.info("Trade Manager stopped.")

    # -----------------------------------------------------------------------
    # WebSocket connection loop
    # -----------------------------------------------------------------------

    def _ws_connect_loop(self) -> None:
        from openalgo import api as oa_api

        oa_cfg   = self.config.get("openalgo", {})
        ws_url   = oa_cfg.get("ws_url", "ws://127.0.0.1:8765")
        base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
        api_key  = oa_cfg.get("apikey", "")

        while self.running:
            if not self.ws_connected:
                try:
                    log.info("Connecting to OpenAlgo WebSocket: %s", ws_url)
                    self.client = oa_api(api_key=api_key, host=base_url, ws_url=ws_url)
                    self.client.connect()
                    self.ws_connected = True
                    log.info("✅ OpenAlgo WebSocket connected.")

                    # Subscribe to an empty list first to register the callback
                    self.client.subscribe_ltp([], on_data_received=self._on_ltp_tick)

                    # Re-subscribe to all currently monitored instruments
                    with self.lock:
                        instruments = [
                            {"exchange": p["exchange"], "symbol": p["symbol"]}
                            for p in self.active_positions.values()
                        ]
                    if instruments:
                        self.client.subscribe_ltp(instruments, on_data_received=self._on_ltp_tick)

                except Exception as exc:
                    self.ws_connected = False
                    log.error("WebSocket connection failed: %s. Retrying in 10 s…", exc)
                    time.sleep(10)
            else:
                time.sleep(5)   # keep thread alive, WS handles its own ping

    # -----------------------------------------------------------------------
    # WebSocket tick callback
    # -----------------------------------------------------------------------

    def _on_ltp_tick(self, data: dict) -> None:
        """
        Called by the OpenAlgo WS client for every LTP update.
        data format: {"symbol": "INFY", "exchange": "NSE", "ltp": 1423.55}
        """
        if not data or not isinstance(data, dict):
            return
        symbol = data.get("symbol")
        ltp    = data.get("ltp")
        if symbol is None or ltp is None:
            return
        try:
            ltp = float(ltp)
        except (ValueError, TypeError):
            return

        with self.lock:
            matching = [
                pos for pos in self.active_positions.values()
                if pos["symbol"] == symbol
            ]

        for pos in matching:
            self._process_tick(pos, ltp)

    # -----------------------------------------------------------------------
    # HTTP polling fallback
    # -----------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        tm_cfg   = self.config.get("trade_management", {})
        interval = float(tm_cfg.get("poll_interval_seconds", 5))

        while self.running:
            # Synchronize in-memory active_positions with open positions in trade_db
            try:
                db_positions = trade_db.get_open_positions()
                with self.lock:
                    db_ids = {p["id"] for p in db_positions}
                    for p in db_positions:
                        if p["id"] not in self.active_positions:
                            self.active_positions[p["id"]] = p
                            log.info("📌 PositionMonitor synced open position #%d (%s %s) from DB", p["id"], p["direction"], p["symbol"])
                    stale_ids = [pid for pid in self.active_positions if pid not in db_ids]
                    for pid in stale_ids:
                        del self.active_positions[pid]
            except Exception as sync_err:
                log.debug("PositionMonitor DB sync error: %s", sync_err)

            if not self.ws_connected:
                with self.lock:
                    snapshot = list(self.active_positions.values())

                # Group positions by (symbol, exchange) to fetch LTP only once per unique stock
                sym_groups: dict = {}
                for p in snapshot:
                    key = (p["symbol"], p.get("exchange", "NSE"))
                    sym_groups.setdefault(key, []).append(p)

                for (symbol, exchange), pos_list in sym_groups.items():
                    if not self.running:
                        break
                    try:
                        ltp = adapter_get_ltp(self.config, symbol, exchange)
                        for p in pos_list:
                            with self.lock:
                                live_pos = self.active_positions.get(p["id"])
                            if live_pos is not None:
                                self._process_tick(live_pos, ltp)
                    except Exception as exc:
                        log.error("Polling LTP failed for %s: %s", symbol, exc)

            time.sleep(interval)

    # -----------------------------------------------------------------------
    # Core tick processor
    # -----------------------------------------------------------------------

    def _process_tick(self, pos: dict, ltp: float) -> None:
        """
        Route a single price update through the rules engine, then dispatch
        each resulting action to the executor.

        This method is called under lock for WS ticks. For polling the lock
        is released before calling, but a fresh reference is taken above.
        """
        tm_cfg = self.config.get("trade_management", {})

        # Evaluate all rules — pure function, no side-effects
        actions = rules_engine.evaluate(pos, ltp, tm_cfg)

        # Persist HWM if rules_engine updated it
        if pos.pop("_hwm_dirty", False):
            trade_db.update_position(pos["id"], high_water_mark=pos["high_water_mark"])

        # Dispatch each action — may mutate pos dict and active_positions
        for action in actions:
            executor.dispatch(
                action         = action,
                pos            = pos,
                ltp            = ltp,
                config         = self.config,
                active_positions = self.active_positions,
                lock           = self.lock,
                ws_client      = self.client if self.ws_connected else None,
            )
            # Stop processing further actions if position was closed
            if pos["id"] not in self.active_positions:
                break

    # -----------------------------------------------------------------------
    # Register a new position after order placement
    # -----------------------------------------------------------------------

    def open_position(self, order_result: dict, req, config: dict) -> None:
        """
        Called by app.py after a successful order is placed.
        Computes initial SL + target, persists to DB, and starts monitoring.
        Enforces allowed_actions filter — refuses to register a position whose
        direction violates BUY_ONLY / SELL_ONLY to prevent stale positions
        from accumulating across restarts.
        """
        direction = str(getattr(req, "action", "BUY")).upper()
        oa_cfg = config.get("openalgo", {})
        allowed_actions = str(oa_cfg.get("allowed_actions", "BOTH")).upper()

        if allowed_actions == "BUY_ONLY" and direction != "BUY":
            log.info(
                "[%s] Skipping position registration — direction=%s violates allowed_actions=BUY_ONLY",
                getattr(req, "symbol", "?"), direction,
            )
            return
        if allowed_actions == "SELL_ONLY" and direction != "SELL":
            log.info(
                "[%s] Skipping position registration — direction=%s violates allowed_actions=SELL_ONLY",
                getattr(req, "symbol", "?"), direction,
            )
            return

        tm_cfg  = config.get("trade_management", {})
        sl_pct  = float(tm_cfg.get("stop_loss_pct", 1.0))
        tgt_pct = float(tm_cfg.get("target_pct", 2.0))

        # Resolve entry price
        entry_price = float(req.price) if (req.price and req.price_type == "LIMIT") else \
                      float(order_result.get("order", {}).get("price", 0.0))
        if entry_price <= 0:
            try:
                entry_price = adapter_get_ltp(config, req.symbol, req.exchange)
            except Exception:
                entry_price = 0.0

        if entry_price <= 0:
            log.error(
                "Cannot determine entry price for %s. Position will not be monitored.",
                req.symbol,
            )
            return

        direction  = str(getattr(req, "action", "BUY")).upper()
        
        # Check if custom technical stop loss / target were supplied on req or order_result
        custom_sl = getattr(req, "stop_loss", None) or getattr(req, "current_sl", None)
        if custom_sl is None and isinstance(order_result, dict):
            custom_sl = order_result.get("stop_loss") or order_result.get("current_sl")
            
        custom_target = getattr(req, "target", None) or getattr(req, "target_price", None)
        if custom_target is None and isinstance(order_result, dict):
            custom_target = order_result.get("target") or order_result.get("target_price")

        try:
            sl_val = float(custom_sl) if (custom_sl is not None and float(custom_sl) > 0) else calc_sl_price(entry_price, sl_pct, direction)
        except (ValueError, TypeError):
            sl_val = calc_sl_price(entry_price, sl_pct, direction)

        try:
            target_val = float(custom_target) if (custom_target is not None and float(custom_target) > 0) else calc_target_price(entry_price, tgt_pct, direction)
        except (ValueError, TypeError):
            target_val = calc_target_price(entry_price, tgt_pct, direction)

        pos_dict = {
            "order_id":          order_result.get("orderid"),
            "symbol":            req.symbol,
            "exchange":          req.exchange,
            "direction":         direction,
            "quantity":          req.quantity,
            "entry_price":       entry_price,
            "current_sl":        round(sl_val, 2),
            "initial_sl":        round(sl_val, 2),
            "target_price":      round(target_val, 2),
            "high_water_mark":   entry_price,
            "profit_locked":     0,
            "profit_lock_tier":  0,
            "trailing_active":   0,
            "partial_exit_tier": 0,
            "timeframe":         config.get("candle_timeframe", config.get("scan_timeframe", "5m")),
            "product":           getattr(req, "product", "MIS"),
        }

        try:
            pos_id = trade_db.open_position_db(pos_dict)
            pos_dict["id"] = pos_id

            with self.lock:
                self.active_positions[pos_id] = pos_dict

            # Subscribe this instrument to WS
            if self.ws_connected and self.client:
                self.client.subscribe_ltp(
                    [{"exchange": req.exchange, "symbol": req.symbol}],
                    on_data_received=self._on_ltp_tick,
                )

            log.info(
                "✅ Registered position #%d: %s %s | Entry: ₹%.2f | SL: ₹%.2f | Target: ₹%.2f",
                pos_id, direction, req.symbol, entry_price, sl_val, target_val,
            )

            alerts.alert_position_opened(pos_dict, config)

        except Exception as exc:
            log.error("Failed to register position for monitoring: %s", exc)

    # -----------------------------------------------------------------------
    # Manual close (called from app.py /api/positions/{id}/close)
    # -----------------------------------------------------------------------

    def _execute_exit(self, pos: dict, ltp: float, reason: str) -> None:
        """
        Public-facing wrapper for manual exits triggered via the API endpoint.
        Delegates to executor.execute_full_exit.
        """
        executor.execute_full_exit(
            pos             = pos,
            exit_price      = ltp,
            reason          = reason,
            config          = self.config,
            active_positions= self.active_positions,
            lock            = self.lock,
            ws_client       = self.client if self.ws_connected else None,
        )
