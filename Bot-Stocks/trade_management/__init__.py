"""
trade_management/__init__.py
============================
Package entry point.

Re-exports PositionMonitor so existing callers (app.py, trade_manager.py shim)
continue to work unchanged:

    from trade_management import PositionMonitor   # ← app.py (via shim)
"""

from .monitor import PositionMonitor

__all__ = ["PositionMonitor"]
