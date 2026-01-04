"""
Utility functions for the Options Bot.
"""

def parse_time_value(value):
    """
    Parses time values with units into minutes.
    
    Supports:
    - Plain numbers: 15 → 15 minutes
    - Seconds: "30s" → 0.5 minutes
    - Minutes: "5m" → 5 minutes
    - Hours: "2h" → 120 minutes
    
    Examples:
        parse_time_value(15) → 15.0
        parse_time_value("30s") → 0.5
        parse_time_value("5m") → 5.0
        parse_time_value("2h") → 120.0
    
    Returns:
        float: Time value in minutes
    """
    if isinstance(value, (int, float)):
        return float(value)
    
    if isinstance(value, str):
        value = value.strip().lower()
        
        # Extract number and unit
        if value[-1] in ['s', 'm', 'h']:
            unit = value[-1]
            try:
                number = float(value[:-1])
            except ValueError:
                raise ValueError(f"Invalid time format: {value}")
            
            # Convert to minutes
            if unit == 's':
                return number / 60.0  # seconds to minutes
            elif unit == 'm':
                return number
            elif unit == 'h':
                return number * 60.0  # hours to minutes
        else:
            # No unit, assume minutes
            try:
                return float(value)
            except ValueError:
                raise ValueError(f"Invalid time format: {value}")
    
    raise ValueError(f"Invalid time value type: {type(value)}")


def format_time_value(minutes):
    """
    Formats minutes into human-readable string.
    
    Examples:
        format_time_value(0.5) → "30s"
        format_time_value(5) → "5m"
        format_time_value(120) → "2h"
    
    Returns:
        str: Formatted time string
    """
    if minutes < 1:
        return f"{int(minutes * 60)}s"
    elif minutes < 60:
        return f"{int(minutes)}m"
    else:
        hours = minutes / 60
        if hours == int(hours):
            return f"{int(hours)}h"
        else:
            return f"{minutes}m"
