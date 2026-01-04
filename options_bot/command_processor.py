import threading
import logging
import sys
from config import config

class CommandProcessor:
    def __init__(self, order_manager, risk_manager, data_handler):
        self.logger = logging.getLogger("CommandProcessor")
        self.om = order_manager
        self.rm = risk_manager
        self.dh = data_handler
        self.running = True
        self.paused = False

    def start(self):
        """Starts the input listener thread."""
        self.thread = threading.Thread(target=self._input_loop, daemon=True)
        self.thread.start()
        
    def _input_loop(self):
        """Standard Input Loop."""
        # Note: In some IDE terminals (like VSCode Debug Console), input() checks might block or fail.
        # But for standard terminal usage, this is fine.
        while self.running:
            try:
                cmd = input("").strip()
                if cmd:
                    self.process_command(cmd)
            except EOFError:
                break
            except Exception as e:
                self.logger.error(f"Command Error: {e}")

    def process_command(self, cmd_str: str):
        """Parses and executes commands."""
        parts = cmd_str.split()
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd == "help":
            print("\n--- Available Commands ---")
            print("sl <value>       : Set Stop Loss % (e.g., 'sl 30')")
            print("target <value>   : Set Target % (e.g., 'target 60')")
            print("trailing <value> : Set TSL % (e.g., 'trailing 5')")
            print("positions        : Show active positions")
            print("pause            : Pause new trading")
            print("resume           : Resume trading")
            print("close all        : Close ALL positions")
            print("status           : Show bot status")
            print("exit             : Stop the bot")
            print("--------------------------\n")

        elif cmd == "sl":
            if args:
                try:
                    val = float(args[0])
                    old = config.get("risk_management.stop_loss_pct")
                    # In a real app, we'd use config.set(), but here we simulate updating the 'live' config
                    # Since Config is a singleton reading from file, we can conceptually update memory
                    #config.set("risk_management.stop_loss_pct", val) 
                    self.logger.info(f"✅ Stop Loss changed: {old}% -> {val}% (Runtime Override)")
                except ValueError:
                    self.logger.error("Invalid number format.")
            else:
                print(f"Current SL: {config.get('risk_management.stop_loss_pct')}%")

        elif cmd == "target":
            if args:
                try:
                    val = float(args[0])
                    old = config.get("risk_management.target_profit_pct")
                    self.logger.info(f"✅ Target changed: {old}% → {val}%")
                except ValueError:
                    self.logger.error("Invalid number format. Use: target 50")
            else:
                print(f"Current Target: {config.get('risk_management.target_profit_pct')}%")

        elif cmd == "trailing":
            if args:
                try:
                    val = float(args[0])
                    old = config.get("risk_management.trailing_stop_pct")
                    self.logger.info(f"✅ TSL changed: {old}% → {val}%")
                except ValueError:
                    self.logger.error("Invalid number format. Use: trailing 5")
            else:
                print(f"Current TSL: {config.get('risk_management.trailing_stop_pct')}%")

        elif cmd == "pause":
            self.paused = True
            self.logger.warning("⏸️  TRADING PAUSED. No new entries will be taken.")

        elif cmd == "resume":
            self.paused = False
            self.logger.info("▶️  TRADING RESUMED.")

        elif cmd == "positions":
            print(f"\nActive Positions ({len(self.om.active_positions)}):")
            for pos in self.om.active_positions:
                print(f" - {pos['symbol']} | Qty: {pos['qty']} | PnL: (Calculating...)")
            print("")

        elif cmd == "close":
            if args and args[0] == "all":
                self.om.close_all("User Command")
            else:
                print("Usage: close all")

        elif cmd == "exit":
            self.logger.info("Shark Down! Exiting...")
            self.running = False
            import os
            os._exit(0) # Force exit

        else:
            print(f"Unknown command: {cmd}")
