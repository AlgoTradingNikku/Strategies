import utils
import pandas as pd
import logging
from config import config

class PullbackManager:
    """
    Manages complex re-entry logic based on configured groups and triggers.
    Logic:
        - Evaluates multiple "Groups" of conditions (e.g., EMA + RSI).
        - Each Group has an operator (AND/OR).
        - The Master Operator connects the Groups (OR).
    """

    def __init__(self):
        self.logger = logging.getLogger("PullbackManager")
        
    # ... (Keep Imports) ...
    
    def is_pullback_valid(self, signal_type: str, current_price: float, ltf_df: pd.DataFrame) -> bool:
        """Evaluates Pullback Strategy settings."""
        return self._evaluate_config_section("strategy_settings.pullback_strategy_settings", signal_type, current_price, ltf_df)

    def is_recovery_valid(self, signal_type: str, current_price: float, ltf_df: pd.DataFrame) -> bool:
        """Evaluates Recovery Mode Strength settings."""
        return self._evaluate_config_section("strategy_settings.renter_trend_mode", signal_type, current_price, ltf_df)

    def _evaluate_config_section(self, config_path: str, signal_type: str, current_price: float, ltf_df: pd.DataFrame) -> bool:
        settings = config.get(config_path, {})
        if not settings.get("enabled", False):
            return False

        groups = settings.get("groups", [])
        master_op = settings.get("master_operator", "OR").upper()
        
        group_results = []
        
        for group in groups:
            if not group.get("active", False):
                continue
                
            triggers = group.get("triggers", [])
            op = group.get("operator", "AND").upper()
            
            trigger_results = []
            for trigger_name in triggers:
                # Pass full config path to trigger for looking up definitions
                result = self._evaluate_trigger(trigger_name, signal_type, current_price, ltf_df, config_path)
                trigger_results.append(result)
                
            if not trigger_results:
                group_val = False
            elif op == "AND":
                group_val = all(trigger_results)
            elif op == "OR":
                group_val = any(trigger_results)
            else:
                group_val = False
                
            group_results.append(group_val)

        if not group_results:
            # If no groups are active, should we return True or False?
            # For Recovery: If enabled but no filters active -> True (Unfiltered)
            # For Pullback: If enabled but no filters active -> False (No Dip found)
            # Logic decision: If enabled but EMPTY active groups, return True because "Enabled" is the master switch.
            return True
            
        if master_op == "AND":
            return all(group_results)
        elif master_op == "OR":
            return any(group_results)
        return False

    def _evaluate_trigger(self, trigger_name: str, signal_type: str, current_price: float, df: pd.DataFrame, config_base: str) -> bool:
        try:
            if trigger_name == "EMA_TOUCH":
                return self._check_ema_touch(signal_type, current_price, df, config_base)
            elif trigger_name == "RSI_DIP":
                return self._check_rsi_dip(signal_type, df, config_base)
            elif trigger_name == "FIB_RETRACEMENT":
                return self._check_fib_retracement(signal_type, current_price, df, config_base)
            elif trigger_name == "VOLUME_SPIKE":
                return self._check_volume_spike(df, config_base)
            elif trigger_name == "RSI_MIN":
                return self._check_rsi_min(signal_type, df, config_base)
            elif trigger_name == "ADX_MIN":
                return self._check_adx_min(df, config_base)
            else:
                self.logger.warning(f"Unknown Trigger: {trigger_name}")
                return False
        except Exception as e:
            self.logger.error(f"Error evaluating {trigger_name}: {e}")
            return False

    # ... (Update existing triggers to accept generic config_base) ...

    def _check_rsi_min(self, signal_type: str, df: pd.DataFrame, config_base: str) -> bool:
        """Checks if RSI is ABOVE a minimum value (Condition: Strong Momentum)."""
        defs = config.get(f"{config_base}.definitions.rsi_min", {})
        threshold = defs.get("value", 55)
        
        rsi_val = df.iloc[-1]['rsi_14']
        
        if signal_type == 'CE':
            return rsi_val >= threshold
        else: # PE (Strong Downward Momentum = Low RSI) - Wait, concept is "Strength".
            # If Strength means "Trend Strength", for PE, RSI should be LOW (< 45).
            # But the user might want "RSI > 55" as a generic 'Volatility' check?
            # Standard interpretation: Strong Uptrend = High RSI. Strong Downtrend = Low RSI.
            # Mirror logic:
            return rsi_val <= (100 - threshold)

    def _check_adx_min(self, df: pd.DataFrame, config_base: str) -> bool:
        """Checks if ADX is ABOVE a minimum (Condition: Strong Trend)."""
        # Note: Requires ADX calculation in DF. Assuming it exists.
        defs = config.get(f"{config_base}.definitions.adx_min", {})
        threshold = defs.get("value", 25)
        
        if 'adx' not in df.columns:
             # Calculate locally if missing (Optional, for now return False)
             return False
        
        return df.iloc[-1]['adx'] >= threshold

    # ------------------------------------------------------------------
    # TRIGGER IMPLEMENTATIONS
    # ------------------------------------------------------------------

    def _check_ema_touch(self, signal_type: str, current_price: float, df: pd.DataFrame, config_base: str) -> bool:
        """
        Checks if price is touching/near the configured EMA.
        Default tolerance: 0.15% (Customizable in future)
        """
        defs = config.get(f"{config_base}.definitions.ema_touch", {})
        period = defs.get("period", 9)
        tolerance_pct = 0.15 # Hardcoded for now, could be in config
        
        # Ensure EMA exists in DF. If not, calculate it (simplified) or assume it exists from indicators.py
        col_name = f'ema_{period}'
        if col_name not in df.columns:
            # Fallback to standard 9 or 21 if custom period requested but not in main DF
            if period == 9 and 'ema_9' in df.columns: col_name = 'ema_9'
            elif period == 21 and 'ema_21' in df.columns: col_name = 'ema_21'
            else: return False 

        ema_val = df.iloc[-1][col_name]
        dist = abs(current_price - ema_val) / ema_val * 100
        
        # Logic: Price must be CLOSE to EMA.
        is_near = dist <= tolerance_pct
        
        # --- NEW: CLEARANCE CHECK (Freshness) ---
        # To avoid entering multiple times while "grinding" along the EMA,
        # we check if the PREVIOUS candle was already touching/near the EMA.
        if len(df) >= 2:
            prev_ema = df.iloc[-2][col_name]
            # Use 'low' for CE (support) and 'high' for PE (resistance) to see if it was near
            if signal_type == 'CE':
                prev_dist = abs(df.iloc[-2]['low'] - prev_ema) / prev_ema * 100
            else:
                prev_dist = abs(df.iloc[-2]['high'] - prev_ema) / prev_ema * 100
                
            was_near_prev = prev_dist <= tolerance_pct
            
            # Valid only if it is a FRESH touch (was not near before)
            return is_near and (not was_near_prev)
            
        return is_near

    def _check_rsi_dip(self, signal_type: str, df: pd.DataFrame, config_base: str) -> bool:
        """
        CE (Buy): RSI should dip BELOW value (Oversold in uptrend).
        PE (Sell): RSI should spike ABOVE (100-value) (Overbought in downtrend).
        """
        defs = config.get(f"{config_base}.definitions.rsi_dip", {})
        threshold = defs.get("value", 40)
        
        rsi_val = df.iloc[-1]['rsi_14']
        
        if signal_type == 'CE':
            return rsi_val <= threshold
        else: # PE
            return rsi_val >= (100 - threshold)

    def _check_volume_spike(self, df: pd.DataFrame, config_base: str) -> bool:
        """Checks if current candle volume is significantly higher than average."""
        defs = config.get(f"{config_base}.definitions.volume_spike", {})
        multiplier = defs.get("multiplier", 2.0)
        
        if 'volume' not in df.columns: return False
        
        current_vol = df.iloc[-1]['volume']
        avg_vol = df['volume'].rolling(20).mean().iloc[-1]
        
        if avg_vol == 0: return False
        
        return current_vol >= (avg_vol * multiplier)

    def _check_fib_retracement(self, signal_type: str, current_price: float, df: pd.DataFrame, config_base: str) -> bool:
        """
        Estimates Swing High/Low from the current trend and checks for Fib level match.
        """
        defs = config.get(f"{config_base}.definitions.fib_retracement", {})
        fib_level = defs.get("level", 0.618)
        tolerance_pct = 0.2
        
        # 1. Identify Trend Start
        # Dynamically detect active signal column
        active_ltf = config.get("active_indicators.ltf") or []
        sig_col = utils.get_signal_col(df, active_ltf)
        
        if not sig_col:
            # Fallback if no active indicator found in DF (should not happen in main loop)
            sig_col = df.columns[-1] 

        try:
            current_sig = 1 if signal_type == 'CE' else -1
            # Scan backwards for when signal was NOT current_sig
            subset = df.tail(50)
            
            # Find the last time the signal was DIFFERENT
            mask = subset[sig_col] != current_sig
            if not mask.any():
                trend_df = subset
            else:
                last_flip_idx = mask[::-1].idxmax()
                trend_df = subset.loc[last_flip_idx:]
                
            if trend_df.empty: return False

            # 2. Calculate Range
            high_price = trend_df['high'].max()
            low_price = trend_df['low'].min()
            price_range = high_price - low_price
            
            if price_range == 0: return False

            # 3. Calculate Target Price
            if signal_type == 'CE':
                target = high_price - (price_range * fib_level)
            else:
                target = low_price + (price_range * fib_level)

            # 4. Check Proximity
            dist_pct = abs(current_price - target) / target * 100
            return dist_pct <= tolerance_pct
            
        except Exception as e:
            self.logger.error(f"Fib Calculation Error: {e}")
            return False
            
            
    def get_wait_status(self, signal_type: str, current_price: float, ltf_df: pd.DataFrame) -> str:
        """
        Returns a human-readable status of what conditions are being waited for.
        Example: "EMA: 25680 (Dist 0.05%) | RSI: 42/60"
        """
        config_path = "strategy_settings.pullback_strategy_settings"
        settings = config.get(config_path, {})
        if not settings.get("enabled", False):
            return "Pullback Disabled"

        groups = settings.get("groups", [])
        status_parts = []

        for group in groups:
            if not group.get("active", False):
                continue
            
            triggers = group.get("triggers", [])
            for trigger_name in triggers:
                try:
                    if trigger_name == "EMA_TOUCH":
                        defs = config.get(f"{config_path}.definitions.ema_touch", {})
                        period = defs.get("period", 9)
                        col = f'ema_{period}'
                        if col in ltf_df.columns:
                            ema_val = ltf_df.iloc[-1][col]
                            dist = abs(current_price - ema_val) / ema_val * 100
                            status_parts.append(f"EMA{period}:{ema_val:.1f} (Gap:{dist:.2f}%)")
                    elif trigger_name == "RSI_DIP":
                        defs = config.get(f"{config_path}.definitions.rsi_dip", {})
                        threshold = defs.get("value", 40)
                        rsi_val = ltf_df.iloc[-1]['rsi_14']
                        target_rsi = threshold if signal_type == 'CE' else (100 - threshold)
                        status_parts.append(f"RSI:{rsi_val:.1f}/{target_rsi}")
                except:
                    pass
        
        return " | ".join(status_parts) if status_parts else "Waiting for Pullback"

    # ... (Rest of the file) ...
