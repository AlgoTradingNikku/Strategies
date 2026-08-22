"""
tests/conftest.py
=================
Shared pytest configuration for the Bot-Stocks test suite.

Adds the Bot-Stocks project root to ``sys.path`` so tests can ``import
rules_engine`` / ``import signals`` etc. without needing an editable install.
"""

import sys
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parent.parent
if str(_BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOT_ROOT))
