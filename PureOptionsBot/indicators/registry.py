"""
Indicator Registry - Factory for creating indicators by name.

This allows creating indicators dynamically from config without hardcoding.
To add a new indicator, just register it here and use it in config.yaml.
"""

from typing import Type, Dict
from .base import BaseIndicator
from .utbot import UTBotIndicator
from .technical import TechnicalIndicator


class IndicatorRegistry:
    """
    Factory for creating indicators by name.
    
    Example:
        # Create UTBot from config
        params = {"sensitivity": 1.0, "atr_period": 10}
        indicator = IndicatorRegistry.create("utbot", params)
        
        # Use it
        signal = indicator.calculate(df, use_ha=True)
    
    To add a new indicator:
        1. Create the indicator class (e.g., indicators/rsi.py)
        2. Register it in _registry dict below
        3. Use it in config.yaml with type: "rsi"
    """
    
    # Registry of available indicators
    _registry: Dict[str, Type[BaseIndicator]] = {
        "utbot": UTBotIndicator,
        "technical": TechnicalIndicator,
        # Add new indicators here:
    }
    
    @classmethod
    def create(cls, name: str, params: dict) -> BaseIndicator:
        """
        Create indicator instance by name.
        
        Args:
            name: Indicator name (e.g., "utbot", "rsi")
            params: Dictionary of indicator parameters
            
        Returns:
            Configured indicator instance
            
        Raises:
            ValueError: If indicator name not found
            
        Example:
            >>> indicator = IndicatorRegistry.create("utbot", {
            ...     "sensitivity": 1.0,
            ...     "atr_period": 10
            ... })
            >>> type(indicator)
            <class 'indicators.utbot.UTBotIndicator'>
        """
        if name not in cls._registry:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Unknown indicator: '{name}'. "
                f"Available indicators: {available}"
            )
        
        indicator_class = cls._registry[name]
        return indicator_class(params)
    
    @classmethod
    def register(cls, name: str, indicator_class: Type[BaseIndicator]):
        """
        Register a new indicator dynamically.
        
        Useful for plugins or custom indicators loaded at runtime.
        
        Args:
            name: Unique name for the indicator
            indicator_class: Class that inherits from BaseIndicator
            
        Raises:
            ValueError: If name already registered or class doesn't inherit BaseIndicator
            
        Example:
            >>> class MyIndicator(BaseIndicator):
            ...     # implementation
            ...     pass
            >>> IndicatorRegistry.register("myindicator", MyIndicator)
        """
        if not issubclass(indicator_class, BaseIndicator):
            raise ValueError(
                f"{indicator_class.__name__} must inherit from BaseIndicator"
            )
        
        if name in cls._registry:
            print(f"Warning: Overwriting existing indicator '{name}'")
        
        cls._registry[name] = indicator_class
    
    @classmethod
    def list_indicators(cls) -> list[str]:
        """
        Get list of all registered indicator names.
        
        Returns:
            List of indicator names
        """
        return list(cls._registry.keys())
    
    @classmethod
    def get_indicator_info(cls, name: str) -> dict:
        """
        Get information about a registered indicator.
        
        Args:
            name: Indicator name
            
        Returns:
            Dictionary with indicator metadata
            
        Raises:
            ValueError: If indicator not found
        """
        if name not in cls._registry:
            raise ValueError(f"Unknown indicator: '{name}'")
        
        indicator_class = cls._registry[name]
        
        # Create a dummy instance to get params info
        try:
            dummy = indicator_class({})
        except (ValueError, KeyError):
            # Expected - missing required params
            dummy = None
        
        info = {
            "name": name,
            "class": indicator_class.__name__,
            "module": indicator_class.__module__,
        }
        
        if dummy:
            info["required_params"] = dummy.required_params
            info["warmup_period"] = dummy.warmup_period
        
        return info
