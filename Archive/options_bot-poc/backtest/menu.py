"""
Interactive Backtest Menu - Main Entry Point

Provides a user-friendly menu to:
1. Run Parameter Optimization
2. Run Single Backtest
3. Exit

Usage:
    python backtest/menu.py
"""

import sys
import os
import logging
from datetime import datetime, timedelta

# Add parent directory to import main bot components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.optimizer import ParameterOptimizer
from backtest.data_fetcher import DataFetcher
from backtest.backtest_engine import BacktestEngine
from backtest.report_generator import ReportGenerator
from openalgo_rest import OpenAlgoREST
from config import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("BacktestMenu")

def print_banner():
    """Print welcome banner"""
    print("\n" + "="*70)
    print("  OPTIONS BOT - BACKTEST & OPTIMIZATION SUITE")
    print("="*70 + "\n")

def get_date_range():
    """Interactive prompt for date range"""
    print("\nDate Range Selection:")
    print("1. Last 7 days")
    print("2. Last 30 days")
    print("3. Last 90 days")
    print("4. Custom range")
    
    choice = input("\nSelect option (1-4): ").strip()
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    if choice == "1":
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    elif choice == "2":
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    elif choice == "3":
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    else:
        start_date = input("Enter start date (YYYY-MM-DD): ").strip()
        end_date = input("Enter end date (YYYY-MM-DD): ").strip()
    
    return start_date, end_date

def run_optimization():
    """Run parameter optimization"""
    logger.info("\n" + "="*70)
    logger.info("  PARAMETER OPTIMIZATION MODE")
    logger.info("="*70)
    
    # Get date range
    start_date, end_date = get_date_range()
    
    # Symbol selection
    print("\nSymbol:")
    print("1. NIFTY")
    print("2. BANKNIFTY")
    symbol_choice = input("Select (1-2): ").strip()
    symbol = "NIFTY" if symbol_choice == "1" else "BANKNIFTY"
    
    # Indicator selection
    print("\nOptimize which indicator?")
    print("1. UTBot (Key & ATR)")
    print("2. EMA (Fast & Slow) - Coming soon")
    print("3. RSI (Period & Levels) - Coming soon")
    
    indicator_choice = input("Select (1-3): ").strip()
    
    if indicator_choice != "1":
        print("\nOnly UTBot optimization is available currently.")
        return
    
    # Initialize
    api_key = config.get("api.api_key")
    host = config.get("api.host", "http://127.0.0.1:5000")
    api = OpenAlgoREST(api_key, host)
    
    optimizer = ParameterOptimizer(api)
    
    # Run optimization
    logger.info(f"\nOptimizing UTBot for {symbol} from {start_date} to {end_date}")
    
    results_df = optimizer.optimize_utbot(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date
    )
    
    if not results_df.empty:
        # Print results
        optimizer.print_top_results(results_df, top_n=10)
    else:
        logger.error("Optimization failed to produce results")

def run_backtest():
    """Run single backtest"""
    logger.info("\n" + "="*70)
    logger.info("  SINGLE BACKTEST MODE")
    logger.info("="*70)
    
    # Get date range
    start_date, end_date = get_date_range()
    
    # Symbol selection
    print("\nSymbol:")
    print("1. NIFTY")
    print("2. BANKNIFTY")
    symbol_choice = input("Select (1-2): ").strip()
    symbol = "NIFTY" if symbol_choice == "1" else "BANKNIFTY"
    
    # Initialize
    api_key = config.get("api.api_key")
    host = config.get("api.host", "http://127.0.0.1:5000")
    api = OpenAlgoREST(api_key, host)
    
    # Fetch data
    logger.info("\nFetching historical data...")
    fetcher = DataFetcher(api)
    
    htf_interval = config.get("strategy_settings.timeframe_htf", "15m")
    ltf_interval = config.get("strategy_settings.timeframe_ltf", "3m")
    
    data = fetcher.prepare_data_for_backtest(
        symbol=symbol,
        htf_interval=htf_interval,
        ltf_interval=ltf_interval,
        start_date=start_date,
        end_date=end_date
    )
    
    if data['ltf'].empty:
        logger.error("Failed to fetch data")
        return
    
    logger.info(f"Fetched {len(data['htf'])} HTF candles and {len(data['ltf'])} LTF candles")
    
    # Run backtest
    engine = BacktestEngine(initial_capital=100000)
    
    ltf_df = data['ltf'].copy()
    htf_df = data['htf'].copy()
    
    ltf_df = engine.calculate_indicators(ltf_df, "LTF")
    htf_df = engine.calculate_indicators(htf_df, "HTF")
    
    logger.info("\nRunning simulation...")
    
    for i in range(20, len(ltf_df)):
        current_ltf = ltf_df.iloc[:i+1]
        current_time = current_ltf.iloc[-1]['timestamp']
        spot_price = current_ltf.iloc[-1]['close']
        
        current_htf = htf_df[htf_df['timestamp'] <= current_time]
        if len(current_htf) < 20:
            continue
        
        current_atr = current_ltf.iloc[-1].get('atr', 0) if 'atr' in current_ltf.columns else 0
        engine.check_exit_conditions(spot_price, current_time, current_atr, days_to_expiry=3)
        
        if not engine.active_position:
            signal = engine.strategy.generate_signal(current_htf, current_ltf)
            if signal and isinstance(signal, dict) and signal.get('action') == 'BUY':
                engine.enter_position(signal, spot_price, current_time, days_to_expiry=3)
        
        engine.update_equity(spot_price, current_time, days_to_expiry=3)
    
    if engine.active_position:
        final_spot = ltf_df.iloc[-1]['close']
        final_time = ltf_df.iloc[-1]['timestamp']
        engine.exit_position(final_spot, final_time, "Backtest End", days_to_expiry=3)
    
    # Generate report
    logger.info("\nGenerating performance report...")
    stats = engine.get_statistics()
    
    reporter = ReportGenerator()
    reporter.generate_full_report(stats, engine.trades, engine.equity_curve)
    
    logger.info("\nBacktest complete!\n")

def main_menu():
    """Main interactive menu"""
    while True:
        print_banner()
        print("Main Menu:")
        print("1. Run Optimization")
        print("2. Run Backtest")
        print("3. Exit")
        
        choice = input("\nSelect: ").strip()
        
        if choice == "1":
            try:
                run_optimization()
            except Exception as e:
                logger.error(f"Optimization error: {e}")
                import traceback
                traceback.print_exc()
            
            input("\nPress Enter to continue...")
            
        elif choice == "2":
            try:
                run_backtest()
            except Exception as e:
                logger.error(f"Backtest error: {e}")
                import traceback
                traceback.print_exc()
            
            input("\nPress Enter to continue...")
            
        elif choice == "3":
            print("\nExiting. Happy trading!\n")
            break
        else:
            print("\nInvalid choice. Please select 1-3.")

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Exiting.\n")
        sys.exit(0)
