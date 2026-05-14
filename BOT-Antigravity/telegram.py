import requests
import yaml

def load_config(path="config.yml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def send_telegram_alert(message, priority=5):
    """
    Send a Telegram alert using the configured mode.

    Modes:
      - "openalgo" : Route through OpenAlgo server's Telegram endpoint.
      - "direct"   : Send directly via Telegram Bot API (no OpenAlgo needed).
    """
    config = load_config()
    tg_cfg = config.get("telegram", {})
    mode = tg_cfg.get("mode", "openalgo").lower()

    if mode == "direct":
        return _send_direct(tg_cfg, message)
    else:
        return _send_via_openalgo(config, message, priority)


def _send_via_openalgo(config, message, priority):
    """Send Telegram alert through OpenAlgo server."""
    url = f"{config['openalgo']['base_url']}/api/v1/telegram/notify"
    payload = {
        "apikey": config['openalgo']['apikey'],
        "username": config['openalgo']['username'],
        "message": message,
        "priority": priority
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Could not connect to OpenAlgo server. Is it running on port 5000?"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP error: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {e}"}


def _send_direct(tg_cfg, message):
    """Send Telegram alert directly via Telegram Bot API."""
    bot_token = tg_cfg.get("bot_token", "")
    chat_id = tg_cfg.get("chat_id", "")

    if not bot_token or not chat_id:
        return {"error": "Telegram direct mode requires bot_token and chat_id in config.yml"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
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


# Usage
#if __name__ == "__main__":
#    result = send_telegram_alert(
#        message="Sell Signal Generated on 5 min timeframe",
#        # message="📊 Daily Trading Summary\n─────────────────────\n✅ Winning Trades: 8\n❌ Losing Trades: 2\n💰 Net P&L: +₹15,450\n📈 Win Rate: 80%\n\n🎯 Great day! Keep it up!",
#        priority=8
#    )
#    print(result)