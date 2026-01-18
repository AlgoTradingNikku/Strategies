"""
Test suite for Trade State Machine.

Tests trade lifecycle, state transitions, and business logic validations.

Run with: pytest tests/test_state_machine.py -v
"""

import pytest
from datetime import datetime, timedelta
from core.state_machine import Trade, TradeState, TradeStateMachine


class TestTradeDataClass:
    """Test Trade dataclass functionality"""
    
    def test_trade_creation(self):
        """Test creating a basic trade"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            state=TradeState.OBSERVING,
            side="CALL",
            entry_price=50.0,
            current_price=50.0,
            quantity=50,
            entry_time=datetime.now()
        )
        
        assert trade.symbol == "NIFTY20JAN2625700CE"
        assert trade.entry_price == 50.0
        assert trade.quantity == 50
        assert trade.side == "CALL"
        assert trade.state == TradeState.OBSERVING
        assert trade.highest_price == 0.0  # Default value
        assert trade.lowest_price == 0.0
    
    def test_calculate_pnl_call_profit(self):
        """Test P&L calculation for profitable CALL"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=60.0
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        
        assert pnl == 500.0  # (60 - 50) * 50
        assert pnl_pct == 20.0  # 10/50 * 100
    
    def test_calculate_pnl_call_loss(self):
        """Test P&L calculation for losing CALL"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=45.0
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        
        assert pnl == -250.0  # (45 - 50) * 50
        assert pnl_pct == -10.0
    
    def test_calculate_pnl_put_profit(self):
        """Test P&L calculation for profitable PUT"""
        trade = Trade(
            symbol="NIFTY20JAN2625700PE",
            entry_price=50.0,
            quantity=50,
            side="PUT",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=40.0  # Price goes down = profit for PUT
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        
        assert pnl == 500.0  # (50 - 40) * 50
        assert pnl_pct == 20.0
    
    def test_calculate_pnl_put_loss(self):
        """Test P&L calculation for losing PUT"""
        trade = Trade(
            symbol="NIFTY20JAN2625700PE",
            entry_price=50.0,
            quantity=50,
            side="PUT",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=55.0  # Price goes up = loss for PUT
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        
        assert pnl == -250.0
        assert pnl_pct == -10.0
    
    def test_is_profitable(self):
        """Test profitable trade detection"""
        profitable_trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=55.0
        )
        
        losing_trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=45.0
        )
        
        assert profitable_trade.is_profitable()
        assert not losing_trade.is_profitable()
    
    def test_update_price(self):
        """Test price update creates new trade object"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=50.0
        )
        
        updated_trade = trade.update_price(55.0)
        
        # Original trade unchanged (immutable pattern)
        assert trade.current_price == 50.0
        # New trade has updated price
        assert updated_trade.current_price == 55.0
        # Other fields preserved
        assert updated_trade.entry_price == 50.0
        assert updated_trade.symbol == trade.symbol
    
    def test_to_dict(self):
        """Test serialization to dictionary"""
        now = datetime.now()
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=now,
            state=TradeState.POSITION,
            sl=45.0,
            target=60.0
        )
        
        data = trade.to_dict()
        
        assert data['symbol'] == "NIFTY20JAN2625700CE"
        assert data['entry_price'] == 50.0
        assert data['state'] == 'POSITION'
        assert 'entry_time' in data
    
    def test_from_dict(self):
        """Test deserialization from dictionary"""
        now = datetime.now()
        data = {
            'symbol': 'NIFTY20JAN2625700CE',
            'entry_price': 50.0,
            'quantity': 50,
            'side': 'CALL',
            'entry_time': now.isoformat(),
            'state': 'POSITION',
            'sl': 45.0,
            'target': 60.0,
            'current_price': 52.0,
            'tsl_level': 48.0,
            'last_stage': 1,
            'pnl': 0.0,
            'pnl_pct': 0.0,
            'exit_time': None,
            'exit_reason': ''
        }
        
        trade = Trade.from_dict(data)
        
        assert trade.symbol == 'NIFTY20JAN2625700CE'
        assert trade.entry_price == 50.0
        assert trade.state == TradeState.POSITION
        assert trade.sl == 45.0


