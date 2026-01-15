import json
import threading
import time
import logging
import websocket
from config import config

logger = logging.getLogger("WSHandler")

class WebSocketHandler:
    def __init__(self, api_key, ws_url):
        self.api_key = api_key
        self.ws_url = ws_url
        self.ws = None
        self.ltp_cache = {}  # { 'SYMBOL.EXCHANGE': price }
        self.subscribed_symbols = set()
        self.is_running = False
        self.authenticated = False
        self._lock = threading.Lock()
        
        # Connection management
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        """Starts the WebSocket connection in a background thread"""
        if self.is_running:
            return
        self.is_running = True
        self.thread.start()
        logger.info(f"WebSocket thread started for {self.ws_url}")

    def stop(self):
        self.is_running = False
        if self.ws:
            self.ws.close()

    def _run(self):
        while self.is_running:
            try:
                # websocket.enableTrace(True)
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever()
            except Exception as e:
                logger.error(f"WebSocket Loop Error: {e}")
            
            if self.is_running:
                logger.info("WebSocket reconnecting in 5 seconds...")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info("WebSocket Connection Opened. Authenticating...")
        # 1. Authenticate immediately
        auth_msg = {
            "action": "authenticate",
            "api_key": self.api_key
        }
        ws.send(json.dumps(auth_msg))

    def _on_message(self, ws, message):
        try:
            msg = json.loads(message)
            msg_type = msg.get("status") or msg.get("type")
            
            if (msg.get("type") == "auth" and msg.get("status") == "success") or \
               (msg_type == "success" and "Authentication" in msg.get("message", "")):
                logger.info("WS Authenticated Successfully")
                self.authenticated = True
                # Re-subscribe to existing symbols on reconnect
                if self.subscribed_symbols:
                    self._subscribe_all()
            
            elif msg_type == "market_data":
                # Handle different bridge formats for topic/symbol
                topic = msg.get("topic")
                if not topic:
                    symbol = msg.get("symbol")
                    exchange = msg.get("exchange")
                    if symbol and exchange:
                        topic = f"{symbol}.{exchange}"
                    elif symbol:
                        topic = symbol
                
                data = msg.get("data", {})
                ltp = data.get("ltp")
                
                if topic and ltp is not None:
                    with self._lock:
                        self.ltp_cache[topic] = float(ltp)
                        
            elif msg_type == "error":
                logger.error(f"WS API ERROR: {msg.get('message')}")
                
        except Exception as e:
            logger.error(f"WS PARSE ERROR: {e}")

    def _on_error(self, ws, error):
        logger.error(f"WS ERROR: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WS CLOSED: {close_msg} ({close_status_code})")
        self.authenticated = False

    def subscribe(self, symbols):
        """
        Subscribes to a list of symbols. 
        Input can be list of 'SYMBOL.EXCHANGE' or list of dicts {'symbol': '...', 'exchange': '...'}
        """
        new_subscriptions = []
        for s in symbols:
            if isinstance(s, str):
                if '.' in s:
                    sym, exch = s.split('.', 1)
                    sub = {"symbol": sym, "exchange": exch}
                else:
                    # Default to NSE_INDEX if no exchange provided (e.g. for NIFTY)
                    sub = {"symbol": s, "exchange": "NSE_INDEX"}
            else:
                sub = s
            
            # Use dot string as key for tracking unique subscriptions
            sub_key = f"{sub['symbol']}.{sub['exchange']}"
            if sub_key not in self.subscribed_symbols:
                self.subscribed_symbols.add(sub_key)
                new_subscriptions.append(sub)
        
        if new_subscriptions and self.authenticated:
            self._send_subscribe(new_subscriptions)

    def _subscribe_all(self):
        if self.subscribed_symbols:
            logger.info(f"Re-subscribing to: {self.subscribed_symbols}")
            # Convert back to list of dicts
            subs = []
            for s in self.subscribed_symbols:
                sym, exch = s.split('.', 1)
                subs.append({"symbol": sym, "exchange": exch})
            self._send_subscribe(subs)

    def _send_subscribe(self, symbols_dicts):
        """symbols_dicts is a list of {'symbol': '...', 'exchange': '...'}"""
        subscribe_msg = {
            "action": "subscribe",
            "symbols": symbols_dicts, # Correct format: list of dicts
            "mode": 1  # Standard LTP mode
        }
        if self.ws:
            self.ws.send(json.dumps(subscribe_msg))
            logger.info(f"Sent Subscription Request for: {symbols_dicts}")

    def get_ltp(self, symbol_key):
        """
        Get price from cache. 
        symbol_key should be 'SYMBOL.EXCHANGE' e.g. 'NIFTY.NSE_INDEX'
        """
        with self._lock:
            return self.ltp_cache.get(symbol_key)
