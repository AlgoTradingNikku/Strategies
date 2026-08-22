import sqlite3
import time
from trade_manager import PositionMonitor
import trade_db

def test_trade_management():
    print("Initializing test configuration...")
    # Mock Config
    config = {
        "openalgo": {
            "apikey": "testkey",
            "base_url": "http://127.0.0.1:5000",
            "ws_url": "ws://127.0.0.1:8765"
        },
        "trade_management": {
            "enabled": True,
            "poll_interval_seconds": 1,
            "stop_loss_pct": 1.0,
            "target_pct": 2.0,
            "partial_exit": {
                "enabled": True,
                "target1_pct": 0.5,
                "exit_qty_fraction": 0.5,
                "move_sl_to_breakeven": True
            },
            "trailing_sl": {
                "enabled": True,
                "activation_pct": 0.8,
                "distance_pct": 0.3
            },
            "profit_lock": {
                "enabled": True,
                "threshold_pct": 1.2,
                "lock_fraction": 0.50
            },
            "notifications": {
                "on_sl_move": False,
                "on_profit_lock": False,
                "on_exit": False
            }
        }
    }

    # Initialize schema first
    trade_db._get_connection()

    # Clean previous DB records for testing
    conn = sqlite3.connect("trades.db")
    conn.execute("DELETE FROM positions")
    conn.execute("DELETE FROM position_events")
    conn.commit()
    conn.close()

    print("Starting PositionMonitor...")
    monitor = PositionMonitor()
    
    # We won't start background loop to keep test deterministic/sync
    monitor.config = config
    monitor.running = True

    # 1. Register a Mock BUY position
    print("\n--- Test 1: Registering Position ---")
    mock_order_result = {"status": "success", "orderid": "mock_12345", "order": {"price": 1000.0}}
    
    class MockOrderRequest:
        symbol = "MOCK_INFY"
        exchange = "NSE"
        action = "BUY"
        quantity = 10
        price_type = "LIMIT"
        price = 1000.0
        product = "MIS"

    req = MockOrderRequest()
    monitor.open_position(mock_order_result, req, config)

    # Fetch registered position
    pos_id = list(monitor.active_positions.keys())[0]
    pos = monitor.active_positions[pos_id]
    print(f"Registered position: ID={pos['id']}, Symbol={pos['symbol']}, Entry={pos['entry_price']}, SL={pos['current_sl']}, Target={pos['target_price']}")

    # Mock trading_adapter behavior for exit executions — patch inside trade_manager module
    import trade_manager as tm
    original_place_order = tm.adapter_place_order
    # Override exit executor with mock success return
    tm.adapter_place_order = lambda cfg, req: {"status": "success", "orderid": "exit_12345", "raw": {}}

    # 2. Simulate price tick: Price moves slightly in favour (+0.2% PnL)
    print("\n--- Test 2: Price tick +0.2% (No action expected) ---")
    monitor._process_price_update(pos, 1002.0)
    pos = monitor.active_positions[pos_id]
    print(f"LTP=1002.0 | SL={pos['current_sl']} (Expected: 990.0) | HighWater={pos['high_water_mark']} (Expected: 1002.0)")

    # 3. Simulate price tick: Partial exit target hit (+0.6% PnL)
    print("\n--- Test 3: Price tick +0.6% (Partial exit expected & Breakeven SL) ---")
    monitor._process_price_update(pos, 1006.0)
    pos = monitor.active_positions[pos_id]
    print(f"LTP=1006.0 | Qty={pos['quantity']} (Expected: 5) | SL={pos['current_sl']} (Expected: 1000.0 Breakeven)")

    # 4. Simulate price tick: Trailing SL activation hit (+0.9% PnL)
    # HWM = 1009.0, Trail distance = 0.3% -> new SL = 1009 * 0.997 = 1005.97
    print("\n--- Test 4: Price tick +0.9% (Trailing SL activation expected) ---")
    monitor._process_price_update(pos, 1009.0)
    pos = monitor.active_positions[pos_id]
    print(f"LTP=1009.0 | SL={pos['current_sl']} (Expected: 1005.97) | TrailingActive={pos['trailing_active']}")

    # 5. Simulate price tick: Profit Lock threshold hit (+1.3% PnL)
    # Peak gain = 13.0 (at LTP 1013), locked gain = 13 * 0.5 = 6.5 -> new SL = 1006.5
    print("\n--- Test 5: Price tick +1.3% (Profit Lock activation expected) ---")
    monitor._process_price_update(pos, 1013.0)
    pos = monitor.active_positions[pos_id]
    print(f"LTP=1013.0 | SL={pos['current_sl']} (Expected: 1006.5) | ProfitLocked={pos['profit_locked']}")

    # 6. Simulate price tick: Price drops below trailing SL (1006.0 < 1006.5 SL) -> Stop Loss Exit
    print("\n--- Test 6: Price drop to 1006.0 (Stop Loss exit expected) ---")
    monitor._process_price_update(pos, 1006.0)
    
    # Position should be closed and removed from monitor
    is_active = pos_id in monitor.active_positions
    print(f"LTP=1006.0 | Position active? {is_active} (Expected: False)")

    # Verify closed details in DB
    closed_pos = trade_db.get_closed_positions()[0]
    print(f"DB Closed Entry: Status={closed_pos['status']}, CloseReason={closed_pos['close_reason']}, ExitPrice={closed_pos['close_price']}, PnL={closed_pos['pnl_pct']}%")

    # Restore adapter
    tm.adapter_place_order = original_place_order

if __name__ == "__main__":
    test_trade_management()