class TestTradeStateMachine:
    """Test state machine transitions and validations"""
    
    def test_valid_transition_observing_to_position(self):
        """Test valid transition from OBSERVING to POSITION"""
        assert TradeStateMachine.can_transition(
            TradeState.OBSERVING,
            TradeState.POSITION
        )
    
    def test_valid_transition_position_to_exiting(self):
        """Test valid transition from POSITION to EXITING"""
        assert TradeStateMachine.can_transition(
            TradeState.POSITION,
            TradeState.EXITING
        )
    
    def test_valid_transition_exiting_to_exited(self):
        """Test valid transition from EXITING to EXITED"""
        assert TradeStateMachine.can_transition(
            TradeState.EXITING,
            TradeState.EXITED
        )
    
    def test_invalid_transition_observing_to_exited(self):
        """Test invalid direct jump from OBSERVING to EXITED"""
        assert not TradeStateMachine.can_transition(
            TradeState.OBSERVING,
            TradeState.EXITED
        )
    
    def test_invalid_transition_exited_to_position(self):
        """Test cannot re-enter from EXITED state"""
        assert not TradeStateMachine.can_transition(
            TradeState.EXITED,
            TradeState.POSITION
        )
    
    def test_transition_updates_state(self):
        """Test transition creates new trade with updated state"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.OBSERVING
        )
        
        new_trade = TradeStateMachine.transition(
            trade,
            TradeState.POSITION,
            reason="Entry signal confirmed"
        )
        
        # Original unchanged
        assert trade.state == TradeState.OBSERVING
        # New trade updated
        assert new_trade.state == TradeState.POSITION
    
    def test_transition_to_exited_sets_exit_time(self):
        """Test transitioning to EXITED sets exit timestamp"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.EXITING
        )
        
        exited_trade = TradeStateMachine.transition(
            trade,
            TradeState.EXITED,
            reason="Target reached"
        )
        
        assert exited_trade.exit_time is not None
        assert exited_trade.exit_reason == "Target reached"
        assert isinstance(exited_trade.exit_time, datetime)
    
    def test_get_valid_transitions_idle(self):
        """Test getting valid next states from IDLE"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.IDLE)
        
        assert TradeState.OBSERVING in valid
        assert TradeState.BLOCKED in valid
    
    def test_get_valid_transitions_observing(self):
        """Test getting valid next states from OBSERVING"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.OBSERVING)
        
        assert TradeState.ENTERING in valid
        assert TradeState.IDLE in valid
        assert TradeState.BLOCKED in valid
    
    def test_get_valid_transitions_entering(self):
        """Test getting valid next states from ENTERING"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.ENTERING)
        
        assert TradeState.POSITION in valid
        assert TradeState.IDLE in valid
    
    def test_get_valid_transitions_position(self):
        """Test getting valid next states from POSITION"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.POSITION)
        
        assert TradeState.EXITING in valid
        assert TradeState.BLOCKED in valid
    
    def test_get_valid_transitions_exited(self):
        """Test EXITED can go back to IDLE or BLOCKED"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.EXITED)
        
        assert TradeState.IDLE in valid
        assert TradeState.BLOCKED in valid
    
    def test_get_valid_transitions_blocked(self):
        """Test BLOCKED can only go to IDLE"""
        valid = TradeStateMachine.get_valid_transitions(TradeState.BLOCKED)
        
        assert TradeState.IDLE in valid
        assert len(valid) == 1
    
    def test_invalid_transition_raises_error(self):
        """Test attempting invalid transition raises ValueError"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.EXITED
        )
        
        with pytest.raises(ValueError, match="Invalid state transition"):
            TradeStateMachine.transition(trade, TradeState.POSITION)


class TestTradeLifecycle:
    """Test complete trade lifecycle scenarios"""
    
    def test_successful_trade_lifecycle(self):
        """Test complete lifecycle of a successful trade"""
        # 1. Start observing
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.OBSERVING
        )
        assert trade.state == TradeState.OBSERVING
        
        # 2. Enter position
        trade = TradeStateMachine.transition(trade, TradeState.POSITION)
        assert trade.state == TradeState.POSITION
        
        # 3. Update price (profitable)
        trade = trade.update_price(60.0)
        assert trade.is_profitable()
        
        # 4. Start exiting
        trade = TradeStateMachine.transition(trade, TradeState.EXITING)
        assert trade.state == TradeState.EXITING
        
        # 5. Complete exit
        trade = TradeStateMachine.transition(
            trade,
            TradeState.EXITED,
            reason="Target reached"
        )
        assert trade.state == TradeState.EXITED
        assert trade.exit_time is not None
        assert trade.exit_reason == "Target reached"
    
    def test_cancelled_trade_lifecycle(self):
        """Test lifecycle of a cancelled trade"""
        # 1. Start observing
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.OBSERVING
        )
        
        # 2. Cancel before entry
        trade = TradeStateMachine.transition(
            trade,
            TradeState.CANCELLED,
            reason="Signal invalidated"
        )
        assert trade.state == TradeState.CANCELLED
        assert trade.exit_reason == "Signal invalidated"
    
    def test_stopped_out_trade_lifecycle(self):
        """Test lifecycle of a stopped out trade"""
        # Enter position
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            sl=45.0
        )
        
        # Price drops below SL
        trade = trade.update_price(44.0)
        assert not trade.is_profitable()
        
        # Exit with loss
        trade = TradeStateMachine.transition(trade, TradeState.EXITING)
        trade = TradeStateMachine.transition(
            trade,
            TradeState.EXITED,
            reason="Stop loss hit"
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        assert pnl < 0
        assert trade.exit_reason == "Stop loss hit"


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_zero_quantity_trade(self):
        """Test trade with zero quantity"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=0,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        assert pnl == 0.0
    
    def test_zero_entry_price(self):
        """Test trade with zero entry price"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=0.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=50.0
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        # With zero entry, percentage calculation should handle gracefully
        assert pnl != 0.0  # Should calculate difference
    
    def test_none_current_price(self):
        """Test trade with None current price"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=None
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        # Should use entry price if current is None
        assert pnl == 0.0
        assert pnl_pct == 0.0


if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v", "--tb=short"])
