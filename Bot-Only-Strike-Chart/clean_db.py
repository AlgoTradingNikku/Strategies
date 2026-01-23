import os
import time

FILES_TO_DELETE = [
    "bot_state.db",
    "instruments_cache.pkl"
]

def clean():
    print("Stopping Bot cleanup...")
    # Ideally bot should be stopped before running this.
    
    for filename in FILES_TO_DELETE:
        if os.path.exists(filename):
            try:
                os.remove(filename)
                print(f"[SUCCESS] Deleted {filename}")
            except Exception as e:
                print(f"[ERROR] Could not delete {filename}: {e}")
        else:
            print(f"[INFO] {filename} not found (Already clean).")
            
    print("\nCleanup Complete.")
    print("You can now restart the bot. It will start with a fresh state.")

if __name__ == "__main__":
    clean()
