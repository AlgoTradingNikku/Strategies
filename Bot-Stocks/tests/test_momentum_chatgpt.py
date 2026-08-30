"""
Unit tests for Momentum-ChatGPT strategy engine.
"""

import pytest
import pandas as pd
import numpy as np

from strategies.momentum_chatgpt import (
    MomentumChatGPTEngine,
    classify_market_regime,
    rank_sectors,
    evaluate_stock_momentum,
    filter_portfolio_selection,
)


def _synthetic_ohlcv(bars: int = 350, base_price: float = 100.0, trend: float = 0.5) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=bars, freq="D")
    prices = [base_price + i * trend + np.sin(i / 5.0) * 2.0 for i in range(bars)]
    closes = pd.Series(prices, index=dates)
    highs = closes + 2.0
    lows = closes - 2.0
    opens = closes - 0.5
    volumes = pd.Series(1000000 + np.random.randint(0, 500000, size=bars), index=dates)

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def test_market_regime_classification():
    nifty_df = _synthetic_ohlcv(bars=350, base_price=20000.0, trend=10.0)
    stock_dfs = {"STOCK1": _synthetic_ohlcv(bars=350, base_price=100.0, trend=0.5)}

    from strategies.momentum_chatgpt.market_regime import compute_nifty_indicators, compute_market_breadth
    nifty_metrics = compute_nifty_indicators(nifty_df)
    breadth_metrics = compute_market_breadth(stock_dfs)
    regime = classify_market_regime(nifty_metrics, breadth_metrics)

    assert regime["regime"] in ["STRONG_BULL", "SELECTIVE_BULL", "NEUTRAL", "BEARISH"]
    assert 0.5 <= regime["multiplier"] <= 1.0


def test_sector_strength_ranking():
    sector_dfs = {
        "NIFTY IT": _synthetic_ohlcv(bars=100, base_price=35000.0, trend=15.0),
        "NIFTY BANK": _synthetic_ohlcv(bars=100, base_price=45000.0, trend=-5.0),
    }
    bm_df = _synthetic_ohlcv(bars=100, base_price=22000.0, trend=5.0)
    ranked = rank_sectors(sector_dfs, bm_df)

    assert "NIFTY IT" in ranked
    assert "NIFTY BANK" in ranked
    assert ranked["NIFTY IT"]["rank"] == 1


def test_evaluate_stock_momentum_qualification():
    df = _synthetic_ohlcv(bars=350, base_price=500.0, trend=1.0)
    bm_df = _synthetic_ohlcv(bars=350, base_price=22000.0, trend=5.0)
    cfg = {"min_history_bars": 300, "min_turnover_20d_crore": 1.0, "min_price": 50.0}

    eval_res = evaluate_stock_momentum(df, bm_df, cfg)
    assert eval_res["qualified"] is True
    assert eval_res["raw_score"] > 0
    assert eval_res["setup_type"] is not None
    assert eval_res["entry_price"] > 0
    assert eval_res["stop_loss"] < eval_res["entry_price"]
    assert eval_res["target_price"] > eval_res["entry_price"]


def test_portfolio_selection_filters():
    candidates = [
        {"symbol": "STOCK1", "sector": "IT", "final_score": 85.0, "entry_price": 500.0, "stop_loss": 480.0},
        {"symbol": "STOCK2", "sector": "IT", "final_score": 80.0, "entry_price": 600.0, "stop_loss": 570.0},
        {"symbol": "STOCK3", "sector": "BANK", "final_score": 75.0, "entry_price": 200.0, "stop_loss": 190.0},
    ]
    cfg = {"max_positions": 2, "max_sector_exposure_pct": 30.0, "risk_per_trade_pct": 0.50}
    selected = filter_portfolio_selection(candidates, total_capital=1000000.0, cfg=cfg)

    assert len(selected) <= 2
    assert selected[0]["symbol"] == "STOCK1"


def test_momentum_chatgpt_engine_pipeline():
    engine = MomentumChatGPTEngine(config={
        "momentum_chatgpt": {
            "min_score": 40,
            "min_history_bars": 50,
            "min_turnover_20d_crore": 0.1,
            "min_price": 10.0,
        }
    })
    stock_dfs = {"STOCK1": _synthetic_ohlcv(bars=350, base_price=500.0, trend=1.0)}
    bm_df = _synthetic_ohlcv(bars=350, base_price=22000.0, trend=5.0)

    res = engine.run_scan(stock_dfs=stock_dfs, benchmark_df=bm_df)
    assert res["strategy_id"] == "momentum_chatgpt"
    assert "market_regime" in res
    assert isinstance(res["selected_portfolio"], list)
