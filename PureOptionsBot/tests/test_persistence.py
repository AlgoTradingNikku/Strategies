"""
Test suite for Trade Persistence (Database Operations).

Tests SQLite database operations, crash recovery, and data integrity.

Run with: pytest tests/test_persistence.py -v
"""

import pytest
import os
import tempfile
from datetime import datetime
from core.persistence import TradePersistence
from core.state_machine import Trade, TradeState


class TestTradePersistence:
    """Test TradePersistence database operations"""
    
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing"""
        # Create temp file
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        # Initialize persistence
        persistence = TradePersistence(db_path=path)
        
        yield persistence
        
        # Cleanup
        persistence.close()
        if os.path.exists(path):
            os.remove(path)
    
    def test_database_initialization(self, temp_db):
        """Test database and tables are created"""
        # Should not raise any errors
        assert temp_db is not None
        
        # Try a simple query
        trades = temp_db.load_active_trades()
        assert isinstance(trades, list)
        assert len(trades) == 0
    
    def test_save_trade(self, temp_db):
        """Test saving a trade to database"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        temp_db.save_trade(trade)
        
        # Load and verify
        loaded_trades = temp_db.load_active_trades()
        assert len(loaded_trades) == 1
        assert loaded_trades[0].symbol == "NIFTY20JAN2625700CE"
        assert loaded_trades[0].entry_price == 50.0
    
    def test_save_trade_updates_existing(self, temp_db):
        """Test saving existing trade updates it"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=50.0
        )
        
        # Save initial
        temp_db.save_trade(trade)
        
        # Update price
        updated_trade = trade.update_price(55.0)
        temp_db.save_trade(updated_trade)
        
        # Should still be only 1 trade
        loaded_trades = temp_db.load_active_trades()
        assert len(loaded_trades) == 1
        assert loaded_trades[0].current_price == 55.0
    
    def test_load_active_trades_only(self, temp_db):
        """Test loading only returns active trades"""
        # Save an active trade
        active_trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        temp_db.save_trade(active_trade)
        
        # Save an exited trade
        exited_trade = Trade(
            symbol="NIFTY20JAN2625700PE",
            entry_price=50.0,
            quantity=50,
            side="PUT",
            entry_time=datetime.now(),
            state=TradeState.EXITED
        )
        temp_db.save_trade(exited_trade)
        
        # Should only load active
        loaded = temp_db.load_active_trades()
        assert len(loaded) == 1
        assert loaded[0].symbol == "NIFTY20JAN2625700CE"
    
    def test_archive_trade(self, temp_db):
        """Test archiving a trade"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        # Save active trade
        temp_db.save_trade(trade)
        assert len(temp_db.load_active_trades()) == 1
        
        # Archive it
        temp_db.archive_trade(trade)
        
        # Should no longer be in active trades
        assert len(temp_db.load_active_trades()) == 0
    
    def test_delete_trade(self, temp_db):
        """Test deleting a trade"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        temp_db.save_trade(trade)
        assert len(temp_db.load_active_trades()) == 1
        
        # Delete
        temp_db.delete_trade(trade.symbol)
        
        assert len(temp_db.load_active_trades()) == 0
    
    def test_get_trade(self, temp_db):
        """Test retrieving specific trade"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        temp_db.save_trade(trade)
        
        # Get by symbol
        loaded = temp_db.get_trade("NIFTY20JAN2625700CE")
        assert loaded is not None
        assert loaded.symbol == "NIFTY20JAN2625700CE"
        
        # Get non-existent
        not_found = temp_db.get_trade("NONEXISTENT")
        assert not_found is None
    
    def test_get_history(self, temp_db):
        """Test retrieving historical trades"""
        # Save multiple exited trades
        for i in range(5):
            trade = Trade(
                symbol=f"TEST{i}",
                entry_price=50.0 + i,
                quantity=50,
                side="CALL",
                entry_time=datetime.now(),
                state=TradeState.EXITED,
                exit_time=datetime.now()
            )
            temp_db.save_trade(trade)
        
        # Get history
        history = temp_db.get_history(days=7)
        assert len(history) == 5
        assert all(isinstance(h, dict) for h in history)
    
    def test_multiple_trades(self, temp_db):
        """Test handling multiple active trades"""
        trades = []
        for i in range(3):
            trade = Trade(
                symbol=f"NIFTY20JAN2625{700+i*100}CE",
                entry_price=50.0 + i,
                quantity=50,
                side="CALL",
                entry_time=datetime.now(),
                state=TradeState.POSITION
            )
            trades.append(trade)
            temp_db.save_trade(trade)
        
        # Load all
        loaded = temp_db.load_active_trades()
        assert len(loaded) == 3
        
        # Verify symbols
        symbols = {t.symbol for t in loaded}
        expected = {t.symbol for t in trades}
        assert symbols == expected
    
    def test_context_manager(self):
        """Test using persistence with context manager"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        try:
            with TradePersistence(db_path=path) as persistence:
                trade = Trade(
                    symbol="TEST",
                    entry_price=50.0,
                    quantity=50,
                    side="CALL",
                    entry_time=datetime.now(),
                    state=TradeState.POSITION
                )
                persistence.save_trade(trade)
                
                loaded = persistence.load_active_trades()
                assert len(loaded) == 1
            
            # Connection should be closed after context
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestCrashRecovery:
    """Test crash recovery scenarios"""
    
    @pytest.fixture
    def persistent_db_path(self):
        """Create a persistent DB path for recovery tests"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        # Cleanup after all tests
        if os.path.exists(path):
            os.remove(path)
    
    def test_recover_after_crash(self, persistent_db_path):
        """Test recovering trades after simulated crash"""
        # Simulate bot session 1
        persistence1 = TradePersistence(db_path=persistent_db_path)
        
        trade1 = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION,
            current_price=52.0,
            tsl_level=48.0
        )
        persistence1.save_trade(trade1)
        persistence1.close()
        
        # Simulate bot restart (session 2)
        persistence2 = TradePersistence(db_path=persistent_db_path)
        
        recovered = persistence2.load_active_trades()
        assert len(recovered) == 1
        assert recovered[0].symbol == "NIFTY20JAN2625700CE"
        assert recovered[0].current_price == 52.0
        assert recovered[0].tsl_level == 48.0
        
        persistence2.close()
    
    def test_recover_multiple_trades(self, persistent_db_path):
        """Test recovering multiple trades"""
        persistence1 = TradePersistence(db_path=persistent_db_path)
        
        # Save 3 active trades
        for i in range(3):
            trade = Trade(
                symbol=f"TRADE{i}",
                entry_price=50.0 + i,
                quantity=50,
                side="CALL",
                entry_time=datetime.now(),
                state=TradeState.POSITION
            )
            persistence1.save_trade(trade)
        
        persistence1.close()
        
        # Recover
        persistence2 = TradePersistence(db_path=persistent_db_path)
        recovered = persistence2.load_active_trades()
        
        assert len(recovered) == 3
        symbols = {t.symbol for t in recovered}
        assert symbols == {"TRADE0", "TRADE1", "TRADE2"}
        
        persistence2.close()
    
    def test_no_duplicate_recovery(self, persistent_db_path):
        """Test trades aren't duplicated on recovery"""
        persistence1 = TradePersistence(db_path=persistent_db_path)
        
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        # Save same trade multiple times (updates)
        for _ in range(5):
            persistence1.save_trade(trade)
        
        persistence1.close()
        
        # Should still be only 1 trade
        persistence2 = TradePersistence(db_path=persistent_db_path)
        recovered = persistence2.load_active_trades()
        assert len(recovered) == 1
        
        persistence2.close()


