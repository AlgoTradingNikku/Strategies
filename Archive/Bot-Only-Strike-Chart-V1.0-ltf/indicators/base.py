"""
Base indicator interface for plugin system.

All indicators must inherit from BaseIndicator and implement the calculate() method.
This allows easy addition of new indicators (RSI, Supertrend, MACD) without touching core code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any
import pandas as pd


@dataclass
class IndicatorSignal:
    """
    Standardized output from any indicator.
    
    Attributes:
        trend: Current trend direction (1=Bullish, -1=Bearish, 0=Neutral)
        signal: Signal type (1=Fresh Buy, -1=Fresh Sell, 2=Pullback Buy, -2=Pullback Sell, 0=No Signal)
        strength: Confidence level 0.0-1.0 (1.0 = highly confident)
        metadata: Indicator-specific data (stop levels, ATR, trend series, etc.)
    """
    trend: int
    signal: int
    strength: float
    metadata: Dict[str, Any]
    
    def is_bullish(self) -> bool:
        """Check if current trend is bullish"""
        return self.trend == 1
    
    def is_bearish(self) -> bool:
        """Check if current trend is bearish"""
        return self.trend == -1
    
    def has_fresh_buy(self) -> bool:
        """Check if this is a fresh buy signal"""
        return self.signal == 1
    
    def has_fresh_sell(self) -> bool:
        """Check if this is a fresh sell signal"""
        return self.signal == -1
    
    def has_pullback_buy(self) -> bool:
        """Check if this is a pullback buy signal"""
        return self.signal == 2
    
    def has_pullback_sell(self) -> bool:
        """Check if this is a pullback sell signal"""
        return self.signal == -2


class BaseIndicator(ABC):
    """
    Abstract base class for all technical indicators.
    
    To create a new indicator:
    1. Inherit from this class
    2. Implement calculate() method
    3. Define required_params property
    4. Optionally override warmup_period
    5. Register in IndicatorRegistry
    
    Example:
        class MyIndicator(BaseIndicator):
            @property
            def required_params(self) -> list[str]:
                return ["period", "threshold"]
            
            def calculate(self, df: pd.DataFrame, use_ha: bool = True) -> IndicatorSignal:
                # Your indicator logic here
                return IndicatorSignal(trend=1, signal=1, strength=1.0, metadata={})
    """
    
    def __init__(self, params: dict):
        """
        Initialize indicator with parameters.
        
        Args:
            params: Dictionary of indicator parameters (e.g., {"sensitivity": 1.0, "period": 10})
        """
        self.params = params
        self.name = self.__class__.__name__
        self._validate_params()
    
    def _validate_params(self):
        """Validate that all required parameters are present"""
        for param in self.required_params:
            if param not in self.params:
                raise ValueError(
                    f"{self.name} requires parameter '{param}'. "
                    f"Available params: {list(self.params.keys())}"
                )
    
    @abstractmethod
    def calculate(self, df: pd.DataFrame, use_ha: bool = True) -> IndicatorSignal:
        """
        Calculate indicator on DataFrame and return standardized signal.
        
        Args:
            df: OHLC DataFrame with columns: Open, High, Low, Close, Volume
                and optional HA columns: HA_Open, HA_High, HA_Low, HA_Close
            use_ha: Whether to use Heikin Ashi values for calculation
            
        Returns:
            IndicatorSignal with trend, signal, strength, and metadata
            
        Raises:
            ValueError: If DataFrame doesn't have required columns
        """
        pass
    
    @property
    @abstractmethod
    def required_params(self) -> list[str]:
        """
        List of required parameter names for this indicator.
        
        Returns:
            List of parameter names that must be present in self.params
            
        Example:
            return ["sensitivity", "atr_period"]
        """
        pass
    
    @property
    def warmup_period(self) -> int:
        """
        Minimum number of bars needed before indicator produces valid signals.
        
        Override this in subclass if your indicator needs more warmup.
        
        Returns:
            Number of bars needed (default: 10)
        """
        return 10
    
    def __repr__(self) -> str:
        return f"{self.name}(params={self.params})"
