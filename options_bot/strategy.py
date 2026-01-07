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
        allow_late_entry = config.get("strategy_settings.allow_late_entry", False)
        
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
            # Start as true, and turn false if ANY enabled indicator disagrees.
            if "utbot" in active_htf and last_htf is not None:
                if last_htf['utbot_signal'] != 1: htf_bullish = False
                if last_htf['utbot_signal'] != -1: htf_bearish = False
                
            if "ema" in active_htf and last_htf is not None:
                if last_htf['ema_9'] <= last_htf['ema_21']: htf_bullish = False
                if last_htf['ema_9'] >= last_htf['ema_21']: htf_bearish = False

        # ---------------------------------------------------------
        # FILTER 2: MOMENTUM (The Trigger)
        # ---------------------------------------------------------
        momentum_buy = False
        momentum_sell = False
        
        # Primary Trigger: UTBot (if enabled)
        if "utbot" in active_ltf:
            fresh_buy = last_ltf['utbot_signal'] == 1 and prev_ltf['utbot_signal'] != 1
            fresh_sell = last_ltf['utbot_signal'] == -1 and prev_ltf['utbot_signal'] != -1
            
            late_buy = last_ltf['utbot_signal'] == 1 and allow_late_entry
            late_sell = last_ltf['utbot_signal'] == -1 and allow_late_entry
            
            if fresh_buy or late_buy: momentum_buy = True
            if fresh_sell or late_sell: momentum_sell = True
            
        # Secondary Trigger: StochRSI (only if UTBot is not enabled)
        elif "stochrsi" in active_ltf:
            if detect_crossover(ltf_data, 'stochrsi_k', 'stochrsi_d'): momentum_buy = True
            if detect_crossunder(ltf_data, 'stochrsi_k', 'stochrsi_d'): momentum_sell = True
            
        # Tertiary Trigger: EMA Crossover (only if neither above are enabled)
        elif "ema" in active_ltf:
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
        # If we have an open position, we might want to know if the signal has flipped
        if last_ltf['utbot_signal'] == -1 and prev_ltf['utbot_signal'] == 1:
            return "PE_REVERSAL" # Chart turned Red
        elif last_ltf['utbot_signal'] == 1 and prev_ltf['utbot_signal'] == -1:
            return "CE_REVERSAL" # Chart turned Green
            
        return None

