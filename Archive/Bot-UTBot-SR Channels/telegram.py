"""
Telegram alert module for Bot-UTBot-SR Channels scanner.
Sends alerts via Telegram Bot API (direct mode) or via OpenAlgo server.
"""

import requests
import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yml"


def load_config(path=None):
    if path is None:
        path = _CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def send_telegram_alert(message, priority=5, silent=False, config: dict = None):
    """
    Send a Telegram alert using the configured mode.

    Modes:
      - "openalgo" : Route through OpenAlgo server's Telegram endpoint.
      - "direct"   : Send directly via Telegram Bot API (no OpenAlgo needed).

    Parameters
    ----------
    config : dict, optional
        Pre-loaded configuration dict. When None, config.yml is read from disk.
        Pass the scanner's live config dict to avoid a redundant file read on
        every alert.
    """
    if config is None:
        config = load_config()
    tg_cfg = config.get("telegram", {})
    mode = tg_cfg.get("mode", "openalgo").lower()
    if not tg_cfg.get("enabled", True):
        return {"status": "skipped", "message": "Telegram alerts are disabled"}

    if mode == "direct":
        return _send_direct(tg_cfg, message, silent)
    else:
        return _send_via_openalgo(config, message, priority)


def _send_via_openalgo(config, message, priority):
    """Send Telegram alert through OpenAlgo server."""
    url = f"{config['openalgo']['base_url']}/api/v1/telegram/notify"
    payload = {
        "apikey": config["openalgo"]["apikey"],
        "username": config["openalgo"]["username"],
        "message": message,
        "priority": priority,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Could not connect to OpenAlgo server. Is it running on port 5000?"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP error: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {e}"}


def _send_direct(tg_cfg, message, silent=False):
    """Send Telegram alert directly via Telegram Bot API."""
    bot_token = tg_cfg.get("bot_token", "")
    chat_id = tg_cfg.get("chat_id", "")

    if not bot_token or not chat_id:
        return {"error": "Telegram direct mode requires bot_token and chat_id in config.yml"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            return {"status": "success", "message_id": result["result"]["message_id"]}
        else:
            return {"error": result.get("description", "Unknown Telegram API error")}
    except requests.exceptions.ConnectionError:
        return {"error": "Could not connect to Telegram API. Check your internet connection."}
    except requests.exceptions.HTTPError as e:
        return {"error": f"Telegram HTTP error: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Telegram request failed: {e}"}
