import time
import threading
import logging
from dataclasses import fields
from typing import Dict, Any, Optional

import trade_db
import trading_adapter
from .models import Position
from .rules_engine import evaluate_position_rules
from .executor import execute_exit, execute_update_sl

log = logging.getLogger("UTBotSRChannelsScanner")

POSITION_FIELDS = {f.name for f in fields(Position)}


class PositionMonitor:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._cfg: dict = {}

    def start(self, cfg: dict):
        self._cfg = cfg
        tm_cfg = cfg.get("trade_management", {})
        if not tm_cfg.get("enabled", True):
            log.info("Position Monitor disabled in config")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("Position Monitor started")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        log.info("Position Monitor stopped")

    def _run_loop(self):
        interval = float(self._cfg.get("trade_management", {}).get("poll_interval_seconds", 3.0))
        while self._running:
            try:
                active_rows = trade_db.get_active_trades()
                for row in active_rows:
                    filtered_dict = {k: v for k, v in row.items() if k in POSITION_FIELDS}
                    pos = Position(**filtered_dict)
                    ltp = trading_adapter.get_ltp(self._cfg, pos.symbol, pos.exchange)
                    if ltp > 0:
                        pos.current_price = ltp
                        trade_db.update_trade_price(pos.trade_id, ltp)

                        action, val, reason = evaluate_position_rules(pos, self._cfg)
                        if action == "EXIT":
                            execute_exit(pos, self._cfg, reason)
                        elif action == "UPDATE_SL" and val is not None:
                            execute_update_sl(pos, val)
            except Exception as e:
                log.error("Error in PositionMonitor loop: %s", e)
            time.sleep(interval)
