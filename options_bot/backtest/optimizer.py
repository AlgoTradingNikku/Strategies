"""
Parameter Optimizer for Options Trading Bot

Automatically tests different indicator parameters to find optimal settings.
Uses grid search to explore parameter space and ranks results by performance.
"""

import logging
from typing import Dict, List, Tuple
import pandas as pd
from datetime import datetime
import os

from backtest.backtest_engine import BacktestEngine
from backtest.data_fetcher import DataFetcher
from config import config

class ParameterOptimizer:
    """
    Optimizes strategy parameters using grid search.
    Tests multiple combinations and ranks by performance metrics.
    """
    
    def __init__(self, api_client, output_dir: str = "backtest/results"):
        self.api = api_client
        self.output_dir = output_dir
        self.logger = logging.getLogger("ParameterOptimizer")
        self.results = []
        
    def optimize_utbot(self, 
                      symbol: str,
                      start_date: str,
                      end_date: str,
                      key_values: List[float] = None,
                      atr_values: List[int] = None) -> pd.DataFrame:
        """
        Optimize UTBot parameters (key and ATR period).
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY)
            start_date: Start date for backtest
            end_date: End date for backtest
            key_values: List of key multipliers to test
            atr_values: List of ATR periods to test
            
        Returns:
            DataFrame with results sorted by performance
        """
        
        # Default parameter ranges
        if key_values is None:
            key_values = [0.5, 1.0, 1.5, 2.0, 2.5]
        
        if atr_values is None:
            atr_values = [8, 10, 12, 14, 16]
        
        total_tests = len(key_values) * len(atr_values)
        self.logger.info(f"\nStarting UTBot Optimization")
        self.logger.info(f"Testing {total_tests} parameter combinations...")
        self.logger.info(f"Key values: {key_values}")
        self.logger.info(f"ATR periods: {atr_values}\n")
        
        # Fetch data once (shared across all tests)
        fetcher = DataFetcher(self.api)
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
            self.logger.error("Failed to fetch data for optimization")
            return pd.DataFrame()
        
        # Grid search
        test_num = 0
        for key in key_values:
            for atr_period in atr_values:
                test_num += 1
                self.logger.info(f"[{test_num}/{total_tests}] Testing Key={key}, ATR={atr_period}")
                
                # Temporarily override config
                original_key = config.get("indicators.utbot_key")
                original_atr = config.get("indicators.utbot_atr")
                
                config._data['indicators']['utbot_key'] = key
                config._data['indicators']['utbot_atr'] = atr_period
                
                # Run backtest
                stats = self._run_single_backtest(data, symbol)
                
                # Restore original config
                config._data['indicators']['utbot_key'] = original_key
                config._data['indicators']['utbot_atr'] = original_atr
                
                # Store results
                result = {
                    'utbot_key': key,
                    'utbot_atr': atr_period,
                    'total_return': stats['total_return'],
                    'total_pnl': stats['total_pnl'],
                    'total_trades': stats['total_trades'],
                    'win_rate': stats['win_rate'],
                    'profit_factor': stats['profit_factor'],
                    'max_drawdown': stats['max_drawdown'],
                    'avg_win': stats['avg_win'],
                    'avg_loss': stats['avg_loss']
                }
                
                self.results.append(result)
                self.logger.info(f"   Return: {stats['total_return']:+.2f}% | Win Rate: {stats['win_rate']:.1f}% | Trades: {stats['total_trades']}\n")
        
        # Convert to DataFrame and sort by Total PnL (Highest Profit First)
        df = pd.DataFrame(self.results)
        df = df.sort_values('total_pnl', ascending=False)
        
        return df
    
    def _run_single_backtest(self, data: Dict, symbol: str) -> Dict:
        """Run a single backtest with current config settings"""
        
        engine = BacktestEngine(initial_capital=100000)
        
        ltf_df = data['ltf'].copy()
        htf_df = data['htf'].copy()
        
        # Apply indicators with current config
        ltf_df = engine.calculate_indicators(ltf_df, "LTF")
        htf_df = engine.calculate_indicators(htf_df, "HTF")
        
        # Simulate
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
        
        # Close final position
        if engine.active_position:
            final_spot = ltf_df.iloc[-1]['close']
            final_time = ltf_df.iloc[-1]['timestamp']
            engine.exit_position(final_spot, final_time, "Backtest End", days_to_expiry=3)
        
        return engine.get_statistics()
    
    def export_results(self, df: pd.DataFrame, filename: str = None):
        """Export optimization results to CSV"""
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"optimization_results_{timestamp}.csv"
        
        filepath = os.path.join(self.output_dir, filename)
        df.to_csv(filepath, index=False)
        
        self.logger.info(f"Optimization results exported to: {filepath}")
        return filepath
    
    def print_top_results(self, df: pd.DataFrame, top_n: int = 10):
        """Print top N parameter combinations"""
        
        from config import config
        lots_per_trade = config.get("position_sizing.lots_per_trade", 1)
        
        self.logger.info("\n" + "="*110)
        self.logger.info("  OPTIMIZATION RESULTS - TOP PERFORMERS")
        self.logger.info("="*110)
        
        self.logger.info(f"\n{'Rank':<6} {'Key':<8} {'ATR':<8} {'Net Profit':<15} {'Return %':<12} {'Win %':<10} {'Profit Factor':<15} {'Trades':<10}")
        self.logger.info("-" * 110)
        
        for idx, row in df.head(top_n).iterrows():
            rank = idx + 1 if isinstance(idx, int) else list(df.index).index(idx) + 1
            net_profit_per_lot = row['total_pnl'] / max(lots_per_trade, 1)
            self.logger.info(f"{rank:<6} {row['utbot_key']:<8.1f} {row['utbot_atr']:<8.0f} "
                  f"Rs.{net_profit_per_lot:+<13,.2f} {row['total_return']:+<12.2f} {row['win_rate']:<10.1f} "
                  f"{row['profit_factor']:<15.2f} {row['total_trades']:<10.0f}")
        
        self.logger.info("\n" + "="*110)
        
        # Best combination
        best = df.iloc[0]
        best_net_profit = best['total_pnl'] / max(lots_per_trade, 1)
        
        self.logger.info(f"\nRECOMMENDED SETTINGS:")
        self.logger.info(f"  utbot_key: {best['utbot_key']}")
        self.logger.info(f"  utbot_atr: {int(best['utbot_atr'])}")
        self.logger.info(f"\nExpected Performance (Per {lots_per_trade} Lot):")
        self.logger.info(f"  Net Profit: Rs.{best_net_profit:+,.2f}")
        self.logger.info(f"  Return: {best['total_return']:+.2f}%")
        self.logger.info(f"  Win Rate: {best['win_rate']:.1f}%")
        self.logger.info(f"  Profit Factor: {best['profit_factor']:.2f}")
        self.logger.info(f"  Max Drawdown: {best['max_drawdown']:.2f}%")
        self.logger.info("\n")
