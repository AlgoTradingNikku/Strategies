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

        # Restore open positions from database on startup
        open_pos = trade_db.get_open_positions()
        with self.lock:
            for pos in open_pos:
                self.active_positions[pos["id"]] = pos
        log.info(
            "Trade Manager started. Loaded %d open position(s) from database.",
            len(open_pos),
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
            if not self.ws_connected:
                with self.lock:
                    snapshot = list(self.active_positions.values())

                for pos in snapshot:
                    if not self.running:
                        break
                    try:
                        ltp = adapter_get_ltp(self.config, pos["symbol"], pos["exchange"])
                        # Re-acquire lock to get fresh position reference
                        with self.lock:
                            live_pos = self.active_positions.get(pos["id"])
                        if live_pos is not None:
                            self._process_tick(live_pos, ltp)
                    except Exception as exc:
                        log.error("Polling LTP failed for %s: %s", pos["symbol"], exc)

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
        """
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

        direction  = req.action.upper()
        sl_val     = calc_sl_price(entry_price, sl_pct, direction)
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
            "timeframe":         config.get("scan_timeframe"),
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
