"""
===============================================================================
  Bot-Options / core / strike_selector.py
  Strike Selection Engine — selects eligible CE/PE option contract strikes
  from the option chain based on the chosen strategy (ATM, ITM, OTM, PREMIUM, etc.)
  and filters for liquidity/OI.
===============================================================================
"""

import logging
from typing import Optional, dict, list, Any

log = logging.getLogger(__name__)

def select_strike(
    chain_data: dict[str, Any],
    option_type: str,     # 'CE' or 'PE'
    selection_cfg: dict,  # strike_selection block from config
    strike_step: float = 50.0
) -> Optional[dict[str, Any]]:
    """
    Selects a single target option contract from the chain based on config guidelines.

    Parameters
    ----------
    chain_data   : Raw option chain returned by OpenAlgo (includes 'chain', 'atm_strike', 'underlying_ltp')
    option_type  : 'CE' or 'PE'
    selection_cfg: Dictionary containing strike selection parameters
    strike_step  : The difference between adjacent strike prices (e.g. 50 for NIFTY, 100 for BANKNIFTY)

    Returns
    -------
    Dictionary of the chosen option contract (e.g. {'symbol': ..., 'ltp': ..., 'oi': ...}) or None.
    """
    if not chain_data or "chain" not in chain_data or not chain_data["chain"]:
        log.warning("No chain data available for strike selection.")
        return None

    method = selection_cfg.get("method", "ATM").upper()
    
    # 1. Extract and sort strikes
    chain_list = chain_data["chain"]
    # Sort chain by strike price ascending
    chain_list = sorted(chain_list, key=lambda x: float(x.get("strike", 0)))
    strikes = [float(x["strike"]) for x in chain_list]
    
    if not strikes:
        return None

    # 2. Identify ATM Strike index
    # Use OpenAlgo's pre-identified atm_strike or calculate closest to underlying_ltp
    underlying_ltp = float(chain_data.get("underlying_ltp", 0))
    atm_strike_val = chain_data.get("atm_strike")
    if atm_strike_val is not None:
        atm_strike_val = float(atm_strike_val)
    else:
        # Fallback to closest strike
        atm_strike_val = min(strikes, key=lambda x: abs(x - underlying_ltp))

    # Find the index of the ATM strike in the sorted list
    try:
        atm_idx = strikes.index(atm_strike_val)
    except ValueError:
        # Find closest match if exact not in list
        closest_strike = min(strikes, key=lambda x: abs(x - atm_strike_val))
        atm_idx = strikes.index(closest_strike)

    log.debug("ATM Strike: %s (Index %d) | Underlying LTP: %s", strikes[atm_idx], atm_idx, underlying_ltp)

    target_idx = atm_idx

    # 3. Apply selector logic
    if method == "ATM":
        target_idx = atm_idx

    elif method == "OTM":
        offset = int(selection_cfg.get("otm_strikes", 1))
        if option_type == "CE":
            # For Calls, OTM is higher strike prices
            target_idx = min(len(strikes) - 1, atm_idx + offset)
        else:
            # For Puts, OTM is lower strike prices
            target_idx = max(0, atm_idx - offset)

    elif method == "ITM":
        offset = int(selection_cfg.get("itm_strikes", 1))
        if option_type == "CE":
            # For Calls, ITM is lower strike prices
            target_idx = max(0, atm_idx - offset)
        else:
            # For Puts, ITM is higher strike prices
            target_idx = min(len(strikes) - 1, atm_idx + offset)

    elif method == "PREMIUM":
        # Select the strike whose option price falls within premium_min and premium_max
        p_min = float(selection_cfg.get("premium_min", 50))
        p_max = float(selection_cfg.get("premium_max", 500))
        
        eligible_contracts = []
        for i, item in enumerate(chain_list):
            opt_data = item.get(option_type.lower(), {})
            ltp = float(opt_data.get("ltp", 0))
            if p_min <= ltp <= p_max:
                eligible_contracts.append((i, ltp))
        
        if eligible_contracts:
            # Pick the contract closest to ATM
            best_idx = min(eligible_contracts, key=lambda x: abs(x[0] - atm_idx))[0]
            target_idx = best_idx
        else:
            log.warning("No strikes found in premium range [%s, %s] for %s.", p_min, p_max, option_type)
            return None

    elif method == "LIQUIDITY":
        # Select the strike with the highest product of OI * Volume
        # This prevents trading illiquid contracts
        best_idx = atm_idx
        best_score = -1.0
        
        # Consider a window around ATM to keep the trade relevant
        window_start = max(0, atm_idx - 5)
        window_end = min(len(strikes) - 1, atm_idx + 5)
        
        for i in range(window_start, window_end + 1):
            item = chain_list[i]
            opt_data = item.get(option_type.lower(), {})
            oi = float(opt_data.get("oi", 0))
            vol = float(opt_data.get("volume", 0))
            score = oi * vol
            if score > best_score:
                best_score = score
                best_idx = i
        target_idx = best_idx

    else:
        log.warning("Unsupported strike selection method '%s'. Defaulting to ATM.", method)
        target_idx = atm_idx

    selected_strike_item = chain_list[target_idx]
    selected_contract = selected_strike_item.get(option_type.lower(), {})
    
    # Add strike price value to the option contract dict for downstream logic
    if selected_contract:
        selected_contract = dict(selected_contract)
        selected_contract["strike"] = float(selected_strike_item.get("strike"))
        selected_contract["option_type"] = option_type
        
        # Apply standard hard filters (OI & Volume thresholds)
        min_oi = float(selection_cfg.get("oi_min_threshold", 0))
        min_vol = float(selection_cfg.get("liquidity_min_volume", 0))
        
        oi = float(selected_contract.get("oi", 0))
        vol = float(selected_contract.get("volume", 0))
        
        if oi < min_oi:
            log.warning("Selected contract %s rejected by OI filter: %s < %s", 
                        selected_contract.get("symbol"), oi, min_oi)
            return None
            
        if vol < min_vol:
            log.warning("Selected contract %s rejected by Volume filter: %s < %s", 
                        selected_contract.get("symbol"), vol, min_vol)
            return None
            
        return selected_contract

    return None
