# Backtesting Module - README

## Overview
This backtesting module allows you to test your trading strategy on historical data without risking real capital.

## Quick Start

### Run a Basic Backtest
```bash
python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05
```

### Test Different Settings
```bash
# Test with 12% TSL and 60% target
python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05 --tsl 12 --target 60

# Test with different capital
python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05 --capital 200000

# Test BankNifty
python backtest/run_backtest.py --start 2025-12-01 --end 2026-01-05 --symbol BANKNIFTY
```

## Command Line Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--start` | Yes | - | Start date (YYYY-MM-DD) |
| `--end` | Yes | - | End date (YYYY-MM-DD) |
| `--symbol` | No | NIFTY | Symbol to backtest |
| `--capital` | No | 100000 | Initial capital |
| `--tsl` | No | config | Override Trailing Stop Loss % |
| `--target` | No | config | Override Target Profit % |

## Output Files

All results are saved in `backtest/results/`:

- **trades_TIMESTAMP.csv**: Detailed log of every trade
- **equity_TIMESTAMP.csv**: Equity curve over time
- **summary_TIMESTAMP.txt**: Performance summary
- **backtest.log**: Full execution log

## Understanding the Results

### Key Metrics

- **Total Return**: Overall profit/loss as a percentage
- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Ratio of gross profit to gross loss
- **Max Drawdown**: Worst peak-to-trough equity decline
- **Avg Win/Loss**: Average P&L for winning and losing trades

### Trade Log Columns

- `entry_time`, `exit_time`: When the trade was entered/exited
- `type`: CE (Call) or PE (Put)
- `strike`: Option strike price
- `entry_premium`, `exit_premium`: Option prices
- `pnl`: Net profit/loss after brokerage
- `exit_reason`: Why the trade was closed (TSL, Target, etc.)

## Important Notes

### Options Pricing
Since historical options data is not available, the backtester uses a **Black-Scholes approximation**. This means:
- Entry/exit prices are estimated, not actual market prices
- Results will be close but not 100% accurate
- Win rate and signal quality are still highly useful

### Realistic Assumptions
- Brokerage: ₹7 per order (same as live trading)
- Lot Size: 50 for Nifty, 15 for BankNifty
- Default IV: 18% (adjustable in `option_pricer.py`)

### Strategy Reuse
The backtest uses the **exact same** code as your live bot:
- Same `StrategyEngine`
- Same indicators (UTBot, EMA, RSI)
- Same risk management (TSL, targets, profit lock)

This ensures that backtest results reflect your actual trading logic.

## Tips for Better Backtesting

1. **Test Multiple Periods**: Don't just test one month. Try different market conditions.
2. **Optimize Settings**: Use `--tsl` and `--target` to find the best parameters.
3. **Compare Symbols**: Test both NIFTY and BANKNIFTY to see which works better.
4. **Check Drawdown**: A strategy with high returns but 50% drawdown is risky.
5. **Validate with Live Data**: Compare backtest results with your actual trades.

## Future Enhancements

Planned features:
- Walk-forward optimization
- Monte Carlo simulation
- Equity curve charting
- Parameter sensitivity analysis
- Support for intraday expiry trading
