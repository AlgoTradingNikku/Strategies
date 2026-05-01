import requests
import yaml

def load_config(path="config.yml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def send_telegram_alert(message, priority=5):
    config = load_config()
    
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
        return {"error": "Could not connect to server. Is it running on port 5000?"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP error: {e}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {e}"}


# Usage
#if __name__ == "__main__":
#    result = send_telegram_alert(
#        message="Sell Signal Generated on 5 min timeframe",
#        # message="📊 Daily Trading Summary\n─────────────────────\n✅ Winning Trades: 8\n❌ Losing Trades: 2\n💰 Net P&L: +₹15,450\n📈 Win Rate: 80%\n\n🎯 Great day! Keep it up!",
#        priority=8
#    )
#    print(result)