"""
Configuration Validator - Schema validation for config.yaml

Prevents bot crashes from invalid configuration changes.
Validates types, ranges, and logical consistency.
"""

from typing import Dict, Any, List, Tuple
import re


class ConfigValidationError(Exception):
    """Raised when configuration validation fails"""
    pass


class ConfigValidator:
    """
    Validates bot configuration against schema rules.
    
    Example:
        validator = ConfigValidator()
        is_valid, errors = validator.validate(config)
        if not is_valid:
            print(f"Invalid config: {errors}")
    """
    
    SCHEMA = {
        # Trading mode
        "live_trade": {"type": bool, "required": True},
        "max_positions": {"type": int, "min": 1, "max": 10, "required": True},
        "max_lots": {"type": int, "min": 1, "max": 100, "required": True},
        "nifty_lot_size": {"type": int, "min": 1, "required": True},
        
        # Strike selection
        "strike_selection": {
            "type": dict,
            "schema": {
                "mode": {"type": str, "enum": ["AUTO", "MANUAL"], "required": True},
                "manual_strikes": {"type": list, "required": False},
                "max_option_price": {"type": (int, float), "min": 0, "required": False}
            }
        },
        
        # TSL configuration
        "tsl": {
            "type": dict,
            "schema": {
                "mode": {"type": str, "enum": ["ATR", "PERCENT", "POINTS"], "required": True},
                "atr_multiplier": {"type": (int, float), "min": 0.1, "max": 10, "required": False},
                "trail_pct": {"type": (int, float), "min": 0.1, "max": 50, "required": False},
                "trail_points": {"type": (int, float), "min": 1, "required": False},
                "enable_profit_guard": {"type": bool, "required": False},
                "guard_1_pct": {"type": (int, float), "min": 0, "required": False},
                "guard_1_trail": {"type": (int, float), "min": 0, "required": False}
            }
        },
        
        # Entry conditions
        "entry_conditions": {
            "type": dict,
            "schema": {
                "use_indicator": {"type": bool, "required": False},
                "use_filters": {"type": bool, "required": False},
                "momentum_check_mode": {"type": str, "enum": ["ALL", "ANY", "NONE"], "required": False},
                "vol_multiplier": {"type": (int, float), "min": 0.1, "max": 10, "required": False},
                "adx_min": {"type": (int, float), "min": 0, "max": 100, "required": False},
                "rsi_min": {"type": (int, float), "min": 0, "max": 100, "required": False},
                "rsi_max": {"type": (int, float), "min": 0, "max": 100, "required": False}
            }
        },
        
        # System configuration
        "system": {
            "type": dict,
            "schema": {
                "loop_intervals": {
                    "type": dict,
                    "schema": {
                        "scanner": {"type": (int, float), "min": 1, "max": 60, "required": False},
                        "risk_monitor": {"type": (int, float), "min": 0.5, "max": 10, "required": False},
                        "position_sync": {"type": (int, float), "min": 5, "max": 300, "required": False}
                    }
                }
            }
        },
        
        # Execution
        "execution": {
            "type": dict,
            "schema": {
                "order_type": {"type": str, "enum": ["LIMIT", "SMART_LIMIT", "MARKET"], "required": False},
                "order_timeout_sec": {"type": (int, float), "min": 1, "max": 60, "required": False},
                "enable_bot_auto_sell": {"type": bool, "required": False}
            }
        }
    }
    
    def validate(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate configuration against schema.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            (is_valid, errors) tuple
        """
        errors = []
        
        try:
            self._validate_dict(config, self.SCHEMA, "config", errors)
            
            # Custom validation logic
            self._validate_custom_rules(config, errors)
            
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
        
        return (len(errors) == 0, errors)
    
    def _validate_dict(self, data: Dict, schema: Dict, path: str, errors: List[str]):
        """Recursively validate dictionary against schema"""
        for key, rules in schema.items():
            current_path = f"{path}.{key}"
            
            # Check required fields
            if rules.get("required", False) and key not in data:
                errors.append(f"{current_path}: Required field missing")
                continue
            
            if key not in data:
                continue  # Optional field not present
            
            value = data[key]
            expected_type = rules.get("type")
            
            # Type validation
            if expected_type:
                if isinstance(expected_type, tuple):
                    if not isinstance(value, expected_type):
                        errors.append(f"{current_path}: Expected {expected_type}, got {type(value)}")
                        continue
                else:
                    if not isinstance(value, expected_type):
                        errors.append(f"{current_path}: Expected {expected_type.__name__}, got {type(value).__name__}")
                        continue
            
            # Nested dict validation
            if isinstance(value, dict) and "schema" in rules:
                self._validate_dict(value, rules["schema"], current_path, errors)
                continue
            
            # Enum validation
            if "enum" in rules and value not in rules["enum"]:
                errors.append(f"{current_path}: Invalid value '{value}'. Must be one of {rules['enum']}")
            
            # Range validation (for numbers)
            if isinstance(value, (int, float)):
                if "min" in rules and value < rules["min"]:
                    errors.append(f"{current_path}: Value {value} below minimum {rules['min']}")
                if "max" in rules and value > rules["max"]:
                    errors.append(f"{current_path}: Value {value} above maximum {rules['max']}")
    
    def _validate_custom_rules(self, config: Dict, errors: List[str]):
        """Custom validation logic for cross-field dependencies"""
        
        # 1. Manual strikes format validation
        strike_cfg = config.get("strike_selection", {})
        if strike_cfg.get("mode") == "MANUAL":
            manual_strikes = strike_cfg.get("manual_strikes", [])
            if not manual_strikes:
                errors.append("strike_selection.manual_strikes: Required when mode=MANUAL")
            else:
                # Validate strike format
                valid_pattern = re.compile(r'^[A-Z]+\d{2}[A-Z]{3}\d{2}\d+[CP]E$')
                for i, strike in enumerate(manual_strikes):
                    if not isinstance(strike, str):
                        errors.append(f"strike_selection.manual_strikes[{i}]: Must be string")
                    elif not valid_pattern.match(strike):
                        errors.append(f"strike_selection.manual_strikes[{i}]: Invalid format '{strike}'")
        
        # 2. RSI range validation
        entry_cfg = config.get("entry_conditions", {})
        if entry_cfg.get("check_rsi", False):
            rsi_min = entry_cfg.get("rsi_min", 0)
            rsi_max = entry_cfg.get("rsi_max", 100)
            if rsi_min >= rsi_max:
                errors.append("entry_conditions: rsi_min must be less than rsi_max")
        
        # 3. TSL mode-specific params
        tsl_cfg = config.get("tsl", {})
        tsl_mode = tsl_cfg.get("mode", "ATR")
        if tsl_mode == "ATR" and "atr_multiplier" not in tsl_cfg:
            errors.append("tsl: atr_multiplier required when mode=ATR")
        if tsl_mode == "PERCENT" and "trail_pct" not in tsl_cfg:
            errors.append("tsl: trail_pct required when mode=PERCENT")
        if tsl_mode == "POINTS" and "trail_points" not in tsl_cfg:
            errors.append("tsl: trail_points required when mode=POINTS")
        
        # 4. Profit guard stages logical order
        if tsl_cfg.get("enable_profit_guard", False):
            g1 = tsl_cfg.get("guard_1_pct", 0)
            g2 = tsl_cfg.get("guard_2_pct", 0)
            g3 = tsl_cfg.get("guard_3_pct", 0)
            if not (g1 < g2 < g3):
                errors.append("tsl: Profit guard stages must be in ascending order (guard_1_pct < guard_2_pct < guard_3_pct)")