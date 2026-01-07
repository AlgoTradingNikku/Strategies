import requests
import json
import logging
from config import config

logger = logging.getLogger("OpenAlgoREST")

class OpenAlgoREST:
    def __init__(self, api_key, host):
        self.api_key = api_key
        self.host = host.rstrip('/')
        self.base_url = f"{self.host}/api/v1"
        self._symbol_cache = {}
        logger.info(f"OpenAlgo REST Client Initialized at {self.base_url}")

    def get_ltp(self, symbol, exchange):
        """Fetch Last Traded Price"""
        payload = {
            "apikey": self.api_key,
            "symbol": symbol,
            "exchange": exchange
        }
        try:
            r = requests.post(f"{self.base_url}/quotes", json=payload, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get('status') == 'success':
                    # REST returns {"data": {"ltp": 123}, "status": "success"}
                    # The SDK logic expects the inner data dict? 
                    # Actually, our SDK test returned {'ltp': {}}
                    # Let's return exactly what the bot expects: {'ltp': value}
                    data = resp.get('data', {})
                    return {"ltp": data.get('ltp')}
                else:
                    logger.error(f"LTP API Error: {resp.get('message')}")
            else:
                logger.error(f"LTP HTTP Error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"LTP Exception: {e}")
        return {"ltp": None}

    def get_quotes(self, symbol, exchange):
        """Fetch full quotes packet"""
        payload = {
            "apikey": self.api_key,
            "symbol": symbol,
            "exchange": exchange
        }
        try:
            r = requests.post(f"{self.base_url}/quotes", json=payload, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get('status') == 'success':
                    return resp.get('data', {})
            logger.error(f"Quotes Error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Quotes Exception: {e}")
        return {}

    def placeorder(self, **kwargs):
        """Place an order"""
        # SDK uses: symbol, action, exchange, quantity, price, product, price_type
        # REST API expects: apikey, strategy, symbol, action, exchange, quantity, price, pricetype, product
        
        # Map price_type -> pricetype if needed
        pricetype = kwargs.get('price_type', kwargs.get('pricetype', 'MARKET'))
        
        # Prepare payload
        strategy_name = kwargs.get('strategy', config.get('api.strategy_name', 'PythonBot'))
        
        payload = {
            "apikey": self.api_key,
            "strategy": strategy_name,
            "symbol": kwargs.get('symbol'),
            "action": kwargs.get('action'),
            "exchange": kwargs.get('exchange'),
            "quantity": kwargs.get('quantity'),
            "price": kwargs.get('price', 0),
            "pricetype": pricetype,
            "product": kwargs.get('product', 'NRML')
        }
        
        
        try:
            r = requests.post(f"{self.base_url}/placeorder", json=payload, timeout=10)
            return r.json()
        except Exception as e:
            logger.error(f"Order Placement Exception: {e}")
            return {"status": "error", "message": str(e)}

    def get_option_chain(self, symbol, expiry):
        """Fetch option chain for underlying and expiry"""
        # SDK uses: symbol, expiry
        # REST uses: underlying, exchange, expiry_date
        
        # Assumption: NIFTY/BANKNIFTY use NSE_INDEX for underlying in many bridges
        # We can try to guess exchange or make it configurable. 
        # Most bridges use NSE or NSE_INDEX for the underlying.
        # Based on index_discovery.py, NIFTY is on NSE_INDEX
        
        payload = {
            "apikey": self.api_key,
            "underlying": symbol,
            "exchange": "NSE_INDEX", # Default to Index for NIFTY/BANKNIFTY
            "expiry_date": expiry
        }
        try:
            r = requests.post(f"{self.base_url}/optionchain", json=payload, timeout=10)
            if r.status_code == 200:
                resp = r.json()
                if resp.get('status') == 'success':
                    return resp.get('data', [])
            logger.error(f"Option Chain Error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Option Chain Exception: {e}")
        return []

    def history(self, symbol, resolution, start=None, end=None, exchange="NSE_INDEX"):
        """Fetch historical candles and return as DataFrame"""
        import pandas as pd
        from datetime import datetime, timedelta
        
        # Interval handling: SDK uses '5', REST uses '5m'
        interval = resolution
        if isinstance(interval, (int, float)):
            if interval == int(interval):
                interval = f"{int(interval)}m"
            else:
                 interval = f"{interval}m"
        elif isinstance(interval, str):
            if interval.isdigit():
                interval = f"{interval}m"
            elif "." in interval:
                try:
                    f_val = float(interval)
                    if f_val == int(f_val):
                        interval = f"{int(f_val)}m"
                except:
                    pass
            
        # Date handling: if None, use last 2 days
        if not start:
            start = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        if not end:
            end = datetime.now().strftime("%Y-%m-%d")

        payload = {
            "apikey": self.api_key,
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "start_date": start,
            "end_date": end 
        }
        
        try:
            r = requests.post(f"{self.base_url}/history", json=payload, timeout=15)
            if r.status_code == 200:
                resp = r.json()
                if resp.get('status') == 'success':
                    data = resp.get('data', [])
                    # Convert to DataFrame
                    df = pd.DataFrame(data)
                    # OpenAlgo returns specific column names, ensure we match standard (Open, High, Low, Close, Volume)
                    # If columns are already named correctly, great.
                    return df
            logger.error(f"History Error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"History Exception: {e}")
            
        return pd.DataFrame()

    def positionbook(self):
        try:
            r = requests.post(f"{self.base_url}/positionbook", json={"apikey": self.api_key}, timeout=10)
            return r.json().get('data', [])
        except:
            return []

    def get_expiries(self, symbol):
        """Fetch available expiries for a symbol by searching for active options."""
        import re
        payload = {
            "apikey": self.api_key,
            "query": symbol
        }
        try:
            r = requests.post(f"{self.host}/api/v1/search", json=payload, timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', [])
                expiries = set()
                # Pattern: Symbol + 2 digits + 3 letters + 2 digits
                # e.g., NIFTY06JAN26
                pattern = re.compile(rf'{symbol}(\d{{2}}[A-Z]{{3}}\d{{2}})')
                for item in data:
                    sym = item.get('symbol', '')
                    match = pattern.search(sym)
                    if match:
                        expiries.add(match.group(1))
                
                if not expiries:
                    return []
                
                # Sort expiries. We need to sort by date, not alphabetically.
                from datetime import datetime
                expiry_list = list(expiries)
                expiry_list.sort(key=lambda x: datetime.strptime(x, "%d%b%y"))
                return expiry_list
            logger.error(f"Expiries Fetch Error: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Expiries Fetch Exception: {e}")
        return []

    def get_symbol_info(self, symbol):
        """Fetch full symbol info from search API."""
        if symbol in self._symbol_cache:
            return self._symbol_cache[symbol]
            
        payload = {
            "apikey": self.api_key,
            "query": symbol
        }
        try:
            r = requests.post(f"{self.host}/api/v1/search", json=payload, timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', [])
                for item in data:
                    if item.get('symbol') == symbol:
                        self._symbol_cache[symbol] = item
                        return item
            return None
        except Exception as e:
            logger.error(f"Symbol Info Fetch Failed: {e}")
            return None

    def get_lot_size(self, symbol):
        """Helper to get only the lot size."""
        info = self.get_symbol_info(symbol)
        if info:
            try:
                # OpenAlgo usually returns 'lotsize' as string or int
                return int(info.get('lotsize', 50))
            except:
                return 50
        return 50 # Fallback
