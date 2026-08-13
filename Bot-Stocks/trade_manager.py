"""
trade_manager.py — backward-compatible shim
============================================
The trade management logic has been refactored into the `trade_management/`
package for clean separation of concerns:

    trade_management/
      __init__.py       — re-exports PositionMonitor
      models.py         — shared data structures
      rules_engine.py   — pure business logic (profit lock, trailing SL, etc.)
      executor.py       — order placement + DB updates
      alerts.py         — Telegram notifications
      monitor.py        — PositionMonitor class (threading, WS, polling)

This shim ensures that any existing import of the form:
    from trade_manager import PositionMonitor
continues to work without any changes to app.py or other callers.
"""

# Re-export PositionMonitor from the new package location
from trade_management import PositionMonitor  # noqa: F401

__all__ = ["PositionMonitor"]