class TestDataIntegrity:
    """Test data integrity and edge cases"""
    
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        persistence = TradePersistence(db_path=path)
        yield persistence
        persistence.close()
        if os.path.exists(path):
            os.remove(path)
    
    def test_save_trade_with_all_fields(self, temp_db):
        """Test saving trade with all optional fields populated"""
        trade = Trade(
            symbol="NIFTY20JAN2625700CE",
            state=TradeState.POSITION,
            side="CALL",
            entry_price=50.0,
            current_price=52.0,
            highest_price=53.0,
            lowest_price=50.0,
            quantity=50,
            entry_time=datetime.now(),
            atr=2.5,
            tsl_level=48.0,
            last_stage="G1",
            obs_candles=2,
            obs_start_time=datetime.now(),
            idx_at_resolution=25000.0,
            expiry_params={'expiry': 'WEEKLY', 'offset': 0},
            metadata={'signal_type': 'explosive'},
            trend_reversed=False,
            manual_exit_pending=False
        )
        
        temp_db.save_trade(trade)
        loaded = temp_db.get_trade(trade.symbol)
        
        assert loaded.atr == 2.5
        assert loaded.tsl_level == 48.0
        assert loaded.last_stage == "G1"
        assert loaded.highest_price == 53.0
        assert loaded.lowest_price == 50.0
        assert loaded.obs_candles == 2
        assert loaded.idx_at_resolution == 25000.0
        assert loaded.expiry_params == {'expiry': 'WEEKLY', 'offset': 0}
        assert loaded.metadata == {'signal_type': 'explosive'}
        assert loaded.trend_reversed == False
        assert loaded.manual_exit_pending == False
    
    def test_unicode_symbol_names(self, temp_db):
        """Test handling unicode characters in symbols"""
        trade = Trade(
            symbol="निफ़्टी_TEST",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.POSITION
        )
        
        temp_db.save_trade(trade)
        loaded = temp_db.get_trade("निफ़्टी_TEST")
        
        assert loaded is not None
        assert loaded.symbol == "निफ़्टी_TEST"
    
    def test_very_long_exit_reason(self, temp_db):
        """Test handling very long exit reasons"""
        long_reason = "X" * 1000  # Very long string
        
        trade = Trade(
            symbol="TEST",
            entry_price=50.0,
            quantity=50,
            side="CALL",
            entry_time=datetime.now(),
            state=TradeState.EXITED,
            exit_reason=long_reason
        )
        
        temp_db.save_trade(trade)
        loaded = temp_db.get_trade("TEST")
        
        assert loaded.exit_reason == long_reason


if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v", "--tb=short"])
