"""
Performance Report Generator

Creates detailed reports and exports for backtest results.
"""

import pandas as pd
import logging
from typing import Dict, List
from datetime import datetime
import os

class ReportGenerator:
    """Generates performance reports and exports backtest results"""
    
    def __init__(self, output_dir: str = "backtest/results"):
        self.output_dir = output_dir
        self.logger = logging.getLogger("ReportGenerator")
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
    
    def print_summary(self, stats: Dict, trades: List[Dict]):
        """Print a console summary of backtest results"""
        
        print("\n" + "="*70)
        print("  BACKTEST PERFORMANCE SUMMARY")
        print("="*70)
        
        # Get lot configuration
        from config import config
        lots_per_trade = config.get("position_sizing.lots_per_trade", 1)
        lot_size = 50  # Nifty standard
        qty_per_trade = lots_per_trade * lot_size
        
        print(f"\nPosition Sizing:")
        print(f"   Lots per Trade:     {lots_per_trade}")
        print(f"   Lot Size:           {lot_size} shares")
        print(f"   Quantity per Trade: {qty_per_trade} shares")
        
        print(f"\nCapital & Returns:")
        print(f"   Initial Capital:    Rs.{stats['initial_capital']:,.2f}")
        print(f"   Final Equity:       Rs.{stats['final_equity']:,.2f}")
        print(f"   Total P&L:          Rs.{stats['total_pnl']:+,.2f}")
        print(f"   P&L per Lot:        Rs.{stats['total_pnl']/max(lots_per_trade, 1):+,.2f}")
        print(f"   Total Return:       {stats['total_return']:+.2f}%")
        print(f"   Max Drawdown:       {stats['max_drawdown']:.2f}%")
        
        print(f"\nTrade Statistics:")
        print(f"   Total Trades:       {stats['total_trades']}")
        print(f"   Winning Trades:     {stats['winning_trades']} ({stats['win_rate']:.1f}%)")
        print(f"   Losing Trades:      {stats['losing_trades']}")
        print(f"   Avg Win:            Rs.{stats['avg_win']:+,.2f}")
        print(f"   Avg Loss:           Rs.{stats['avg_loss']:+,.2f}")
        print(f"   Profit Factor:      {stats['profit_factor']:.2f}")
        
        print(f"\nCosts:")
        print(f"   Total Brokerage:    Rs.{stats['total_brokerage']:,.2f}")
        print(f"   Brokerage per Trade: Rs.14.00 (Rs.7 x 2)")
        
        if trades:
            print(f"\nRecent Trades (Last 5):")
            print(f"{'Time':<20} {'Type':<4} {'Entry':<8} {'Exit':<8} {'P&L':<12} {'Reason':<20}")
            print("-" * 80)
            
            for trade in trades[-5:]:
                entry_time = trade['entry_time'].strftime("%Y-%m-%d %H:%M")
                pnl_str = f"Rs.{trade['pnl']:+,.2f}"
                print(f"{entry_time:<20} {trade['type']:<4} "
                      f"Rs.{trade['entry_premium']:<6.2f} Rs.{trade['exit_premium']:<6.2f} "
                      f"{pnl_str:<12} {trade['exit_reason']:<20}")
        
        print("\n" + "="*70 + "\n")
    
    def export_trades_to_csv(self, trades: List[Dict], filename: str = None):
        """Export all trades to a CSV file"""
        
        if not trades:
            self.logger.warning("No trades to export")
            return
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"trades_{timestamp}.csv"
        
        filepath = os.path.join(self.output_dir, filename)
        
        df = pd.DataFrame(trades)
        df.to_csv(filepath, index=False)
        
        self.logger.info(f"Trades exported to: {filepath}")
        return filepath
    
    def export_equity_curve(self, equity_curve: List[Dict], filename: str = None):
        """Export equity curve to CSV"""
        
        if not equity_curve:
            self.logger.warning("No equity data to export")
            return
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"equity_curve_{timestamp}.csv"
        
        filepath = os.path.join(self.output_dir, filename)
        
        df = pd.DataFrame(equity_curve)
        df.to_csv(filepath, index=False)
        
        self.logger.info(f"Equity curve exported to: {filepath}")
        return filepath
    
    def generate_full_report(self, stats: Dict, trades: List[Dict], equity_curve: List[Dict]):
        """
        Generate a complete backtest report with all exports.
        Returns dictionary with file paths.
        """
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Print console summary
        self.print_summary(stats, trades)
        
        # Export trades
        trades_file = self.export_trades_to_csv(trades, f"trades_{timestamp}.csv")
        
        # Export equity curve
        equity_file = self.export_equity_curve(equity_curve, f"equity_{timestamp}.csv")
        
        # Create summary text file
        summary_file = os.path.join(self.output_dir, f"summary_{timestamp}.txt")
        with open(summary_file, 'w') as f:
            f.write("BACKTEST PERFORMANCE SUMMARY\n")
            f.write("="*70 + "\n\n")
            f.write(f"Initial Capital:    Rs.{stats['initial_capital']:,.2f}\n")
            f.write(f"Final Equity:       Rs.{stats['final_equity']:,.2f}\n")
            f.write(f"Total P&L:          Rs.{stats['total_pnl']:+,.2f}\n")
            f.write(f"Total Return:       {stats['total_return']:+.2f}%\n")
            f.write(f"Max Drawdown:       {stats['max_drawdown']:.2f}%\n\n")
            f.write(f"Total Trades:       {stats['total_trades']}\n")
            f.write(f"Win Rate:           {stats['win_rate']:.1f}%\n")
            f.write(f"Profit Factor:      {stats['profit_factor']:.2f}\n")
            f.write(f"Total Brokerage:    Rs.{stats['total_brokerage']:,.2f}\n")
        
        self.logger.info(f"Summary exported to: {summary_file}")
        
        return {
            'trades_file': trades_file,
            'equity_file': equity_file,
            'summary_file': summary_file
        }
