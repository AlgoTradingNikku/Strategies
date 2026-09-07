import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_combos import generate_strategy_combos


def _cfg():
    return {
        "options": {"strike_gap": 50},
        "trading": {"min_grade": "B", "min_score": 60, "options": {"quantity": 65}},
        "strategy_combos": {
            "enabled": True,
            "enabled_types": ["bull_call_spread", "bear_put_spread", "long_straddle", "long_strangle"],
            "max_candidates": 10,
            "vertical_width_steps": 1,
            "hedge_price_pct": 0.40,
        },
    }


def test_generates_vertical_spreads_from_buy_signals():
    scan = {
        "buy_results": [
            {"symbol": "NIFTY01SEP2624500CE", "underlying": "NIFTY", "expiry": "01SEP26", "option_type": "CE", "strike": 24500, "price": 100, "grade": "A", "setup_score": 82, "signal_type": "BUY", "lot_size": 65},
            {"symbol": "NIFTY01SEP2624500PE", "underlying": "NIFTY", "expiry": "01SEP26", "option_type": "PE", "strike": 24500, "price": 90, "grade": "B", "setup_score": 72, "signal_type": "BUY", "lot_size": 65},
        ],
        "sell_results": [],
    }

    combos = generate_strategy_combos(scan, _cfg())
    types = {c["combo_type"] for c in combos}

    assert "bull_call_spread" in types
    assert "bear_put_spread" in types
    bull = next(c for c in combos if c["combo_type"] == "bull_call_spread")
    assert bull["max_loss"] == 3900.0  # (100 - 40) * 65
    assert bull["max_profit"] == 0.0   # 50-point width, 60-point debit => capped/no edge but visible
    assert len(bull["legs"]) == 2
    assert bull["legs"][0]["action"] == "BUY"
    assert bull["legs"][1]["action"] == "SELL"


def test_quality_filters_block_low_grade_candidates():
    scan = {
        "buy_results": [
            {"symbol": "NIFTY01SEP2624500CE", "underlying": "NIFTY", "expiry": "01SEP26", "option_type": "CE", "strike": 24500, "price": 100, "grade": "C", "setup_score": 55, "signal_type": "BUY", "lot_size": 65},
        ],
        "sell_results": [],
    }

    assert generate_strategy_combos(scan, _cfg()) == []
