"""
Test suite for Risk Manager.

Tests trailing stop loss, exit decisions, P&L tracking, and daily limits.

Run with: pytest tests/test_risk_manager.py -v
"""

import pytest
from datetime import datetime, timedelta
from risk.manager import RiskManager, TrailingStopManager, ExitReason, RiskDecision
from core.state_machine import Trade, TradeState


class TestRiskManagerInitialization:
    """Test RiskManager initialization"""
    
    def test_initialization_with_config(self, sample_config):
        """Test initialization with full config"""
        sample_config['tsl'] = {
            'mode': 'PERCENT',
            'trail_pct': 2.0,
            'enable_profit_guard': True
        }
        
        manager = RiskManager(sample_config)
        assert manager is not None
        assert manager.tsl_manager is not None
    
    def test_initialization_with_atr_tsl(self, sample_config):
        """Test initialization with ATR-based TSL"""
        sample_config['tsl'] = {
            'mode': 'ATR',
            'atr_multiplier': 1.5
        }
        
        manager = RiskManager(sample_config)
        assert manager is not None
        assert manager.tsl_manager.mode == 'ATR'
    
    def test_update_config(self, sample_config):
        """Test updating config dynamically"""
        sample_config['tsl'] = {'mode': 'PERCENT', 'trail_pct': 2.0}
        manager = RiskManager(sample_config)
        
        new_config = sample_config.copy()
        new_config['tsl'] = {'mode': 'PERCENT', 'trail_pct': 3.0}
        
        manager.update_config(new_config)
        # Should not raise errors


class TestPercentTSL:
    """Test percent-based trailing stop loss"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager with percent TSL"""
        sample_config['risk'] = {
            'tsl_mode': 'PERCENT',
            'tsl_percent': 20,  # Trail 20% below high
            'target_percent': 50,
            'max_loss_per_trade': 500,
            'max_daily_loss': 2000
        }
        return RiskManager(sample_config)
    
    def test_no_exit_when_price_rising(self, risk_manager, sample_trade):
        """Test no exit when price is rising"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=52.0,
            tsl_level=48.0
        )
        
        decision = risk_manager.evaluate(sample_trade, 52.0)
        
        assert not decision.should_exit
        assert decision.new_tsl_level > 0  # TSL should update
    
    def test_exit_when_tsl_hit(self, risk_manager, sample_trade):
        """Test exit when TSL is breached"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=55.0,
            tsl_level=52.0  # TSL at 52
        )
        
        # Price drops below TSL
        decision = risk_manager.evaluate(sample_trade, 51.0)
        
        assert decision.should_exit
        assert decision.reason == ExitReason.TSL_HIT
    
    def test_tsl_moves_up_with_profit(self, risk_manager, sample_trade):
        """Test TSL moves up as profit increases"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=60.0,
            tsl_level=48.0
        )
        
        # Price at 60 = 20% profit
        # TSL should be: 60 - (60 * 0.20) = 48
        decision = risk_manager.evaluate(sample_trade, 60.0)
        
        assert decision.new_tsl_level > sample_trade.tsl_level
        assert not decision.should_exit
    
    def test_tsl_doesnt_move_down(self, risk_manager, sample_trade):
        """Test TSL doesn't move down (ratchet effect)"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=60.0,
            tsl_level=55.0  # High water mark
        )
        
        # Price drops to 58
        decision = risk_manager.evaluate(sample_trade, 58.0)
        
        # TSL should stay at 55, not drop
        assert decision.new_tsl_level >= 55.0
    
    def test_target_hit(self, risk_manager, sample_trade):
        """Test exit when target is reached"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=75.0,  # 50% profit
            target=75.0
        )
        
        decision = risk_manager.evaluate(sample_trade, 75.0)
        
        assert decision.should_exit
        assert decision.reason == ExitReason.TARGET_HIT


class TestStopLoss:
    """Test stop loss functionality"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager"""
        sample_config['risk'] = {
            'tsl_mode': 'PERCENT',
            'tsl_percent': 20,
            'max_loss_per_trade': 500
        }
        return RiskManager(sample_config)
    
    def test_stop_loss_hit(self, risk_manager, sample_trade):
        """Test exit when stop loss is hit"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=44.0,
            sl=45.0
        )
        
        decision = risk_manager.evaluate(sample_trade, 44.0)
        
        assert decision.should_exit
        assert decision.reason == ExitReason.STOP_LOSS
    
    def test_max_loss_per_trade(self, risk_manager, sample_trade):
        """Test exit when max loss limit reached"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            quantity=50,
            current_price=40.0  # Loss = 500
        )
        
        decision = risk_manager.evaluate(sample_trade, 40.0)
        
        assert decision.should_exit
        # Should hit max loss
        pnl, _ = sample_trade.calculate_pnl()
        assert abs(pnl) >= 500


