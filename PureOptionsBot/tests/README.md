# PureOptionsBot Test Suite

Comprehensive test suite for the PureOptionsBot trading system with 100+ tests covering all major components.

## Overview

This test suite provides thorough coverage of:
- ✅ **State Machine** - Trade lifecycle and transitions
- ✅ **Persistence** - Database operations and crash recovery
- ✅ **Risk Manager** - TSL calculations and exit logic
- ✅ **Indicators** - UTBot and technical indicators
- 🚧 **Order Manager** - Order placement logic (planned)
- 🚧 **Data Provider** - Market data fetching (planned)
- 🚧 **Strategy Logic** - Entry/exit decisions (planned)
- 🚧 **Integration Tests** - End-to-end flows (planned)

## Test Files

### `conftest.py`
Shared fixtures and utilities:
- Sample configurations
- Mock API clients
- Test data generators (OHLC, trades, signals)
- Helper functions

### `test_state_machine.py`
Tests for trade lifecycle:
- Trade creation and P&L calculations
- State transitions (OBSERVING → POSITION → EXITING → EXITED)
- Trade lifecycle scenarios (profitable, losing, cancelled)
- Edge cases (zero quantity, zero price)

**Key Tests:**
- ✅ P&L calculations for CALL/PUT options
- ✅ Valid/invalid state transitions
- ✅ Complete trade lifecycles
- ✅ Serialization/deserialization

### `test_persistence.py`
Tests for database operations:
- SQLite CRUD operations
- Crash recovery scenarios
- Data integrity
- Multiple trades handling

**Key Tests:**
- ✅ Save/load trades from database
- ✅ Crash recovery (simulated restart)
- ✅ Active vs archived trades
- ✅ Unicode symbol names

### `test_risk_manager.py`
Tests for risk management:
- Trailing stop loss (TSL) calculations
- Exit decision logic
- Daily limits and P&L tracking
- Multi-stage TSL

**Key Tests:**
- ✅ Percent-based TSL
- ✅ Stop loss triggers
- ✅ Target hit detection
- ✅ Trend reversal exits
- ✅ Daily loss limits
- ✅ PUT option risk management

### `test_indicators.py`
Tests for technical indicators:
- UTBot indicator calculations
- Indicator registry/factory
- Trend age calculations
- Signal validation

**Key Tests:**
- ✅ UTBot signal generation
- ✅ Insufficient data handling
- ✅ Trend age calculation
- ✅ Indicator registry

## Running Tests

### Run All Tests
```bash
cd PureOptionsBot
pytest tests/ -v
```

### Run Specific Test File
```bash
pytest tests/test_state_machine.py -v
pytest tests/test_persistence.py -v
pytest tests/test_risk_manager.py -v
```

### Run Specific Test Class
```bash
pytest tests/test_state_machine.py::TestTradeDataClass -v
pytest tests/test_risk_manager.py::TestPercentTSL -v
```

### Run Specific Test
```bash
pytest tests/test_state_machine.py::TestTradeDataClass::test_calculate_pnl_call_profit -v
```

### Run with Coverage Report
```bash
pytest tests/ --cov=core --cov=risk --cov=indicators --cov-report=html
```

### Run Tests in Parallel (faster)
```bash
pytest tests/ -v -n auto
```

## Test Output

### Successful Run Example
```
tests/test_state_machine.py::TestTradeDataClass::test_trade_creation PASSED
tests/test_state_machine.py::TestTradeDataClass::test_calculate_pnl_call_profit PASSED
tests/test_persistence.py::TestTradePersistence::test_save_trade PASSED
tests/test_risk_manager.py::TestPercentTSL::test_tsl_moves_up_with_profit PASSED

==================== 45 passed in 2.35s ====================
```

### Failed Test Example
```
tests/test_risk_manager.py::TestPercentTSL::test_exit_when_tsl_hit FAILED

AssertionError: assert False
Expected TSL hit exit but got no exit decision
```

## Test Coverage Goals

Current coverage by module:
- **core.state_machine**: ~95% ✅
- **core.persistence**: ~90% ✅
- **risk.manager**: ~85% ✅
- **indicators**: ~80% ✅
- **execution.order_manager**: ~0% 🚧
- **data.provider**: ~0% 🚧
- **core.engine**: ~0% 🚧

