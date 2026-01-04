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
    
    # 1. Initialize Components
    logger.info("Initializing Components...")
    api = MockAPI() # Swap with Real OpenAlgo API later
    dh = DataHandler(api)
    om = OrderManager(api)
    rm = RiskManager(om)
    strat = StrategyEngine()
    cmd_proc = CommandProcessor(om, rm, dh)
    
    # 2. Start Command Listener
    cmd_proc.start()
    logger.info("✅ Command Interface Ready. Type 'help' for commands.")
    
    # 3. Main Trading Loop
    logger.info("🚀 Bot is LIVE using MOCK Data. (Waiting for signals...)")
    
    try:
        while True:
            if cmd_proc.paused:
                time.sleep(1)
                continue

            # A. Fetch Data (Mock 1-min poll)
            # In real system, this happens via WebSocket/Polling inside DataHandler
            # Here we manually simulate a "New Candle" arrival every 2 seconds for demo
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
            # For DEMO: We will randomly "inject" a signal pattern occasionally if you want, 
            # but for now let's just let it run on random noise (mostly no signal)
            signal = strat.generate_signal(htf_df, ltf_df)
            
            if signal:
                # D. Risk Check & Execution
                if rm.check_pre_entry_risk():
                    om.place_entry_order(signal, ltf_df['close'].iloc[-1])
            
            # E. Manage Active Positions
            # Pass a mock "current price" dict. 
            # In reality this comes from WebSocket Ticks.
            current_prices = {}
            for pos in om.active_positions:
                # Mock Price fluctuation: +/- 1%
                import random
                fluctuation = random.uniform(0.99, 1.01)
                current_price = pos['peak_price'] * fluctuation # Random walk
                current_prices[pos['symbol']] = current_price
            
            rm.check_exit_conditions(current_prices)
            
            time.sleep(2) # 2 Second Loop Interval
            
    except KeyboardInterrupt:
        logger.info("Stopping...")

if __name__ == "__main__":
    main()