class TestTrendReversal:
    """Test trend reversal exit logic"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager"""
        return RiskManager(sample_config)
    
    def test_exit_on_trend_reversal(self, risk_manager, sample_trade):
        """Test exit when trend reverses"""
        sample_trade = sample_trade._replace(
            side="CALL",
            entry_price=50.0,
            current_price=52.0
        )
        
        # Signal trend reversal
        decision = risk_manager.evaluate(
            sample_trade,
            52.0,
            is_trend_reversed=True
        )
        
        assert decision.should_exit
        assert decision.reason == ExitReason.TREND_REVERSAL
    
    def test_no_exit_when_trend_continues(self, risk_manager, sample_trade):
        """Test no exit when trend continues"""
        sample_trade = sample_trade._replace(
            side="CALL",
            entry_price=50.0,
            current_price=52.0
        )
        
        decision = risk_manager.evaluate(
            sample_trade,
            52.0,
            is_trend_reversed=False
        )
        
        assert not decision.should_exit


class TestDailyLimits:
    """Test daily loss and trade limits"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager with daily limits"""
        sample_config['risk'] = {
            'max_daily_loss': 2000,
            'max_daily_trades': 10
        }
        return RiskManager(sample_config)
    
    def test_track_daily_pnl(self, risk_manager):
        """Test daily P&L tracking"""
        # Add some trades
        risk_manager.update_daily_pnl(-100)
        risk_manager.update_daily_pnl(-150)
        risk_manager.update_daily_pnl(50)
        
        # Total should be -200
        assert risk_manager.get_daily_pnl() == -200
    
    def test_daily_loss_limit_reached(self, risk_manager, sample_trade):
        """Test exit when daily loss limit reached"""
        # Simulate heavy losses
        risk_manager.update_daily_pnl(-2100)
        
        decision = risk_manager.evaluate(sample_trade, 50.0)
        
        assert decision.should_exit
        assert decision.reason == ExitReason.DAILY_LOSS_LIMIT
    
    def test_reset_daily_stats(self, risk_manager):
        """Test resetting daily statistics"""
        risk_manager.update_daily_pnl(-500)
        assert risk_manager.get_daily_pnl() == -500
        
        risk_manager.reset_daily_stats()
        assert risk_manager.get_daily_pnl() == 0


class TestMultiStageTSL:
    """Test multi-stage trailing stop loss"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager with staged TSL"""
        sample_config['risk'] = {
            'tsl_mode': 'STAGED',
            'stages': [
                {'profit_pct': 10, 'trail_pct': 5},
                {'profit_pct': 20, 'trail_pct': 10},
                {'profit_pct': 30, 'trail_pct': 15}
            ]
        }
        return RiskManager(sample_config)
    
    def test_stage_1_tsl(self, risk_manager, sample_trade):
        """Test stage 1 TSL (10% profit)"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=55.0,  # 10% profit
            last_stage=0
        )
        
        decision = risk_manager.evaluate(sample_trade, 55.0)
        
        # Should move to stage 1
        assert decision.new_stage == 1
        assert decision.new_tsl_level > sample_trade.tsl_level
    
    def test_stage_progression(self, risk_manager, sample_trade):
        """Test moving through TSL stages"""
        # Start at stage 1
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=60.0,  # 20% profit
            last_stage=1
        )
        
        decision = risk_manager.evaluate(sample_trade, 60.0)
        
        # Should move to stage 2
        assert decision.new_stage == 2


class TestPUTOptions:
    """Test risk management for PUT options"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager"""
        sample_config['risk'] = {
            'tsl_mode': 'PERCENT',
            'tsl_percent': 20
        }
        return RiskManager(sample_config)
    
    def test_put_profit_calculation(self, risk_manager):
        """Test P&L calculation for PUT"""
        trade = Trade(
            symbol="NIFTY20JAN2625700PE",
            entry_price=50.0,
            quantity=50,
            side="PUT",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=40.0  # Price drop = profit
        )
        
        pnl, pnl_pct = trade.calculate_pnl()
        assert pnl > 0  # Profit
        assert pnl_pct == 20.0  # 20% profit
    
    def test_put_tsl_behavior(self, risk_manager):
        """Test TSL behavior for PUT options"""
        trade = Trade(
            symbol="NIFTY20JAN2625700PE",
            entry_price=50.0,
            quantity=50,
            side="PUT",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=40.0,
            tsl_level=48.0
        )
        
        # Price rises (bad for PUT)
        decision = risk_manager.evaluate(trade, 49.0)
        
        # Should exit if above TSL
        if trade.tsl_level > 0:
            assert decision.should_exit or decision.new_tsl_level > 0


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    @pytest.fixture
    def risk_manager(self, sample_config):
        """Create risk manager"""
        return RiskManager(sample_config)
    
    def test_zero_price(self, risk_manager, sample_trade):
        """Test handling zero price"""
        decision = risk_manager.evaluate(sample_trade, 0.0)
        
        # Should not crash
        assert decision is not None
    
    def test_negative_price(self, risk_manager, sample_trade):
        """Test handling negative price"""
        decision = risk_manager.evaluate(sample_trade, -10.0)
        
        # Should handle gracefully
        assert decision is not None
    
    def test_extremely_high_profit(self, risk_manager, sample_trade):
        """Test handling 1000% profit"""
        sample_trade = sample_trade._replace(
            entry_price=50.0,
            current_price=550.0  # 1000% profit!
        )
        
        decision = risk_manager.evaluate(sample_trade, 550.0)
        
        # Should handle and potentially exit at target
        assert decision is not None
    
    def test_trade_duration_check(self, risk_manager):
        """Test checking trade duration"""
        old_trade = Trade(
            symbol="TEST",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now() - timedelta(hours=6),
            state=TradeState.POSITION,
            current_price=52.0
        )
        
        # Should potentially flag long-running trade
        decision = risk_manager.evaluate(old_trade, 52.0)
        assert decision is not None


