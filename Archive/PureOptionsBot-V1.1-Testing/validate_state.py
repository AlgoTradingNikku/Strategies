"""
Tests for state machine and persistence.

Run with: python validate_state.py
"""

import sys
sys.path.insert(0, '.')

from datetime import datetime
from core.state_machine import Trade, TradeState, TradeStateMachine
from core.persistence import TradePersistence
from risk.manager import RiskManager, ExitReason


def test_trade_creation():
    """Test creating a trade"""
    print("Testing Trade creation...")
    
    trade = Trade(
        symbol="NIFTY24JAN25500CE",
        state=TradeState.IDLE,
        side="CALL"
    )
    
    assert trade.symbol == "NIFTY24JAN25500CE"
    assert trade.state == TradeState.IDLE
    assert trade.pnl == 0.0
    print("  [OK] Trade created successfully")


def test_state_transitions():
    """Test valid and invalid state transitions"""
    print("\nTesting State Transitions...")
    
    trade = Trade(symbol="TEST", state=TradeState.IDLE)
    
    # Valid: IDLE -> OBSERVING
    trade = TradeStateMachine.transition(trade, TradeState.OBSERVING)
    assert trade.state == TradeState.OBSERVING
    assert trade.obs_start_time is not None
    print("  [OK] IDLE -> OBSERVING")
    
    # Valid: OBSERVING -> ENTERING
    trade = TradeStateMachine.transition(trade, TradeState.ENTERING)
    assert trade.state == TradeState.ENTERING
    print("  [OK] OBSERVING -> ENTERING")
    
    # Valid: ENTERING -> POSITION
    trade = TradeStateMachine.transition(trade, TradeState.POSITION)
    assert trade.state == TradeState.POSITION
    assert trade.entry_time is not None
    print("  [OK] ENTERING -> POSITION")
    
    # Valid: POSITION -> EXITING
    trade = TradeStateMachine.transition(trade, TradeState.EXITING)
    assert trade.state == TradeState.EXITING
    print("  [OK] POSITION -> EXITING")
    
    # Valid: EXITING -> EXITED
    trade = TradeStateMachine.transition(trade, TradeState.EXITED, "TSL Hit")
    assert trade.state == TradeState.EXITED
    assert trade.exit_time is not None
    assert trade.exit_reason == "TSL Hit"
    print("  [OK] EXITING -> EXITED")
    
    # Invalid: Try EXITED -> POSITION (should fail)
    try:
        TradeStateMachine.transition(trade, TradeState.POSITION)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"  [OK] Invalid transition blocked: {e}")


def test_pnl_calculation():
    """Test P&L calculation"""
    print("\nTesting P&L Calculation...")
    
    trade = Trade(
        symbol="TEST",
        state=TradeState.POSITION,
        entry_price=100.0,
        current_price=105.0,
        quantity=50
    )
    
    pnl, pnl_pct = trade.calculate_pnl()
    assert pnl == 250.0  # (105-100) * 50
    assert pnl_pct == 5.0  # 5%
    print(f"  [OK] P&L calculated: Rs.{pnl:.2f} ({pnl_pct:.2f}%)")
    
    # Update price
    trade = trade.update_price(110.0)
    assert trade.current_price == 110.0
    assert trade.highest_price == 110.0
    pnl, pnl_pct = trade.calculate_pnl()
    print(f"  [OK] After price update: Rs.{pnl:.2f} ({pnl_pct:.2f}%)")


