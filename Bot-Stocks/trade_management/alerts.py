"""
trade_management/alerts.py
===========================
All Telegram notification helpers for the trade management module.

Each function respects the `trade_management.notifications` sub-section of
config.yml so the user can selectively enable/disable each alert type.

Notification config keys
-------------------------
notifications:
  on_open        : bool   alert when a new position is registered
  on_exit        : bool   alert on full position close
  on_partial_exit: bool   alert on partial (scaled-out) exit
  on_profit_lock : bool   alert when profit is locked (SL ratcheted up)
  on_sl_move     : bool   alert on any trailing SL adjustment
"""

from __future__ import annotations
import logging
from telegram import send_telegram_alert

log = logging.getLogger("UTBotSRChannelsScanner")


def _notif(cfg: dict, key: str, default: bool = False) -> bool:
    """Return the notification toggle for a given key."""
    return cfg.get("trade_management", {}).get("notifications", {}).get(key, default)


# ---------------------------------------------------------------------------
# Position opened
# ---------------------------------------------------------------------------

def alert_position_opened(pos: dict, cfg: dict) -> None:
    if not _notif(cfg, "on_open", False):
        return
    direction = pos["direction"]
    arrow = "📈" if direction == "BUY" else "📉"
    msg = (
        f"{arrow} <b>Position Opened</b>\n"
        f"Symbol: <code>{pos['symbol']}</code> | {direction}\n"
        f"Entry: ₹{pos['entry_price']:.2f} | Qty: {pos['quantity']}\n"
        f"SL: ₹{pos['current_sl']:.2f} | Target: ₹{pos['target_price']:.2f}"
    )
    send_telegram_alert(msg, priority=5, config=cfg)


# ---------------------------------------------------------------------------
# Full position exit
# ---------------------------------------------------------------------------

def alert_exit(pos: dict, exit_price: float, reason: str, pnl_pct: float, cfg: dict) -> None:
    if not _notif(cfg, "on_exit", True):
        return
    emoji = "✅" if pnl_pct >= 0 else "❌"
    reason_label = {
        "TARGET":         "Target Reached 🎯",
        "STOP_LOSS":      "Stop Loss Hit 🛑",
        "MANUAL":         "Manual Exit 👤",
        "EOD_SQUARE_OFF": "EOD Auto Square-off ⏰",
    }.get(reason, reason)
    msg = (
        f"🔔 <b>Trade Closed ({reason_label})</b>\n"
        f"Symbol: <code>{pos['symbol']}</code> | {pos['direction']}\n"
        f"Entry: ₹{pos['entry_price']:.2f} → Exit: ₹{exit_price:.2f}\n"
        f"PnL: {emoji} <b>{pnl_pct:+.2f}%</b>"
    )
    send_telegram_alert(msg, priority=8, config=cfg)


# ---------------------------------------------------------------------------
# Partial exit
# ---------------------------------------------------------------------------

def alert_partial_exit(pos: dict, exit_qty: int, price: float, tier: int, cfg: dict) -> None:
    if not _notif(cfg, "on_partial_exit", True):
        return
    msg = (
        f"⚠️ <b>Partial Exit — Tier {tier + 1}</b>\n"
        f"Symbol: <code>{pos['symbol']}</code> | Exited {exit_qty} shares @ ₹{price:.2f}\n"
        f"Remaining Qty: {pos['quantity']}"
    )
    send_telegram_alert(msg, priority=6, config=cfg)


# ---------------------------------------------------------------------------
# Profit lock SL update
# ---------------------------------------------------------------------------

def alert_profit_lock(pos: dict, old_sl: float, new_sl: float, tier: int, cfg: dict) -> None:
    if not _notif(cfg, "on_profit_lock", True):
        return
    msg = (
        f"🔒 <b>Profit Locked — Tier {tier}</b>\n"
        f"Symbol: <code>{pos['symbol']}</code>\n"
        f"SL moved: ₹{old_sl:.2f} → ₹{new_sl:.2f}"
    )
    send_telegram_alert(msg, priority=6, config=cfg)


# ---------------------------------------------------------------------------
# Trailing SL move
# ---------------------------------------------------------------------------

def alert_sl_move(pos: dict, old_sl: float, new_sl: float, cfg: dict) -> None:
    if not _notif(cfg, "on_sl_move", False):
        return
    msg = (
        f"⚙️ <b>Trailing SL Adjusted</b>\n"
        f"Symbol: <code>{pos['symbol']}</code>\n"
        f"SL moved: ₹{old_sl:.2f} → ₹{new_sl:.2f}"
    )
    send_telegram_alert(msg, priority=4, config=cfg)
