import pytest
from trading_adapter import calculate_l1_limit_price


def test_calculate_l1_limit_price_aggressive():
    quote = {"ltp": 100.25, "bid": 100.10, "ask": 100.30}
    
    # BUY aggressive -> Best Ask
    buy_price = calculate_l1_limit_price("BUY", quote, l1_mode="AGGRESSIVE", tick_size=0.05)
    assert buy_price == 100.30

    # SELL aggressive -> Best Bid
    sell_price = calculate_l1_limit_price("SELL", quote, l1_mode="AGGRESSIVE", tick_size=0.05)
    assert sell_price == 100.10


def test_calculate_l1_limit_price_passive():
    quote = {"ltp": 100.25, "bid": 100.10, "ask": 100.30}
    
    # BUY passive -> Best Bid + 0.05
    buy_price = calculate_l1_limit_price("BUY", quote, l1_mode="PASSIVE", tick_size=0.05)
    assert buy_price == 100.15

    # SELL passive -> Best Ask - 0.05
    sell_price = calculate_l1_limit_price("SELL", quote, l1_mode="PASSIVE", tick_size=0.05)
    assert sell_price == 100.25


def test_calculate_l1_limit_price_midpoint():
    quote = {"ltp": 100.0, "bid": 100.0, "ask": 100.20}
    
    # Midpoint = (100.0 + 100.20) / 2 = 100.10
    buy_price = calculate_l1_limit_price("BUY", quote, l1_mode="MIDPOINT", tick_size=0.05)
    assert buy_price == 100.10


def test_calculate_l1_limit_price_fallback_to_ltp():
    quote = {"ltp": 1500.55, "bid": 0.0, "ask": 0.0}
    
    # When bid/ask are 0, fall back to LTP rounded to tick size
    buy_price = calculate_l1_limit_price("BUY", quote, l1_mode="AGGRESSIVE", tick_size=0.05)
    assert buy_price == 1500.55
