import unittest
import pandas as pd
from unittest.mock import MagicMock, patch
from pullback_manager import PullbackManager
from config import config

class TestPullbackManager(unittest.TestCase):
    def setUp(self):
        self.pm = PullbackManager()
        
    @patch('config.config.get')
    def test_ema_touch(self, mock_get):
        # Mock Config: Enabled, Group with EMA_TOUCH
        mock_get.side_effect = lambda key, default=None: {
            "strategy_settings.pullback_strategy_settings": {
                "enabled": True,
                "master_operator": "OR",
                "groups": [
                    {
                        "name": "EMA Test",
                        "active": True,
                        "operator": "AND",
                        "triggers": ["EMA_TOUCH"]
                    }
                ],
                "definitions": {
                     "ema_touch": {"period": 9}
                }
            },
            "strategy_settings.pullback_strategy_settings.definitions.ema_touch": {"period": 9}
        }.get(key, default)

        # Mock DF
        df = pd.DataFrame({'ema_9': [100.0, 100.0]})
        
        # Test 1: Exact Touch (100.0) -> True
        self.assertTrue(self.pm.is_pullback_valid('CE', 100.0, df))
        
        # Test 2: Within 0.15% Tolerance (100.1) -> True
        self.assertTrue(self.pm.is_pullback_valid('CE', 100.1, df))
        
        # Test 3: Far Away (105.0) -> False
        self.assertFalse(self.pm.is_pullback_valid('CE', 105.0, df))

    @patch('config.config.get')
    def test_logic_and(self, mock_get):
        # Mock Config: AND Logic (EMA + RSI)
        mock_get.side_effect = lambda key, default=None: {
            "strategy_settings.pullback_strategy_settings": {
                "enabled": True,
                "groups": [
                    {
                        "active": True,
                        "operator": "AND",
                        "triggers": ["EMA_TOUCH", "RSI_DIP"]
                    }
                ],
                "definitions": {
                     "ema_touch": {"period": 9},
                     "rsi_dip": {"value": 40}
                }
            },
            "strategy_settings.pullback_strategy_settings.definitions.ema_touch": {"period": 9},
            "strategy_settings.pullback_strategy_settings.definitions.rsi_dip": {"value": 40}
        }.get(key, default)

        df = pd.DataFrame({
            'ema_9': [100.0, 100.0],
            'rsi_14': [30.0, 30.0] # RSI < 40 (Method logic: <= value)
        })
        
        # Case 1: EMA Good (100), RSI Good (30) -> True
        self.assertTrue(self.pm.is_pullback_valid('CE', 100.0, df))
        
        # Case 2: EMA Bad (105), RSI Good (30) -> False
        self.assertFalse(self.pm.is_pullback_valid('CE', 105.0, df))

    @patch('config.config.get')
    def test_recovery_strength(self, mock_get):
        # Mock Config: Recovery Mode with RSI > 55
        mock_get.side_effect = lambda key, default=None: {
            "strategy_settings.renter_trend_mode": {
                "enabled": True,
                "master_operator": "AND",
                "groups": [
                    {
                        "active": True,
                        "operator": "AND",
                        "triggers": ["RSI_MIN"]
                    }
                ],
                "definitions": {
                     "rsi_min": {"value": 55}
                }
            },
            "strategy_settings.renter_trend_mode.definitions.rsi_min": {"value": 55}
        }.get(key, default)

        # Case 1: RSI 60 (Strong) -> True
        df_strong = pd.DataFrame({'rsi_14': [60.0, 60.0]})
        self.assertTrue(self.pm.is_recovery_valid('CE', 100.0, df_strong))
        
        # Case 2: RSI 50 (Weak) -> False
        df_weak = pd.DataFrame({'rsi_14': [50.0, 50.0]})
        self.assertFalse(self.pm.is_recovery_valid('CE', 100.0, df_weak))

if __name__ == '__main__':
    unittest.main()
