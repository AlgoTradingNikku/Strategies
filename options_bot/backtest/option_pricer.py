"""
Simplified Options Pricing Model for Backtesting

Since we don't have historical options data, we use a practical approximation
based on the Black-Scholes model and typical option Greeks.
"""

import math
from typing import Dict
from scipy.stats import norm

class OptionPricer:
    """
    Simplified options pricer using Black-Scholes approximation.
    This is NOT meant for real trading, only for backtesting estimation.
    """
    
    def __init__(self, 
                 risk_free_rate: float = 0.065,  # 6.5% (India T-Bills)
                 default_iv: float = 0.18):       # 18% IV (typical for Nifty)
        self.risk_free_rate = risk_free_rate
        self.default_iv = default_iv
        
    def black_scholes_call(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """
        Black-Scholes Call Option Price
        S: Spot price
        K: Strike price
        T: Time to expiry (in years)
        r: Risk-free rate
        sigma: Volatility (IV)
        """
        if T <= 0:
            return max(S - K, 0)  # Intrinsic value
        
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        call_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        return max(call_price, 0)
    
    def black_scholes_put(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Black-Scholes Put Option Price"""
        if T <= 0:
            return max(K - S, 0)
        
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return max(put_price, 0)
    
    def estimate_option_price(self, 
                              spot_price: float, 
                              strike_price: float, 
                              option_type: str,  # "CE" or "PE"
                              days_to_expiry: int,
                              implied_vol: float = None) -> float:
        """
        Estimate option premium for a given spot price and strike.
        
        Args:
            spot_price: Current index price
            strike_price: Option strike
            option_type: "CE" (Call) or "PE" (Put)
            days_to_expiry: Days until expiry
            implied_vol: Volatility (default uses class setting)
        
        Returns:
            Estimated option premium in rupees
        """
        T = days_to_expiry / 365.0  # Convert to years
        sigma = implied_vol if implied_vol else self.default_iv
        
        if option_type == "CE":
            price = self.black_scholes_call(spot_price, strike_price, T, self.risk_free_rate, sigma)
        else:  # PE
            price = self.black_scholes_put(spot_price, strike_price, T, self.risk_free_rate, sigma)
        
        return round(price, 2)
    
    def calculate_delta(self, 
                       spot_price: float, 
                       strike_price: float, 
                       option_type: str,
                       days_to_expiry: int,
                       implied_vol: float = None) -> float:
        """
        Calculate option Delta (rate of change of option price vs spot)
        Used for estimating how much the option moves when spot changes.
        """
        if days_to_expiry <= 0:
            return 1.0 if spot_price > strike_price else 0.0
        
        T = days_to_expiry / 365.0
        sigma = implied_vol if implied_vol else self.default_iv
        
        d1 = (math.log(spot_price / strike_price) + (self.risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        
        if option_type == "CE":
            delta = norm.cdf(d1)
        else:  # PE
            delta = norm.cdf(d1) - 1
        
        return abs(delta)
