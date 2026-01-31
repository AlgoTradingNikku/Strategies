import sqlite3
import os
import sys
from unittest.mock import MagicMock
import asyncio
import functools

# Ensure project root is in path
project_root = os.path.abspath(".")
sys.path.insert(0, project_root)

from core.persistence import TradePersistence
from execution.order_manager import OrderManager

async def verify():
    print("\n--- Verifying SQLite Schema ---")
    db_path = os.path.join(project_root, "bot_state.db")
    
    if os.path.exists(db_path):
        persistence = TradePersistence(db_path)
        cursor = persistence.conn.execute("PRAGMA table_info(trades)")
        columns = [column[1] for column in cursor.fetchall()]
        print(f"Trades columns count: {len(columns)}")
        if len(columns) == 25 and 'exit_price' in columns:
            print("✅ Trades schema verified.")
        else:
            print(f"❌ Trades schema mismatch: {len(columns)} columns. {columns}")
        persistence.close()
    else:
        print("❌ bot_state.db not found.")

    print("\n--- Verifying OrderManager ---")
    mock_client = MagicMock()
    mock_client.orderbook.return_value = "ERROR"
    mock_client.cancelorder.return_value = {"status": "success"}
    
    config = {"max_order_retries": 1}
    om = OrderManager(mock_client, config)
    
    # Test status robustness
    status = await om._get_order_status("123")
    print(f"OrderManager status handling: {status}")
    if status == "UNKNOWN":
        print("✅ String response handling verified.")

    # Test cancelorder keyword arguments via partial
    # We need to ensure it's called. Since it uses run_in_executor, 
    # and we are mocking the client, we should see the call.
    await om._cancel_order("ORD123")
    
    # Check if call was recorded. partial(mock_client.cancelorder, orderid="ORD123")() 
    # should record a call to mock_client.cancelorder with orderid kwarg.
    found = False
    for call in mock_client.cancelorder.call_args_list:
        if 'orderid' in call.kwargs and call.kwargs['orderid'] == "ORD123":
            found = True
            break
            
    if found:
        print("✅ Keyword arguments via partial verified.")
    else:
        print(f"❌ Keyword arguments NOT verified. Call list: {mock_client.cancelorder.call_args_list}")

if __name__ == "__main__":
    asyncio.run(verify())
