"""
Portfolio Diversification & Sizing Engine for Momentum-ChatGPT.
Enforces position caps, sector exposure limits, cash reserves, and correlation filtering.
"""

from typing import Dict, Any, List
import math
import pandas as pd
import numpy as np


def compute_position_quantity(
    entry_price: float,
    stop_loss: float,
    total_capital: float = 1000000.0,
    cfg: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Calculate position quantity based on risk-per-trade and position value cap."""
    if cfg is None:
        cfg = {}

    risk_pct = cfg.get("risk_per_trade_pct", 0.50) / 100.0
    max_pos_pct = cfg.get("max_position_pct", 15.0) / 100.0

    max_trade_risk = total_capital * risk_pct
    max_position_val = total_capital * max_pos_pct

    risk_per_share = max(0.01, entry_price - stop_loss)
    risk_qty = math.floor(max_trade_risk / risk_per_share)
    capital_qty = math.floor(max_position_val / entry_price)

    quantity = max(0, min(risk_qty, capital_qty))
    position_value = quantity * entry_price
    actual_risk = quantity * risk_per_share

    return {
        "quantity": quantity,
        "position_value": round(position_value, 2),
        "actual_risk": round(actual_risk, 2),
        "risk_per_share": round(risk_per_share, 2),
    }


def filter_portfolio_selection(
    candidates: List[Dict[str, Any]],
    stock_dfs: Dict[str, pd.DataFrame] = None,
    total_capital: float = 1000000.0,
    cfg: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    Sequentially applies score ranking, position caps, sector exposure limits, 
    cash reserve rules, and return correlation caps to select optimal portfolio set.
    """
    if cfg is None:
        cfg = {}

    max_positions = cfg.get("max_positions", 8)
    max_sector_pct = cfg.get("max_sector_exposure_pct", 30.0) / 100.0
    min_cash_pct = cfg.get("minimum_cash_pct", 20.0) / 100.0
    max_corr = cfg.get("max_correlation", 0.70)

    max_sector_val = total_capital * max_sector_pct
    available_capital = total_capital * (1.0 - min_cash_pct)

    # Sort candidates by final regime-adjusted score descending
    sorted_candidates = sorted(candidates, key=lambda x: x.get("final_score", 0.0), reverse=True)

    selected = []
    sector_exposure = {}
    allocated_capital = 0.0
    selected_symbols = []

    for cand in sorted_candidates:
        if len(selected) >= max_positions:
            break

        symbol = cand["symbol"]
        sector = cand.get("sector", "GENERAL")
        entry = cand["entry_price"]
        sl = cand["stop_loss"]

        # Calculate sizing
        sizing = compute_position_quantity(entry, sl, total_capital, cfg)
        pos_val = sizing["position_value"]

        if sizing["quantity"] <= 0:
            continue

        # Check cash reserve
        if allocated_capital + pos_val > available_capital:
            continue

        # Check sector cap
        curr_sec_val = sector_exposure.get(sector, 0.0)
        if curr_sec_val + pos_val > max_sector_val:
            continue

        # Check 60D return correlation if stock data is available
        is_correlated = False
        if stock_dfs and symbol in stock_dfs and selected_symbols:
            cand_df = stock_dfs[symbol]
            if len(cand_df) >= 60:
                cand_ret = cand_df['close'].pct_change().iloc[-60:]
                for sel_sym in selected_symbols:
                    if sel_sym in stock_dfs and len(stock_dfs[sel_sym]) >= 60:
                        sel_ret = stock_dfs[sel_sym]['close'].pct_change().iloc[-60:]
                        corr = cand_ret.corr(sel_ret)
                        if not np.isnan(corr) and corr > max_corr:
                            is_correlated = True
                            break

        if is_correlated:
            continue

        # Accept candidate
        cand["sizing"] = sizing
        selected.append(cand)
        selected_symbols.append(symbol)
        allocated_capital += pos_val
        sector_exposure[sector] = curr_sec_val + pos_val

    return selected
