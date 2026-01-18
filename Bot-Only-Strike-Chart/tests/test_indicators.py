"""
Test suite for indicator plugins.

Run with: pytest tests/test_indicators.py -v
"""

import pytest
import pandas as pd
import numpy as np
from indicators.base import BaseIndicator, IndicatorSignal
from indicators.utbot import UTBotIndicator
from indicators.registry import IndicatorRegistry


class TestIndicatorBase:
    """Test BaseIndicator interface"""
    
    def test_indicator_signal_helpers(self):
        """Test IndicatorSignal helper methods"""
        # Bullish signal
        signal = IndicatorSignal(trend=1, signal=1, strength=1.0, metadata={})
        assert signal.is_bullish()
        assert not signal.is_bearish()
        assert signal.has_fresh_buy()
        
        # Bearish signal
        signal = IndicatorSignal(trend=-1, signal=-1, strength=1.0, metadata={})
        assert signal.is_bearish()
        assert not signal.is_bullish()
        assert signal.has_fresh_sell()
        
        # Pullback signals
        signal = IndicatorSignal(trend=1, signal=2, strength=1.0, metadata={})
        assert signal.has_pullback_buy()
        
        signal = IndicatorSignal(trend=-1, signal=-2, strength=1.0, metadata={})
        assert signal.has_pullback_sell()


class TestUTBotIndicator:
    """Test UTBot indicator plugin"""
    
    @pytest.fixture
    def sample_data(self):
        """Create sample OHLC data for testing"""
        np.random.seed(42)
        n = 50
        
        # Create realistic OHLC data
        close = 25000 + np.cumsum(np.random.randn(n) * 10)
        high = close + np.random.rand(n) * 20
        low = close - np.random.rand(n) * 20
        open_price = close + np.random.randn(n) * 5
        
        df = pd.DataFrame({
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.random.randint(1000, 10000, n)
        })
        
        # Add Heikin Ashi columns
        df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
        ha_open = [df['Open'].iloc[0]]
        for i in range(1, len(df)):
            ha_open.append((ha_open[i-1] + df['HA_Close'].iloc[i-1]) / 2)
        df['HA_Open'] = ha_open
        df['HA_High'] = df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
        df['HA_Low'] = df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
        
        return df
    
    def test_utbot_initialization(self):
        """Test UTBot can be initialized with valid params"""
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = UTBotIndicator(params)
        
        assert indicator.name == "UTBotIndicator"
        assert indicator.params == params
        assert indicator.warmup_period == 15  # atr_period(10) + 5
    
    def test_utbot_requires_params(self):
        """Test UTBot validates required parameters"""
        # Missing sensitivity
        with pytest.raises(ValueError, match="sensitivity"):
            UTBotIndicator({"atr_period": 10})
        
        # Missing atr_period
        with pytest.raises(ValueError, match="atr_period"):
            UTBotIndicator({"sensitivity": 1.0})
    
    def test_utbot_calculate(self, sample_data):
        """Test UTBot calculate method"""
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = UTBotIndicator(params)
        
        # Calculate indicator
        signal = indicator.calculate(sample_data, use_ha=True)
        
        # Verify return type
        assert isinstance(signal, IndicatorSignal)
        
        # Verify trend is valid
        assert signal.trend in [-1, 1]
        
        # Verify signal is valid
        assert signal.signal in [-2, -1, 0, 1, 2]
        
        # Verify strength
        assert signal.strength == 1.0  # UTBot is always confident
        
        # Verify metadata
        assert "stop_level" in signal.metadata
        assert "atr" in signal.metadata
        assert "trend_series" in signal.metadata
        
        # Verify stop level is reasonable
        assert signal.metadata["stop_level"] > 0
        assert signal.metadata["atr"] > 0
    
    def test_utbot_insufficient_data(self):
        """Test UTBot handles insufficient data"""
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = UTBotIndicator(params)
        
        # Create DataFrame with only 5 bars (needs 15)
        small_df = pd.DataFrame({
            "Open": [100, 101, 102, 103, 104],
            "High": [102, 103, 104, 105, 106],
            "Low": [99, 100, 101, 102, 103],
            "Close": [101, 102, 103, 104, 105],
            "HA_Open": [100, 101, 102, 103, 104],
            "HA_High": [102, 103, 104, 105, 106],
            "HA_Low": [99, 100, 101, 102, 103],
            "HA_Close": [101, 102, 103, 104, 105],
        })
        
        with pytest.raises(ValueError, match="Insufficient data"):
            indicator.calculate(small_df, use_ha=True)
    
    def test_utbot_trend_age(self, sample_data):
        """Test trend age calculation"""
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = UTBotIndicator(params)
        
        signal = indicator.calculate(sample_data, use_ha=True)
        trend_age = indicator.get_trend_age(signal)
        
        # Trend age should be non-negative
        assert trend_age >= 0
        assert isinstance(trend_age, int)


class TestIndicatorRegistry:
    """Test indicator registry/factory"""
    
    def test_create_utbot(self):
        """Test creating UTBot via registry"""
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = IndicatorRegistry.create("utbot", params)
        
        assert isinstance(indicator, UTBotIndicator)
        assert indicator.params == params
    
    def test_unknown_indicator(self):
        """Test error on unknown indicator"""
        with pytest.raises(ValueError, match="Unknown indicator"):
            IndicatorRegistry.create("nonexistent", {})
    
    def test_list_indicators(self):
        """Test listing available indicators"""
        indicators = IndicatorRegistry.list_indicators()
        
        assert "utbot" in indicators
        assert isinstance(indicators, list)
    
    def test_get_indicator_info(self):
        """Test getting indicator metadata"""
        info = IndicatorRegistry.get_indicator_info("utbot")
        
        assert info["name"] == "utbot"
        assert info["class"] == "UTBotIndicator"
        assert "required_params" in info or "module" in info


if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v", "--tb=short"])