def test_persistence():
    """Test SQLite persistence"""
    print("\nTesting Persistence...")
    
    # Create test database
    import os
    db_file = "test_bot_state.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    
    persistence = TradePersistence(db_file)
    
    # Create and save a trade
    trade = Trade(
        symbol="NIFTY24JAN25500CE",
        state=TradeState.POSITION,
        side="CALL",
        entry_price=200.0,
        current_price=210.0,
        quantity=75,
        entry_time=datetime.now(),
        atr=15.0
    )
    
    persistence.save_trade(trade)
    print("  [OK] Trade saved to database")
    
    # Load it back
    loaded = persistence.get_trade("NIFTY24JAN25500CE")
    assert loaded is not None
    assert loaded.symbol == trade.symbol
    assert loaded.entry_price == trade.entry_price
    print("  [OK] Trade loaded from database")
    
    # Test crash recovery (load all active trades)
    active_trades = persistence.load_active_trades()
    assert len(active_trades) == 1
    assert active_trades[0].symbol == "NIFTY24JAN25500CE"
    print("  [OK] Crash recovery works")
    
    # Archive trade (transition to EXITED first)
    exit_trade = TradeStateMachine.transition(trade, TradeState.EXITING)
    exit_trade = TradeStateMachine.transition(exit_trade, TradeState.EXITED, "TSL Hit")
    exit_trade = Trade(**{
        **exit_trade.__dict__,
        "pnl": 750.0
    })
    persistence.archive_trade(exit_trade)
    print("  [OK] Trade archived to history")
    
    # Verify it's removed from active trades
    active_trades = persistence.load_active_trades()
    assert len(active_trades) == 0
    print("  [OK] Archived trade removed from active")
    
    # Verify history
    history = persistence.get_history(days=1)
    assert len(history) >= 1
    print("  [OK] Trade history retrieved")
    
    persistence.close()
    
    # Cleanup
    if os.path.exists(db_file):
        os.remove(db_file)


def test_risk_manager():
    """Test risk manager"""
    print("\nTesting Risk Manager...")
    
    config = {
        "tsl": {
            "mode": "PERCENT",
            "trail_pct": 2.0,
            "enable_profit_guard": True,
            "guard_1_pct": 1.5,
            "guard_1_trail": 1.0,
            "guard_2_pct": 3.0,
            "guard_2_trail": 2.0,
            "guard_3_pct": 5.0,
            "guard_3_trail": 3.0,
        },
        "exit_on_reversal": True,
        "enable_time_based_exit": False
    }
    
    risk_mgr = RiskManager(config)
    
    # Create a trade in profit
    trade = Trade(
        symbol="TEST",
        state=TradeState.POSITION,
        entry_price=100.0,
        current_price=106.0,  # +6%
        highest_price=106.0,
        quantity=50,
        entry_time=datetime.now(),
        atr=5.0,
        last_stage="INIT"
    )
    
    # Should trigger Guard 3 (>5%)
    decision = risk_mgr.evaluate(trade, current_price=106.0)
    assert not decision.should_exit  # Not hit yet
    assert "G3" in decision.new_stage
    print(f"  [OK] Guard 3 active: TSL={decision.new_tsl_level:.2f}")
    
    # Drop to TSL level
    tsl = decision.new_tsl_level
    decision = risk_mgr.evaluate(trade, current_price=tsl - 0.1)
    assert decision.should_exit
    assert decision.reason == ExitReason.TSL_HIT
    print(f"  [OK] TSL exit triggered: {decision.message}")
    
    # Test trend reversal
    decision = risk_mgr.evaluate(trade, current_price=105.0, is_trend_reversed=True)
    assert decision.should_exit
    assert decision.reason == ExitReason.TREND_REVERSAL
    print("  [OK] Trend reversal exit triggered")


if __name__ == "__main__":
    print("=" * 60)
    print("PureOptionsBot - State Management Validation")
    print("=" * 60)
    
    try:
        test_trade_creation()
        test_state_transitions()
        test_pnl_calculation()
        test_persistence()
        test_risk_manager()
        
        print("\n" + "=" * 60)
        print("[SUCCESS] ALL STATE MANAGEMENT TESTS PASSED!")
        print("=" * 60)
        print("\nPhase 2 components working correctly:")
        print("  1. Trade dataclass with immutable pattern")
        print("  2. State machine with transition validation")
        print("  3. SQLite crash recovery")
        print("  4. Risk manager with 3-stage profit guard")
        
    except Exception as e:
        print(f"\n[FAILED] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


