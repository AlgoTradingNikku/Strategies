"""
telegram.py
===========
Telegram Alert Notifier for Bot-NSE-Options.
Sends buy/sell alerts and trade updates directly via Telegram Bot API or OpenAlgo notification.
"""

import logging
import requests

log = logging.getLogger("UTBotSRChannelsScanner")


def send_telegram_alert(config: dict, message: str) -> bool:
    tg_cfg = config.get("telegram", {})
    if not tg_cfg.get("enabled", True):
        return False

    mode = tg_cfg.get("mode", "direct").lower()
    bot_token = tg_cfg.get("bot_token", "")
    chat_id = tg_cfg.get("chat_id", "")

    if mode == "direct":
        if not bot_token or not chat_id:
            log.warning("Telegram direct mode missing bot_token or chat_id")
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        try:
            r = requests.post(url, json=payload, timeout=10)
            return r.status_code == 200
        except Exception as e:
            log.error("Telegram send alert error: %s", e)
            return False
    else:
        oa_cfg = config.get("openalgo", {})
        url = f"{oa_cfg.get('base_url', 'http://127.0.0.1:5000')}/api/v1/telegram/notify"
        payload = {
            "apikey": oa_cfg.get("apikey", ""),
            "username": oa_cfg.get("username", ""),
            "message": message,
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            return r.status_code == 200
        except Exception as e:
            log.error("Telegram OpenAlgo alert error: %s", e)
            return False
