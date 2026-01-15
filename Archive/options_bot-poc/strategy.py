import utils
import pandas as pd
import logging
from typing import Dict, Any, Tuple
from config import config
from indicators import calculate_ema, calculate_rsi, calculate_stochrsi, calculate_utbot, detect_crossover, detect_crossunder

class StrategyEngine:
    def __init__(self):
        self.logger = logging.getLogger("StrategyEngine")
        
    def generate_signal(self, htf_data: pd.DataFrame, ltf_data: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyzes DataFrames and returns a signal dictionary.
        Strictly respects active_indicators from config.
        """
        if len(ltf_data) < 20: 
            return None

        # 1. Retrieve Configuration
        active_htf = [x for x in (config.get("active_indicators.htf") or []) if x]
        active_ltf = [x for x in (config.get("active_indicators.ltf") or []) if x]
        
        # Check both "Renter Trend" (Recovery) and "Pullback" modes.
        # If EITHER is true, we allow the strategy to generate a "Continued" signal.
        # main.py will decide whether to enter immediately (Recovery) or wait (Pullback).
        recovery_enabled = config.get("strategy_settings.renter_trend_mode.enabled", False)
        pullback_enabled = config.get("strategy_settings.pullback_strategy_settings.enabled", False)
        allow_mid_stream = recovery_enabled or pullback_enabled
        
        # 2. Extract Latest Data
        last_ltf = ltf_data.iloc[-1]
        prev_ltf = ltf_data.iloc[-2]
        last_htf = htf_data.iloc[-1] if not htf_data.empty else None

        # ---------------------------------------------------------
        # FILTER 1: HTF TREND (15m)
        # ---------------------------------------------------------
        htf_bullish = True
        htf_bearish = True
        
        if active_htf:
            # If any HTF indicators are active, they must ALL agree.
            h_bull, h_bear = utils.detect_trend(htf_data, active_htf, default=True)
            if not h_bull: htf_bullish = False
            if not h_bear: htf_bearish = False
            
            # Additional EMA Filter if enabled
            if "ema" in active_htf and last_htf is not None:
                if last_htf['ema_9'] <= last_htf['ema_21']: htf_bullish = False
                if last_htf['ema_9'] >= last_htf['ema_21']: htf_bearish = False

        # ---------------------------------------------------------
        # FILTER 2: MOMENTUM (The Trigger)
        # ---------------------------------------------------------
        # TREND-BASED ARCHITECTURE UPDATE:
        # We no longer require "fresh flip" here. Instead, we return signals
        # whenever the trend IS active (Green or Red). main.py will handle
        # alignment checking and entry timing.
        
        momentum_buy = False
        momentum_sell = False
        
        # Primary Trigger: Trend State (UTBot or SuperTrend)
        if active_ltf:
            ltf_bull, ltf_bear = utils.detect_trend(ltf_data, active_ltf)
            if ltf_bull: momentum_buy = True
            if ltf_bear: momentum_sell = True
            
        # Secondary Trigger: StochRSI (if no signal indicators are active)
        if not momentum_buy and not momentum_sell and "stochrsi" in active_ltf:
            if detect_crossover(ltf_data, 'stochrsi_k', 'stochrsi_d'): momentum_buy = True
            if detect_crossunder(ltf_data, 'stochrsi_k', 'stochrsi_d'): momentum_sell = True
            
        # Tertiary Trigger: EMA Crossover
        if not momentum_buy and not momentum_sell and "ema" in active_ltf:
            if detect_crossover(ltf_data, 'ema_9', 'ema_21'): momentum_buy = True
            if detect_crossunder(ltf_data, 'ema_9', 'ema_21'): momentum_sell = True

        # ---------------------------------------------------------
        # FILTER 3: STRENGTH (RSI)
        # ---------------------------------------------------------
        strength_buy = True
        strength_sell = True
        
        if "rsi" in active_ltf:
            rsi_val = last_ltf['rsi_14']
            if rsi_val < config.get("indicators.rsi_overbought", 55): strength_buy = False
            if rsi_val > config.get("indicators.rsi_oversold", 45): strength_sell = False

        # ---------------------------------------------------------
        # FINAL DECISION
        # ---------------------------------------------------------
        
        # CE Signal
        if htf_bullish and momentum_buy and strength_buy:
            return {'action': 'BUY', 'type': 'CE'}
            
        # PE Signal
        if htf_bearish and momentum_sell and strength_sell:
            return {'action': 'BUY', 'type': 'PE'}
            
        # --- REVERSAL CHECK (For closing open positions) ---
        # Dynamically detect active signal column for reversal
        sig_col = None
        if "utbot" in active_ltf: sig_col = "utbot_signal"
        elif "supertrend" in active_ltf: sig_col = "supertrend_signal"
        
        if sig_col and sig_col in last_ltf:
            if last_ltf[sig_col] == -1 and prev_ltf[sig_col] == 1:
                return "PE_REVERSAL" # Chart turned Red
            elif last_ltf[sig_col] == 1 and prev_ltf[sig_col] == -1:
                return "CE_REVERSAL" # Chart turned Green
            
        return None

