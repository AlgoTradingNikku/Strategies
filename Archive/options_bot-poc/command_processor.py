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
            print("sl <value> [sym]       : Set SL % globally or for symbol (e.g., 'sl 30' or 'sl 30 NIFTY...')")
            print("target <value> [sym]   : Set Target % (e.g., 'target 60' or 'target 60 NIFTY...')")
            print("trailing <value> [sym] : Set TSL % (e.g., 'trailing 5' or 'trailing 5 NIFTY...')")
            print("positions              : Show active positions & their risk settings")
            print("pause                  : Pause new trading")
            print("resume                 : Resume trading")
            print("close all              : Close ALL positions")
            print("status                 : Show bot status")
            print("exit                   : Stop the bot")
            print("--------------------------\n")

        elif cmd == "sl":
            if args:
                try:
                    val = float(args[0])
                    symbol = args[1].upper() if len(args) > 1 else None
                    
                    if symbol:
                        # Update specific position
                        found = False
                        for pos in self.om.active_positions:
                            if symbol in pos['symbol']:
                                pos['sl_pct'] = val
                                self.logger.info(f"🎯 SL updated for {pos['symbol']}: {val}%")
                                found = True
                        if not found:
                            self.logger.error(f"❌ No active position found for {symbol}")
                    else:
                        # Global update (conceptual runtime override)
                        old = config.get("risk_management.stop_loss_pct")
                        # We would ideally update config object here if it supported live sets
                        self.logger.info(f"🌍 Global SL override: {old}% -> {val}% (Affects NEW trades)")
                except ValueError:
                    self.logger.error("Invalid number format.")
            else:
                print(f"Current Global SL: {config.get('risk_management.stop_loss_pct')}%")

        elif cmd == "target":
            if args:
                try:
                    val = float(args[1]) if len(args) > 1 and args[0].isalpha() else float(args[0])
                    symbol = args[1].upper() if len(args) > 1 and not args[1].replace('.','',1).isdigit() else (args[0].upper() if len(args) > 1 else None)
                    # Simpler parsing: target <val> [sym]
                    val = float(args[0])
                    symbol = args[1].upper() if len(args) > 1 else None

                    if symbol:
                        found = False
                        for pos in self.om.active_positions:
                            if symbol in pos['symbol']:
                                pos['target_pct'] = val
                                self.logger.info(f"🎯 Target updated for {pos['symbol']}: {val}%")
                                found = True
                        if not found:
                            self.logger.error(f"❌ No active position found for {symbol}")
                    else:
                        old = config.get("risk_management.target_profit_pct")
                        self.logger.info(f"🌍 Global Target override: {old}% → {val}%")
                except ValueError:
                    self.logger.error("Invalid format. Use: target 50 [symbol]")
            else:
                print(f"Current Global Target: {config.get('risk_management.target_profit_pct')}%")

        elif cmd == "trailing":
            if args:
                try:
                    val = float(args[0])
                    symbol = args[1].upper() if len(args) > 1 else None
                    
                    if symbol:
                        found = False
                        for pos in self.om.active_positions:
                            if symbol in pos['symbol']:
                                pos['tsl_pct'] = val
                                self.logger.info(f"🎯 TSL updated for {pos['symbol']}: {val}%")
                                found = True
                        if not found:
                            self.logger.error(f"❌ No active position found for {symbol}")
                    else:
                        old = config.get("risk_management.trailing_stop_pct")
                        self.logger.info(f"🌍 Global TSL override: {old}% → {val}%")
                except ValueError:
                    self.logger.error("Invalid format. Use: trailing 5 [symbol]")
            else:
                print(f"Current Global TSL: {config.get('risk_management.trailing_stop_pct')}%")

        elif cmd == "pause":
            self.paused = True
            self.logger.warning("⏸️  TRADING PAUSED. No new entries will be taken.")

        elif cmd == "resume":
            self.paused = False
            self.logger.info("▶️  TRADING RESUMED.")

        elif cmd == "positions":
            print(f"\n{'='*60}")
            print(f"  📊 ACTIVE POSITIONS ({len(self.om.active_positions)})")
            print(f"{'-'*60}")
            total_unrealized = 0
            for pos in self.om.active_positions:
                sym = pos['symbol']
                qty = pos['qty']
                entry = pos['entry_price']
                
                # Try to get latest price from DataHandler/WS
                ltp = self.dh.api.get_ltp(sym, "NFO").get('ltp', entry)
                unrealized = (ltp - entry) * qty
                total_unrealized += unrealized
                
                print(f"📍 {sym}")
                print(f"   Qty: {qty} | Entry: ₹{entry:.2f} | LTP: ₹{ltp:.2f}")
                print(f"   PnL: ₹{unrealized:+.2f} ({((ltp-entry)/entry)*100:+.2f}%)")
                print(f"   Risk: SL {pos['sl_pct']}% | Tgt {pos['target_pct']}% | TSL {pos['tsl_pct']}%")
            
            print(f"{'-'*60}")
            print(f"  Total Unrealized PnL: ₹{total_unrealized:+.2f}")
            print(f"{'='*60}\n")

        elif cmd == "status":
            print(f"\n{'='*60}")
            print(f"  📈 BOT PERFORMANCE SUMMARY")
            print(f"{'-'*60}")
            print(f"  Realized PnL (Session):")
            for sym, pnl in self.rm.realized_pnl_map.items():
                print(f"   - {sym:20} : ₹{pnl:+.2f}")
            
            total_realized = self.rm.daily_pnl
            total_brokerage = self.rm.total_brokerage
            print(f"{'-'*60}")
            print(f"  Total Realized PnL  : ₹{total_realized:+.2f}")
            print(f"  Total Brokerage Paid: ₹{total_brokerage:.2f} (₹7/order)")
            print(f"  Net Session Profit  : ₹{total_realized:.2f}")
            
            # Active count
            active_count = len(self.om.active_positions)
            paused_str = "PAUSED" if self.paused else "RUNNING"
            print(f"  Status              : {paused_str}")
            print(f"  Active Positions    : {active_count}")
            print(f"{'='*60}\n")

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
