"""
===============================================================================
  Bot-Options / notifications / notifier.py
  Formatted notifications for Telegram and WhatsApp alerts.
  Integrates direct Telegram and OpenAlgo WhatsApp endpoints.
===============================================================================
"""

import sys
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Add Bot-Stocks to path to import telegram.py
bot_stocks_dir = Path(__file__).resolve().parents[2] / "Bot-Stocks"
if str(bot_stocks_dir) not in sys.path:
    sys.path.insert(0, str(bot_stocks_dir))

try:
    from telegram import send_telegram_alert
except ImportError:
    log.error("Failed to import send_telegram_alert from Bot-Stocks.")
    def send_telegram_alert(message, priority=5, silent=False, config=None):
        log.warning("Placeholder send_telegram_alert called: %s", message)
        return {"status": "skipped", "message": "Import failed"}

def send_alert(message: str, config: dict, oa_client=None, priority: int = 5, silent: bool = False):
    """
    Unified entrypoint to dispatch alerts to enabled notification channels.
    """
    # 1. Telegram
    tg_cfg = config.get("telegram", {})
    if tg_cfg.get("enabled", True):
        try:
            send_telegram_alert(message, priority=priority, silent=silent, config=config)
        except Exception as e:
            log.error("Failed to send Telegram notification: %s", e)

    # 2. WhatsApp
    wa_cfg = config.get("whatsapp", {})
    if wa_cfg.get("enabled", False) and oa_client is not None:
        try:
            # Send message using OpenAlgo WhatsApp REST call
            resp = oa_client.whatsapp(message)
            log.info("WhatsApp notification response: %s", resp)
        except Exception as e:
            log.error("Failed to send WhatsApp notification: %s", e)


def notify_new_signal(sig: dict, config: dict, oa_client=None):
    """Alert on new option signal generation."""
    msg = (
        f"🚨 *NEW OPTION SIGNAL* 🚨\n\n"
        f"Underlying: {sig.get('underlying')} ({sig.get('underlying_price', 0.0)})\n"
        f"Contract: *{sig.get('symbol')}* ({sig.get('option_type')})\n"
        f"Direction: *{sig.get('direction')}* (BUY Option)\n"
        f"Premium LTP: *₹{sig.get('entry_premium')}*\n"
        f"Score: *{sig.get('confidence_score')}/100*\n"
        f"Timeframe: {sig.get('timeframe')}\n"
        f"Reasons:\n"
    )
    for r in sig.get("score_reasons", []):
        msg += f" • {r}\n"
        
    send_alert(msg, config, oa_client, priority=8, silent=False)


def notify_execution(pos: dict, config: dict, oa_client=None):
    """Alert on successful option trade execution."""
    msg = (
        f"📈 *OPTION POSITION OPENED* 📈\n\n"
        f"Contract: *{pos.get('symbol')}*\n"
        f"Action: BUY\n"
        f"Qty: {pos.get('quantity')} ({pos.get('num_lots')} lots)\n"
        f"Entry Premium: *₹{pos.get('entry_premium')}*\n"
        f"Initial SL: *₹{pos.get('current_sl_premium')}*\n"
        f"Target Premium: *₹{pos.get('target_premium')}*\n"
        f"Order ID: {pos.get('order_id')}"
    )
    send_alert(msg, config, oa_client, priority=7, silent=False)


def notify_exit(pos: dict, config: dict, oa_client=None):
    """Alert on position close."""
    pnl = pos.get("pnl_amount", 0.0)
    pnl_pct = pos.get("pnl_pct", 0.0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    
    msg = (
        f"{pnl_emoji} *OPTION POSITION CLOSED* {pnl_emoji}\n\n"
        f"Contract: *{pos.get('symbol')}*\n"
        f"Exit Premium: *₹{pos.get('close_premium')}* (Entry: ₹{pos.get('entry_premium')})\n"
        f"Exit Reason: *{pos.get('close_reason')}*\n"
        f"PnL: *₹{pnl:+.2f} ({pnl_pct:+.1f}%)*\n"
        f"Close Time: {pos.get('close_time')}"
    )
    send_alert(msg, config, oa_client, priority=7, silent=False)


def notify_partial_exit(pos: dict, qty: int, price: float, config: dict, oa_client=None):
    """Alert on partial lot-based exit."""
    msg = (
        f"⚠️ *PARTIAL EXIT EXECUTION* ⚠️\n\n"
        f"Contract: *{pos.get('symbol')}*\n"
        f"Exited Qty: {qty} @ *₹{price}*\n"
        f"Remaining Qty: {pos.get('quantity') - qty}\n"
        f"SL adjusted to Break-even: *₹{pos.get('current_sl_premium')}*"
    )
    send_alert(msg, config, oa_client, priority=6, silent=True)


def notify_profit_lock(pos: dict, threshold: float, locked_sl: float, config: dict, oa_client=None):
    """Alert on profit lock trigger."""
    msg = (
        f"🔒 *PROFIT LOCK TRIGGERED* 🔒\n\n"
        f"Contract: *{pos.get('symbol')}*\n"
        f"Premium crossed +{threshold}% target threshold\n"
        f"SL locked at floor: *₹{locked_sl}*"
    )
    send_alert(msg, config, oa_client, priority=6, silent=True)