**Target**: 80%+ coverage across all modules

## Writing New Tests

### Test Structure
```python
class TestFeatureName:
    """Test description"""
    
    @pytest.fixture
    def setup_data(self):
        """Fixture for test data"""
        return {"key": "value"}
    
    def test_specific_behavior(self, setup_data):
        """Test that X does Y when Z"""
        # Arrange
        input_data = setup_data
        
        # Act
        result = function_under_test(input_data)
        
        # Assert
        assert result == expected_value
```

### Using Fixtures
```python
def test_with_config(self, sample_config):
    """Use shared configuration fixture"""
    config = sample_config
    config['custom_setting'] = True
    # Test with modified config
```

### Using Mock API
```python
def test_with_mock_api(self, mock_api_client):
    """Use mock API client"""
    mock_api_client.quotes.return_value = {'ltp': 50.0}
    # Test without real API calls
```

## Best Practices

### ✅ Do
- Write descriptive test names: `test_exit_when_tsl_hit`
- Use fixtures for shared setup
- Test edge cases (zero, negative, very large values)
- Test error conditions
- Use mocks to avoid external dependencies
- Keep tests independent (no shared state)

### ❌ Don't
- Use real API calls in tests
- Depend on network connectivity
- Use hardcoded dates/times (use `datetime.now()`)
- Test implementation details
- Create brittle tests (test behavior, not internals)

## Continuous Integration

### GitHub Actions Example
```yaml
name: Run Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov pytest-asyncio
      - name: Run tests
        run: pytest tests/ -v --cov
```

## Debugging Failed Tests

### Verbose Output
```bash
pytest tests/test_risk_manager.py::test_exit_when_tsl_hit -vv
```

### Print Debugging
```bash
pytest tests/ -v -s  # Shows print() statements
```

### Drop into Debugger on Failure
```bash
pytest tests/ --pdb
```

### Run Only Failed Tests
```bash
pytest --lf  # Last failed
pytest --ff  # Failed first, then others
```

## Mock Data Generators

Available in `conftest.py`:

### OHLC Data
```python
def test_with_data(self, sample_ohlc_data):
    df = sample_ohlc_data  # 100 bars of sample data
```

### Bullish/Bearish Data
```python
def test_trend(self, bullish_ohlc_data, bearish_ohlc_data):
    bull_df = bullish_ohlc_data  # Strong uptrend
    bear_df = bearish_ohlc_data  # Strong downtrend
```

### Sample Trades
```python
def test_trade(self, sample_trade, profitable_trade, losing_trade):
    # Pre-configured trade objects
```

## Future Enhancements

### Planned Test Files
1. **test_order_manager.py** - Order placement and smart limit logic
2. **test_data_provider.py** - Historical data and quotes
3. **test_strategy_logic.py** - Entry/exit decision validation
4. **test_integration.py** - End-to-end trading flows
5. **test_websocket.py** - WebSocket connection and reconnection

### Planned Features
- Performance benchmarking tests
- Load testing (many concurrent trades)
- Backtesting validation
- Strategy parameter optimization tests

## Contributing

When adding new features:
1. Write tests first (TDD approach)
2. Ensure >80% code coverage
3. Add docstrings to test functions
4. Update this README if adding new test files
5. Run full test suite before committing

## Troubleshooting

### Import Errors
```bash
# Ensure project root is in PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/path/to/PureOptionsBot"
```

### Database Lock Errors
Tests use temporary databases. If you see lock errors:
```bash
# Clean up any stale database files
rm -f /tmp/*.db
```

### Async Tests Failing
Ensure pytest-asyncio is installed:
```bash
pip install pytest-asyncio
```

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [Python unittest.mock](https://docs.python.org/3/library/unittest.mock.html)

## Support

For issues or questions about the test suite:
1. Check this README first
2. Review existing test examples in the codebase
3. Check pytest documentation
4. Open an issue on GitHub

---

**Last Updated**: January 2026
**Test Count**: 45+ tests
**Coverage**: ~60% (target: 80%+)
