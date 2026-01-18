"""
Shared pytest fixtures and utilities for PureOptionsBot tests.

This module provides common test fixtures, mock objects, and helper functions
used across multiple test files.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock
from typing import Dict, List

# Import bot components
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_machine import Trade, TradeState
from indicators.base import IndicatorSignal


# ============================================================
# CONFIGURATION FIXTURES
# ============================================================

@pytest.fixture
def sample_config():
    """Sample bot configuration for testing"""
    return {
        "strategy_name": "PureOptionsBot",
        "live_trade": False,
        "index_query": "NIFTY",
        "index_exchange": "NSE_INDEX",
        "signal_source": "OPTION",
        "trend_tf": "15m",
        "execution_tf": "3m",
        
        "strike_selection": {
            "mode": "MANUAL",
            "manual_strikes": ["NIFTY20JAN2625700CE", "NIFTY20JAN2625700PE"],
            "max_option_price": 100
        },
        
        "strategy": {
            "smart_momentum": {
                "enabled": True,
                "entry_mode": "ADVANCED",
                "master_trend": {
                    "enabled": True,
                    "ema_fast": 9,
                    "ema_mid": 21,
                    "ema_slow": 50,
                    "adx_period": 14,
                    "rsi_period": 14,
                    "adx_threshold": 20,
                    "rsi_bull_min": 55,
                    "rsi_bear_max": 45
                },
                "explosive_trend": {
                    "enabled": True,
                    "adx_min": 30,
                    "min_body_pct": 0.005,
                    "min_body_ratio": 0.6,
                    "min_close_pos": 0.75
                },
                "wait_logic": {
                    "max_retrace_pct": 0.30
                },
                "entry_confirmation": {
                    "use_atr_price_cap": True,
                    "atr_multiplier": 1.5,
                    "vwap_max_buffer": 1.015,
                    "check_spread": False,
                    "max_spread_pct": 0.003,
                    "ema_fast": 9,
                    "ema_slow": 21,
                    "volume_multiplier": 1.2,
                    "check_upper_wick": True,
                    "check_delta": False
                },
                "wait_candles": 1
            }
        },
        
        "trading_hours": {
            "enabled": True,
            "start_time": "09:25",
            "end_time": "15:00",
            "sq_off_time": "15:20",
            "avoid_lunch": False
        },
        
        "execution": {
            "order_type": "SMART_LIMIT",
            "limit_offset_pct": 0.0,
            "max_spread_pct": 0.3,
            "order_timeout_sec": 8,
            "enable_bot_auto_sell": True
        },
        
        "system": {
            "loop_intervals": {
                "scanner": 5,
                "risk_monitor": 1,
                "position_sync": 10,
                "config_monitor": 2
            },
            "data_limits": {
                "trend_bars": 100,
                "exec_bars": 50
            },
            "cooldowns": {
                "error_sec": 60
            }
        },
        
        "risk": {
            "max_loss_per_trade": 500,
            "max_daily_loss": 2000,
            "tsl_mode": "PERCENT",
            "tsl_percent": 20,
            "target_percent": 50
        },
        
        "max_positions": 4,
        "lots": 1,
        "product_type": "MIS",
        "api_key": "test_key",
        "api_host": "http://127.0.0.1:5000",
        "use_websocket": True,
        "use_threading": True
    }


# ============================================================
# DATA FIXTURES
# ============================================================

@pytest.fixture
def sample_ohlc_data():
    """Generate sample OHLC candlestick data"""
    np.random.seed(42)
    n = 100
    
    # Trending price data
    base_price = 25000
    trend = np.linspace(0, 200, n)  # Upward trend
    noise = np.random.randn(n) * 30
    close = base_price + trend + noise
    
    # OHLC relationships
    high = close + np.abs(np.random.randn(n) * 20)
    low = close - np.abs(np.random.randn(n) * 20)
    open_price = close + np.random.randn(n) * 10
    
    # Create DataFrame with proper index
    dates = pd.date_range(end=datetime.now(), periods=n, freq='3T')
    
    df = pd.DataFrame({
        'Open': open_price,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': np.random.randint(10000, 100000, n)
    }, index=dates)
    
    # Add Heikin Ashi columns
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    return df


@pytest.fixture
def bullish_ohlc_data():
    """Generate strongly bullish OHLC data"""
    n = 50
    dates = pd.date_range(end=datetime.now(), periods=n, freq='3T')
    
    # Strong uptrend
    close = 25000 + np.linspace(0, 500, n) + np.random.randn(n) * 10
    high = close + np.abs(np.random.randn(n) * 15)
    low = close - np.abs(np.random.randn(n) * 5)  # Small lower wicks
    open_price = (close + low) / 2  # Opens near lows
    
    df = pd.DataFrame({
        'Open': open_price,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': np.random.randint(50000, 150000, n)
    }, index=dates)
    
    # Add HA
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    return df


@pytest.fixture
def bearish_ohlc_data():
    """Generate strongly bearish OHLC data"""
    n = 50
    dates = pd.date_range(end=datetime.now(), periods=n, freq='3T')
    
    # Strong downtrend
    close = 25000 - np.linspace(0, 500, n) + np.random.randn(n) * 10
    high = close + np.abs(np.random.randn(n) * 5)  # Small upper wicks
    low = close - np.abs(np.random.randn(n) * 15)
    open_price = (close + high) / 2  # Opens near highs
    
    df = pd.DataFrame({
        'Open': open_price,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': np.random.randint(50000, 150000, n)
    }, index=dates)
    
    # Add HA
    df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    ha_open = [df['Open'].iloc[0]]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
    df['HA_Open'] = ha_open
    df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    return df


# ============================================================
# TRADE FIXTURES
# ============================================================

@pytest.fixture
def sample_trade():
    """Create a sample trade in POSITION state"""
    return Trade(
        symbol="NIFTY20JAN2625700CE",
        state=TradeState.POSITION,
        side="CALL",
        entry_price=50.0,
        current_price=52.0,
        highest_price=52.0,
        lowest_price=50.0,
        quantity=50,
        entry_time=datetime.now(),
        atr=2.5,
        tsl_level=48.0,
        last_stage="TRAILING"
    )


@pytest.fixture
def profitable_trade():
    """Create a profitable trade"""
    return Trade(
        symbol="NIFTY20JAN2625700CE",
        state=TradeState.POSITION,
        side="CALL",
        entry_price=50.0,
        current_price=60.0,
        highest_price=60.0,
        lowest_price=50.0,
        quantity=50,
        entry_time=datetime.now() - timedelta(minutes=15),
        atr=2.5,
        tsl_level=55.0,
        last_stage="G2"
    )


@pytest.fixture
def losing_trade():
    """Create a losing trade"""
    return Trade(
        symbol="NIFTY20JAN2625700PE",
        state=TradeState.POSITION,
        side="PUT",
        entry_price=50.0,
        current_price=42.0,
        highest_price=50.0,
        lowest_price=42.0,
        quantity=50,
        entry_time=datetime.now() - timedelta(minutes=10),
        atr=2.5,
        tsl_level=45.0,
        last_stage="BE"
    )


# ============================================================
# MOCK API CLIENT
# ============================================================

@pytest.fixture
def mock_api_client():
    """Create a mock OpenAlgo API client"""
    client = Mock()
    
    # Mock methods
    client.placeorder = AsyncMock(return_value={
        'status': 'success',
        'orderid': 'TEST123456',
        'message': 'Order placed successfully'
    })
    
    client.orderbook = Mock(return_value={
        'status': 'success',
        'data': []
    })
    
    client.positionbook = Mock(return_value={
        'status': 'success',
        'data': []
    })
    
    client.quotes = Mock(return_value={
        'status': 'success',
        'data': {
            'ltp': 52.5,
            'bid': 52.0,
            'ask': 53.0,
            'volume': 125000
        }
    })
    
    client.historical = Mock(return_value={
        'status': 'success',
        'data': []
    })
    
    # WebSocket mocks
    client.connect = Mock()
    client.disconnect = Mock()
    client.subscribe_ltp = Mock()
    client.ws = Mock()
    client.ws.sock = Mock()
    
    return client


@pytest.fixture
def mock_data_provider():
    """Create a mock DataProvider"""
    provider = AsyncMock()
    
    # Mock methods
    provider.fetch_history = AsyncMock()
    provider.get_quote = AsyncMock(return_value={
        'ltp': 52.5,
        'bid': 52.0,
        'ask': 53.0,
        'volume': 125000
    })
    provider.get_live_price = AsyncMock(return_value=52.5)
    provider.get_lot_size = AsyncMock(return_value=50)
    provider.close = AsyncMock()
    
    return provider


# ============================================================
# INDICATOR SIGNAL FIXTURES
# ============================================================

@pytest.fixture
def bullish_signal():
    """Create a bullish indicator signal"""
    return IndicatorSignal(
        trend=1,
        signal=1,
        strength=1.0,
        metadata={
            'stop_level': 24950.0,
            'atr': 50.0,
            'trend_series': [1] * 10
        }
    )


@pytest.fixture
def bearish_signal():
    """Create a bearish indicator signal"""
    return IndicatorSignal(
        trend=-1,
        signal=-1,
        strength=1.0,
        metadata={
            'stop_level': 25050.0,
            'atr': 50.0,
            'trend_series': [-1] * 10
        }
    )


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def create_mock_order_response(success=True, order_id="TEST123", filled_price=50.0):
    """Helper to create mock order responses"""
    if success:
        response = Mock()
        response.success = True
        response.order_id = order_id
        response.filled_price = filled_price
        response.message = "Order placed successfully"
        return response
    else:
        response = Mock()
        response.success = False
        response.order_id = None
        response.filled_price = None
        response.message = "Order failed"
        return response


def assert_trade_state(trade: Trade, expected_state: TradeState):
    """Helper to assert trade state"""
    assert trade.state == expected_state, f"Expected state {expected_state.name}, got {trade.state.name}"


def create_option_chain_data(strikes: List[int], base_price: float = 50.0):
    """
    Helper to create mock option chain data.
    
    Args:
        strikes: List of strike prices
        base_price: Base price for options
        
    Returns:
        Dictionary of strike -> price mapping
    """
    chain = {}
    for strike in strikes:
        # Simple pricing: closer to ATM = higher premium
        distance = abs(strike - strikes[len(strikes)//2])
        price = base_price * (1 - distance * 0.01)
        chain[strike] = max(price, 1.0)  # Minimum 1.0
    return chain
