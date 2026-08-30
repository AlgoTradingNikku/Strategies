"""
Momentum-ChatGPT Strategy Engine Interface.
Coordinates Market Regime Classification, Sector Ranking, Stock Setup Detection,
Scoring, Risk-based Sizing, and Portfolio Construction.
"""

from typing import Dict, Any, List, Optional
import pandas as pd

from .market_regime import compute_nifty_indicators, compute_market_breadth, classify_market_regime
from .sector_strength import rank_sectors
from .setups import evaluate_stock_momentum
from .portfolio import filter_portfolio_selection


class MomentumChatGPTEngine:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.strategy_id = "momentum_chatgpt"
        self.strategy_version = "1.0.0"

    def run_scan(
        self,
        stock_dfs: Dict[str, pd.DataFrame],
        benchmark_df: pd.DataFrame = None,
        sector_dfs: Dict[str, pd.DataFrame] = None,
        stock_sectors: Dict[str, str] = None,
        total_capital: float = 1000000.0
    ) -> Dict[str, Any]:
        """Runs full Momentum-ChatGPT strategy scan pipeline."""
        cfg = self.config.get("momentum_chatgpt", {})
        min_score = cfg.get("min_score", 60)

        # Step 1: Market Regime
        nifty_metrics = compute_nifty_indicators(benchmark_df)
        breadth_metrics = compute_market_breadth(stock_dfs)
        regime_info = classify_market_regime(nifty_metrics, breadth_metrics, cfg)
        regime_mult = regime_info["multiplier"]

        # Step 2: Sector Strength Ranking
        sector_rankings = {}
        if sector_dfs:
            sector_rankings = rank_sectors(sector_dfs, benchmark_df)

        # Step 3: Individual Stock Setup Detection & Scoring
        candidates = []
        rejected = []

        for symbol, df in stock_dfs.items():
            eval_res = evaluate_stock_momentum(df, benchmark_df, cfg)
            if not eval_res.get("qualified", False):
                rejected.append({"symbol": symbol, "reason": eval_res.get("rejection_reason", "DISQUALIFIED")})
                continue

            raw_score = eval_res["raw_score"]
            final_score = raw_score * regime_mult

            if final_score < min_score:
                rejected.append({"symbol": symbol, "reason": f"SCORE_BELOW_THRESHOLD ({final_score:.1f} < {min_score})"})
                continue

            sector = stock_sectors.get(symbol, "GENERAL") if stock_sectors else "GENERAL"
            candidate = {
                "symbol": symbol,
                "sector": sector,
                "raw_score": raw_score,
                "final_score": round(final_score, 2),
                "regime": regime_info["regime"],
                "regime_mult": regime_mult,
                "setup_type": eval_res["setup_type"],
                "entry_price": eval_res["entry_price"],
                "stop_loss": eval_res["stop_loss"],
                "target_price": eval_res["target_price"],
                "rr_ratio": eval_res["rr_ratio"],
                "rsi": eval_res["rsi"],
                "adx": eval_res["adx"],
                "vol_surge": eval_res["vol_surge"],
                "mansfield_rs": eval_res["mansfield_rs"],
                "turnover_cr": eval_res["turnover_cr"],
            }
            candidates.append(candidate)

        # Step 4: Portfolio Selection & Diversification
        selected_portfolio = filter_portfolio_selection(candidates, stock_dfs, total_capital, cfg)

        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "market_regime": regime_info,
            "sector_rankings": sector_rankings,
            "all_candidates": candidates,
            "selected_portfolio": selected_portfolio,
            "rejected_count": len(rejected),
            "rejected_details": rejected[:20],
        }
