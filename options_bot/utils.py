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
def get_expiry_date(base_symbol: str, expiry_type: str = "CURRENT_WEEKLY") -> str:
    """
    Calculates the expiry date string in DDMMMYY format (e.g., 08JAN26).
    Supports: NIFTY, BANKNIFTY (Thursdays), FINNIFTY (Tuesdays).
    """
    import datetime
    
    now = datetime.datetime.now()
    
    # Define primary expiry days (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri...)
    expiry_days = {
        "NIFTY": 1,      # Tuesday (Updated based on user feedback)
        "BANKNIFTY": 3,  # Thursday
        "FINNIFTY": 1,   # Tuesday
        "MIDCPNIFTY": 0  # Monday
    }
    
    target_weekday = expiry_days.get(base_symbol, 3) # Default to Thursday
    
    # Find the CURRENT weekly expiry
    days_ahead = target_weekday - now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    
    # If today is expiry day, check time. Usually 3:30 PM is cutoff.
    # For safety, if it's after 3:25 PM on expiry day, move to next.
    if days_ahead == 0 and now.hour >= 15 and now.minute >= 25:
        days_ahead = 7

    expiry_date = now + datetime.timedelta(days=days_ahead)
    
    # Handle NEXT_WEEKLY
    if expiry_type == "NEXT_WEEKLY":
        expiry_date = expiry_date + datetime.timedelta(days=7)
    
    # Format: DDMMMYY (e.g., 08JAN26)
    return expiry_date.strftime("%d%b%y").upper()
