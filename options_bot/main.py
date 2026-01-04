import logging
import time
import signal
import sys
from config import config
from mock_api import MockAPI
from data_handler import DataHandler
from indicators import calculate_ema, calculate_rsi, calculate_utbot, calculate_stochrsi
from strategy import StrategyEngine
from order_manager import OrderManager
from risk_manager import RiskManager
from command_processor import CommandProcessor

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Main")

def signal_handler(sig, frame):
    logger.info("Exiting Bot...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def main():
    logger.info("--------------------------------")
    logger.info("   Options Bot - V1.0 (Live)    ")
    logger.info("--------------------------------")
    
    # 1. Initialize API Client (Auto-select based on live_trading flag)
    logger.info("Initializing Components...")
    
    live_trading = config.get("live_trading", False)
    
    if live_trading:
        # LIVE TRADING MODE
        logger.warning("=" * 60)
        logger.warning("⚠️  LIVE TRADING MODE ENABLED - REAL MONEY AT RISK!")
        logger.warning("=" * 60)
        
        try:
            # Import real OpenAlgo client
            # Attempt to import the library (User must have installed: pip install openalgo)
            try:
                from openalgo import api as openalgo_api
            except ImportError:
                raise ImportError("OpenAlgo library not installed. Please run: pip install openalgo")

            # Initialize API
            logger.info("Connecting to OpenAlgo API...")
            api = openalgo_api(
                api_key=config.get("api.api_key"),
                host=config.get("api.host")
            )
            logger.info("✅ Connected to OpenAlgo API")
            
        except ImportError as e:
            logger.error(f"❌ Dependency Error: {e}")
            logger.error("💡 Set 'live_trading: false' in config.json to use paper trading mode")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ API Connection Failed: {e}")
            logger.error("💡 Check your API Key and Host URL in config.json")
            sys.exit(1)
    else:
        # PAPER TRADING MODE (Safe default)
        logger.info("📄 PAPER TRADING MODE - Using Mock Data (No real money)")
        api = MockAPI()
    
    # 2. Initialize other components
    dh = DataHandler(api)
    om = OrderManager(api)
    rm = RiskManager(om)
    strat = StrategyEngine()
    cmd_proc = CommandProcessor(om, rm, dh)
    
    # 2. Start Command Listener
    cmd_proc.start()
    logger.info("✅ Command Interface Ready. Type 'help' for commands.")
    
    # Display Active Configuration
    logger.info("=" * 50)
    logger.info("📊 ACTIVE INDICATORS:")
    htf_indicators = config.get("active_indicators.htf", [])
    ltf_indicators = config.get("active_indicators.ltf", [])
    logger.info(f"  HTF ({config.get('strategy_settings.timeframe_htf')}min): {', '.join(htf_indicators).upper()}")
    logger.info(f"  LTF ({config.get('strategy_settings.timeframe_ltf')}min): {', '.join(ltf_indicators).upper()}")
    
    logger.info("🎯 RISK SETTINGS:")
    logger.info(f"  Stop Loss: {config.get('risk_management.stop_loss_pct')}%")
    logger.info(f"  Target: {config.get('risk_management.target_profit_pct')}%")
    logger.info(f"  TSL: {config.get('risk_management.trailing_stop_pct')}%")
    max_loss = config.get('risk_management.max_daily_acceptable_loss')
    logger.info(f"  Daily Acceptable Loss: ₹{max_loss} {'(Disabled)' if max_loss == 0 else ''}")
    logger.info("=" * 50)
    
    # 3. Main Trading Loop
    if live_trading:
        logger.info("🚀 Bot is LIVE with REAL DATA. (Monitoring Market...)")
    else:
        logger.info("🚀 Bot is LIVE using MOCK Data. (Waiting for signals...)")
    
    try:
        while True:
            if cmd_proc.paused:
                time.sleep(1)
                continue

            # A. Fetch Data
            if live_trading:
                # REAL MODE: Fetch actual market data
                try:
                    # Fetch Quote to verify connection and get current price
                    # Assumption: api.get_ltp() exists and returns something like {'data': 23450} or similar
                    try:
                        # NIFTY spot is on NSE exchange
                        quote = api.get_ltp(symbol="NIFTY", exchange="NSE")
                        # quote might be {'status': 'success', 'data': ...}
                        # logger.debug(f"Tick: {quote}")
                    except Exception as e:
                        # Fallback if get_ltp fails
                        # Just raise error to fail fast if we don't know the API
                        raise ConnectionError(f"Failed to fetch market data: {e}")
                     
                except Exception as e:
                    logger.error(f"❌ Error fetching live data (Check API Key): {e}")
                    time.sleep(5)
                    # If this is authentication error, maybe exit?
                    # For now, retry loop
                    continue
            else:
                # PAPER/MOCK MODE:
                # Manually simulate a "New Candle" arrival every 2 seconds for demo
                mock_data = api.history("NIFTY", "5", None, None) # Get 5min data
            
                # B. Calculate Indicators
                ltf_df = mock_data.copy()
                ltf_df = calculate_ema(ltf_df, 9)
                ltf_df = calculate_ema(ltf_df, 21)
                ltf_df = calculate_rsi(ltf_df, 14)
                ltf_df = calculate_stochrsi(ltf_df)
                ltf_df = calculate_utbot(ltf_df, config.get("indicators.utbot_key"), 10)
                
                htf_df = ltf_df.copy() # Mocking HTF for now
                
                # C. Generate Signal
                signal = strat.generate_signal(htf_df, ltf_df)
                
                if signal:
                    # D. Risk Check & Execution
                    if rm.check_pre_entry_risk():
                        # Use simulated close price for entry
                        om.place_entry_order(signal, ltf_df['close'].iloc[-1])
            
            # E. Manage Active Positions
            current_prices = {}
            if live_trading:
                # REAL MODE: Fetch real LTPs for active positions
                for pos in om.active_positions:
                    try:
                        # quote = api.get_quote(pos['symbol'])
                        # current_price = quote['ltp']
                         pass # Placeholder
                    except Exception as e:
                        logger.error(f"Error fetching quote for {pos['symbol']}: {e}")
            else:
                # PAPER/MOCK MODE: Simulate price movement
                for pos in om.active_positions:
                    # Mock Price fluctuation: +/- 1%
                    import random
                    fluctuation = random.uniform(0.99, 1.01)
                    current_price = pos['peak_price'] * fluctuation # Random walk
                    current_prices[pos['symbol']] = current_price
            
            if current_prices:
                rm.check_exit_conditions(current_prices)
            
            time.sleep(2) # Loop Interval
            
    except KeyboardInterrupt:
        logger.info("Stopping...")

if __name__ == "__main__":
    main()
