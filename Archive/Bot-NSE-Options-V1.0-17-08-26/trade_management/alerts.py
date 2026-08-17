from .models import Position
import telegram

def notify_position_event(pos: Position, cfg: dict, event_type: str, details: str = ""):
    msg = f"<b>[Bot-NSE-Options Trade Event]</b>\n"
    msg += f"<b>Symbol:</b> {pos.symbol}\n"
    msg += f"<b>Event:</b> {event_type}\n"
    msg += f"<b>Entry:</b> ₹{pos.entry_price:.2f} | <b>LTP:</b> ₹{pos.current_price:.2f}\n"
    msg += f"<b>P&L:</b> ₹{pos.pnl_amount:.2f} ({pos.return_pct:+.2f}%)\n"
    if details:
        msg += f"<b>Note:</b> {details}\n"
    telegram.send_telegram_alert(cfg, msg)
