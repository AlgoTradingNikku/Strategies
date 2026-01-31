import sqlite3
import os

db_path = "c:/Rahul/06_Nikku/Strategies/Bot-Only-Strike-Chart/bot_state.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(trades);")
    columns = cursor.fetchall()
    for col in columns:
        print(f"{col[0]}: {col[1]}")
    conn.close()
else:
    print(f"File not found: {db_path}")
