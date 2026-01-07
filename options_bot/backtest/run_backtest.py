"""
Backtest Runner - Main Entry Point

Run backtests from command line with custom parameters.

Usage:
    python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05
    python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05 --tsl 12 --target 60
"""

import argparse
import logging
import sys
import os
from datetime import datetime

# Add parent directory to import main bot components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_fetcher import DataFetcher
from backtest.backtest_engine import BacktestEngine
from backtest.report_generator import ReportGenerator
from openalgo_rest import OpenAlgoREST
from config import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('backtest/results/backtest.log')
    ]
)

logger = logging.getLogger("BacktestRunner")

def run_backtest(args):
    """
    Main backtest execution function.
    
    Args:
        args: Command line arguments
    """
    
    logger.info("="*70)
    logger.info("  STARTING BACKTEST")
    logger.info("="*70)
    logger.info(f"  Symbol:        {args.symbol}")
    logger.info(f"  Date Range:    {args.start} to {args.end}")
    logger.info(f"  Capital:       Rs.{args.capital:,.2f}")
    logger.info("="*70 + "\n")
    
    # Initialize API client
    api_key = config.get("api.api_key")
    host = config.get("api.host", "http://127.0.0.1:5000")
    
    api = OpenAlgoREST(api_key, host)
    
    # Fetch historical data
    logger.info("Fetching historical data...")
    fetcher = DataFetcher(api)
    
    htf_interval = config.get("strategy_settings.timeframe_htf", "15m")
    ltf_interval = config.get("strategy_settings.timeframe_ltf", "3m")
    
    data = fetcher.prepare_data_for_backtest(
        symbol=args.symbol,
        htf_interval=htf_interval,
        ltf_interval=ltf_interval,
        start_date=args.start,
        end_date=args.end
    )
    
    if data['ltf'].empty:
        logger.error("Failed to fetch data. Check your date range and API connection.")
        return
    
    logger.info(f"Fetched {len(data['htf'])} HTF candles and {len(data['ltf'])} LTF candles\n")
    
    # Override config with CLI arguments if provided
    if args.tsl:
        logger.info(f"Overriding TSL to {args.tsl}%")
        config._data['risk_management']['trailing_stop_pct'] = args.tsl
    
    if args.target:
        logger.info(f"Overriding Target to {args.target}%")
        config._data['risk_management']['target_profit_pct'] = args.target
    
    # Initialize backtest engine
    engine = BacktestEngine(initial_capital=args.capital)
    
    # Run simulation
    logger.info("\nRunning simulation...\n")
    
    ltf_df = data['ltf'].copy()
    htf_df = data['htf'].copy()
    
    # Apply indicators
    ltf_df = engine.calculate_indicators(ltf_df, "LTF")
    htf_df = engine.calculate_indicators(htf_df, "HTF")
    
    # Simulate candle-by-candle
    for i in range(20, len(ltf_df)):  # Need at least 20 candles for indicators
        
        current_ltf = ltf_df.iloc[:i+1]
        current_time = current_ltf.iloc[-1]['timestamp']
        spot_price = current_ltf.iloc[-1]['close']
        
        # Get matching HTF data up to this point
        current_htf = htf_df[htf_df['timestamp'] <= current_time]
        
        if len(current_htf) < 20:
            continue
        
        # Check exit conditions for active position
        current_atr = current_ltf.iloc[-1].get('atr', 0) if 'atr' in current_ltf.columns else 0
        engine.check_exit_conditions(spot_price, current_time, current_atr, days_to_expiry=3)
        
        # Generate signal if no active position
        if not engine.active_position:
            signal = engine.strategy.generate_signal(current_htf, current_ltf)
            
            if signal and isinstance(signal, dict) and signal.get('action') == 'BUY':
                engine.enter_position(signal, spot_price, current_time, days_to_expiry=3)
        
        # Update equity curve
        engine.update_equity(spot_price, current_time, days_to_expiry=3)
    
    # Close any open position at end
    if engine.active_position:
        final_spot = ltf_df.iloc[-1]['close']
        final_time = ltf_df.iloc[-1]['timestamp']
        engine.exit_position(final_spot, final_time, "Backtest End", days_to_expiry=3)
    
    # Generate report
    logger.info("\nGenerating performance report...\n")
    stats = engine.get_statistics()
    
    reporter = ReportGenerator()
    reporter.generate_full_report(stats, engine.trades, engine.equity_curve)
    
    logger.info("\nBacktest complete!\n")

def main():
    parser = argparse.ArgumentParser(description="Run backtest for Options Trading Bot")
    
    parser.add_argument('--start', type=str, required=True, 
                       help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, required=True, 
                       help='End date (YYYY-MM-DD)')
    parser.add_argument('--symbol', type=str, default='NIFTY', 
                       help='Symbol to backtest (default: NIFTY)')
    parser.add_argument('--capital', type=float, default=100000, 
                       help='Initial capital (default: 100000)')
    parser.add_argument('--tsl', type=float, 
                       help='Override Trailing Stop Loss % (optional)')
    parser.add_argument('--target', type=float, 
                       help='Override Target Profit % (optional)')
    
    args = parser.parse_args()
    
    # Validate dates
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        print("Error: Dates must be in YYYY-MM-DD format")
        sys.exit(1)
    
    run_backtest(args)

if __name__ == "__main__":
    main()