class TestRiskDecision:
    """Test RiskDecision data class"""
    
    def test_risk_decision_creation(self):
        """Test creating risk decision for exit"""
        decision = RiskDecision(
            should_exit=True,
            reason=ExitReason.TSL_HIT,
            message="TSL Hit at 48.0",
            new_tsl_level=48.0,
            new_stage="G2"
        )
        
        assert decision.should_exit
        assert decision.reason == ExitReason.TSL_HIT
        assert decision.new_tsl_level == 48.0
        assert decision.new_stage == "G2"
    
    def test_no_exit_decision(self):
        """Test no-exit decision"""
        decision = RiskDecision(
            should_exit=False,
            reason=None,
            message="Continue holding",
            new_tsl_level=50.0,
            new_stage="TRAILING"
        )
        
        assert not decision.should_exit
        assert decision.reason is None
        assert decision.new_tsl_level == 50.0


class TestTrailingStopManager:
    """Test TrailingStopManager class"""
    
    def test_percent_tsl_calculation(self):
        """Test percent-based TSL calculation"""
        config = {
            'mode': 'PERCENT',
            'trail_pct': 2.0,
            'enable_profit_guard': False
        }
        tsl_manager = TrailingStopManager(config)
        
        tsl_level, stage = tsl_manager.calculate_tsl(
            entry_price=50.0,
            current_price=55.0,
            highest_price=55.0,
            atr=2.5,
            last_stage="INIT"
        )
        
        # TSL should be 55 * (1 - 0.02) = 53.9
        assert tsl_level == pytest.approx(53.9, rel=0.01)
        assert stage == "TRAILING"
    
    def test_profit_guard_stage_3(self):
        """Test 3-stage profit guard activation"""
        config = {
            'mode': 'PERCENT',
            'trail_pct': 2.0,
            'enable_profit_guard': True,
            'guard_3_pct': 5.0,
            'guard_3_trail': 3.0,
            'guard_2_pct': 3.0,
            'guard_2_trail': 2.0,
            'guard_1_pct': 1.5,
            'guard_1_trail': 1.0
        }
        tsl_manager = TrailingStopManager(config)
        
        # 10% profit should trigger G3
        tsl_level, stage = tsl_manager.calculate_tsl(
            entry_price=50.0,
            current_price=55.0,  # 10% profit
            highest_price=55.0,
            atr=2.5,
            last_stage="INIT"
        )
        
        # Should be in G3 stage
        assert stage == "G3"
        # TSL = 55 * (1 - 0.03) = 53.35
        assert tsl_level == pytest.approx(53.35, rel=0.01)
    
    def test_atr_tsl_calculation(self):
        """Test ATR-based TSL calculation"""
        config = {
            'mode': 'ATR',
            'atr_multiplier': 1.5,
            'enable_profit_guard': False
        }
        tsl_manager = TrailingStopManager(config)
        
        tsl_level, stage = tsl_manager.calculate_tsl(
            entry_price=50.0,
            current_price=55.0,
            highest_price=55.0,
            atr=2.5,
            last_stage="INIT"
        )
        
        # TSL = 55 - (2.5 * 1.5) = 51.25
        assert tsl_level == pytest.approx(51.25, rel=0.01)
        assert stage == "TRAILING"


if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v", "--tb=short"])
