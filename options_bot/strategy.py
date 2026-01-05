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
        Signal Format: {'action': 'BUY', 'type': 'CE', 'confidence': 'HIGH'} or None
        """
        if len(ltf_data) < 50 or len(htf_data) < 50:
            return None

        # 1. Retrieve Configuration
        # Users can enable/disable specific filters here
        active_htf_indicators = config.get("active_indicators.htf", ["ema"])
        active_ltf_indicators = config.get("active_indicators.ltf", ["ema", "stochrsi", "rsi"])
        
        # 2. Prepare Data (Calculate needed indicators)
        # Note: In a live optimized version, this is done in DataHandler. 
        # Here we double-check or calculate on the fly for simplicity.
        
        # --- HTF (15 min) Indicators ---
        if "ema" in active_htf_indicators:
            htf_data = calculate_ema(htf_data, config.get("indicators.ema_fast", 9))
            htf_data = calculate_ema(htf_data, config.get("indicators.ema_slow", 21))
        
        if "utbot" in active_htf_indicators: # Example of substitution
            htf_data = calculate_utbot(htf_data, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr"))

        # --- LTF (5 min) Indicators ---
        # The 'Momentum' and 'Strength' filters rely on these
        if "ema" in active_ltf_indicators:
            ltf_data = calculate_ema(ltf_data, 9)
            ltf_data = calculate_ema(ltf_data, 21)
            
        if "stochrsi" in active_ltf_indicators:
            ltf_data = calculate_stochrsi(ltf_data)
            
        if "rsi" in active_ltf_indicators:
            ltf_data = calculate_rsi(ltf_data)
            
        if "utbot" in active_ltf_indicators:
            ltf_data = calculate_utbot(ltf_data, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr"))

        # 3. Apply The 4 Filters (Conditional)
        # We assume 'CALL' scenario first, then check 'PUT'
        
        # --- FILTER 1: HTF Trend (The Big Picture) ---
        # "Is the main trend UP?"
        htf_bullish = True
        htf_bearish = True
        
        if "ema" in active_htf_indicators:
             # Check last closed candle
            last_htf = htf_data.iloc[-1]
            if last_htf['ema_9'] > last_htf['ema_21']:
                htf_bearish = False # Can't be bearish
            else:
                htf_bullish = False # Can't be bullish
                
        if "utbot" in active_htf_indicators:
             # Alternative: Check UTBot signal
            last_htf = htf_data.iloc[-1]
            if last_htf['utbot_signal'] == 1:
                htf_bearish = False
            else:
                htf_bullish = False
        
        # --- FILTER 2: LTF Alignment (The Immediate Trend) ---
        # "Is the 5min trend aligned with 15min?"
        ltf_bullish_trend = True
        ltf_bearish_trend = True
        
        if "ema" in active_ltf_indicators:
            last_ltf = ltf_data.iloc[-1]
            if last_ltf['ema_9'] <= last_ltf['ema_21']:
                ltf_bullish_trend = False
            if last_ltf['ema_9'] >= last_ltf['ema_21']:
                ltf_bearish_trend = False

        # --- FILTER 3: Momentum (StochRSI / Entry Trigger) ---
        # "Is it the right time to enter?"
        # Usually we look for a CROSSOVER here
        momentum_buy = False
        momentum_sell = False
        
        # Option A: StochRSI (Default)
        if "stochrsi" in active_ltf_indicators:
            # Check for fresh crossover in K vs D
            # or if K crossed above Oversold (20)
            if detect_crossover(ltf_data, 'stochrsi_k', 'stochrsi_d'):
                momentum_buy = True
            if detect_crossunder(ltf_data, 'stochrsi_k', 'stochrsi_d'):
                momentum_sell = True
                
        # Option B: UTBot (Substitution)
        # If enabled, it overrides or adds to momentum
        if "utbot" in active_ltf_indicators:
            # UTBot Signal = 1 (Buy) or -1 (Sell)
            last_ltf = ltf_data.iloc[-1]
            # We look for a *Change* in signal for entry
            prev_ltf = ltf_data.iloc[-2]
            
            if last_ltf['utbot_signal'] == 1 and prev_ltf['utbot_signal'] != 1:
                momentum_buy = True
            if last_ltf['utbot_signal'] == -1 and prev_ltf['utbot_signal'] != -1:
                momentum_sell = True
        
        # If NO momentum indicator is active, we default to True (Disable Filter)
        # But usually you need *some* trigger.
        if "stochrsi" not in active_ltf_indicators and "utbot" not in active_ltf_indicators:
             # Basic EMA Crossover as backup trigger
             if detect_crossover(ltf_data, 'ema_9', 'ema_21'):
                 momentum_buy = True
             if detect_crossunder(ltf_data, 'ema_9', 'ema_21'):
                 momentum_sell = True

        # --- FILTER 4: Strength (RSI) ---
        # "Is the move strong enough?"
        strength_buy = True
        strength_sell = True
        
        if "rsi" in active_ltf_indicators:
            last_ltf = ltf_data.iloc[-1]
            rsi_val = last_ltf['rsi_14']
            
            # Buy Condition: RSI > 55
            if rsi_val < config.get("indicators.rsi_overbought", 55):
                strength_buy = False
            
            # Sell Condition: RSI < 45
            if rsi_val > config.get("indicators.rsi_oversold", 45):
                strength_sell = False

        # 4. Final Decision Logic
        # Combine all active filters
        
        # CE Entry
        if htf_bullish and ltf_bullish_trend and momentum_buy and strength_buy:
            self.logger.info("✅ SIGNAL GENERATED: BUY CE (All Filters Passed)")
            return {'action': 'BUY', 'type': 'CE'}
            
        # PE Entry
        if htf_bearish and ltf_bearish_trend and momentum_sell and strength_sell:
            self.logger.info("✅ SIGNAL GENERATED: BUY PE (All Filters Passed)")
            return {'action': 'BUY', 'type': 'PE'}
        
        # Diagnostic Logging (Only if some but not all filters pass, to avoid spam)
        # We log this every 50 iterations or so to tell the user what's missing
        if not hasattr(self, 'diag_count'): self.diag_count = 0
        self.diag_count += 1
        
        if self.diag_count % 30 == 0:
            if htf_bearish and ltf_bearish_trend:
                missing = []
                if not momentum_sell: missing.append("Momentum(UTBot Trigger)")
                if not strength_sell: missing.append(f"Strength(RSI < {config.get('indicators.rsi_overbought', 45)})")
                if missing:
                    self.logger.info(f"🔍 Monitoring PE: Trend is SELL, but waiting for: {', '.join(missing)}")
            elif htf_bullish and ltf_bullish_trend:
                missing = []
                if not momentum_buy: missing.append("Momentum(UTBot Trigger)")
                if not strength_buy: missing.append(f"Strength(RSI > {config.get('indicators.rsi_oversold', 55)})")
                if missing:
                    self.logger.info(f"🔍 Monitoring CE: Trend is BUY, but waiting for: {', '.join(missing)}")
            
        return None
