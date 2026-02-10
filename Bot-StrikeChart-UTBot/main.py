"""
PureOptionsBot v2.0 - Main Entry Point

Modular, async-based trading bot with crash recovery and plugin architecture.

Usage:
    python main.py

The bot will:
1. Load configuration from config.yaml
2. Restore any active trades from database (crash recovery)
3. Start async event loop with concurrent tasks
4. Monitor for signals and manage risk continuously
"""

import asyncio
import yaml
import logging
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.engine import TradingEngine


def setup_logging():
    """Configure logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('bot.log'),
            logging.StreamHandler()
        ]
    )
    
    # Suppress verbose HTTP library logs (httpx, httpcore, websocket)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def print_startup_banner(config: dict, logger):
    """Print detailed startup configuration banner"""
    # Index source
    index_query = config.get("index_query", "NIFTY")
    index_exchange = config.get("index_exchange", "NSE_INDEX")
    
    # Manual strikes
    strike_cfg = config.get("strike_selection", {})
    manual_strikes = strike_cfg.get("manual_strikes", [])
    
    # Indicator settings
    opt_cfg = config.get("option", {})
    ltf_cfg = opt_cfg.get("ltf", {})
    htf_cfg = opt_cfg.get("htf", {"enabled": False})
    
    ltf_tf = ltf_cfg.get("timeframe", "1m")
    ltf_sens = ltf_cfg.get("sensitivity", 1.0)
    ltf_atr = ltf_cfg.get("atr", 10)
    
    # TSL settings
    tsl_cfg = config.get("tsl", {})
    tsl_mode = tsl_cfg.get("mode", "ATR").upper()
    
    live_mode = config.get("live_trade", False)
    mode_str = "LIVE TRADE" if live_mode else "PAPER/OBSERVE"
    
    # Print banner
    print("")
    print("=" * 60)
    print("  Bot-Only-Strike-Chart - [Multi-Timeframe Strategy]")
    print("=" * 60)
    print(f"Mode:             {mode_str}")
    print(f"Strategy:         HTF Trend Filter + LTF Signal Timing")
    print(f"Max Positions:    {config.get('max_positions', 2)}")
    print(f"Max Lots:         {config.get('max_lots', 1)}")
    print(f"Lot Size (Nifty): {config.get('nifty_lot_size', 65)}")
    
    if manual_strikes:
        print(f"Manual Strikes:   {len(manual_strikes)} configured")
        for strike in manual_strikes[:4]:  # Show first 4
            print(f"  - {strike}")
        if len(manual_strikes) > 4:
            print(f"  ... and {len(manual_strikes) - 4} more")
    
    # HTF Detail
    if htf_cfg.get("enabled", False):
        h_tf = htf_cfg.get("timeframe", "15m")
        h_s = htf_cfg.get("sensitivity", 1.0)
        h_a = htf_cfg.get("atr", 10)
        print(f"HTF Trend Filter: {h_tf} (Sens: {h_s}, ATR: {h_a}) | Repaint: OFF")
    else:
        print(f"HTF Trend Filter: DISABLED (Using LTF signals only)")

    # LTF Detail
    print(f"LTF Entry Timing: {ltf_tf} (Sens: {ltf_sens}, ATR: {ltf_atr}) | Repaint: {'ON' if ltf_cfg.get('repaint', False) else 'OFF'}")
    print(f"HA Mode:          {'ON' if ltf_cfg.get('use_ha', False) else 'OFF'}")
    
    # Entry conditions summary
    entry_cfg = config.get("entry_conditions", {})
    checks = []
    if entry_cfg.get("check_vwap_bullish", True): checks.append("VWAP")
    if entry_cfg.get("check_ema_trend", True): checks.append("EMA")
    if entry_cfg.get("check_volume", True): checks.append("VOL")
    if entry_cfg.get("check_wick_ratio", True): checks.append("WICK")
    if entry_cfg.get("check_adx", False): checks.append("ADX")
    if entry_cfg.get("check_rsi", False): checks.append("RSI")
    
    if htf_cfg.get("enabled", False):
        checks.insert(0, f"HTF_{htf_cfg.get('timeframe', '15m')}")
        
    print(f"Entry Checks:     {', '.join(checks)}")
    
    # TSL
    print(f"TSL Mode:         {tsl_mode}")
    print("")
    print("Press Ctrl+C to stop.")
    print("")
    
    logger.info("Configuration banner printed")


async def main():
    """Main async entry point"""
    # Setup
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Load configuration
    try:
        config = load_config()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return
    
    # Print startup banner
    print_startup_banner(config, logger)
    
    # Initialize OpenAlgo API client
    try:
        from openalgo import api
        import os
        
        # Check environment variable first, then config (security best practice)
        api_key = os.getenv("OPENALGO_API_KEY") or config.get("api_key")
        api_host = config.get("api_host", "http://127.0.0.1:5000")
        
        if not api_key:
            raise ValueError("api_key not found in config.yaml or OPENALGO_API_KEY environment variable")
        
        client = api(api_key=api_key, host=api_host)
        logger.info(f"OpenAlgo API client initialized (host: {api_host})")
        
        # Verify Connection/Credentials
        print("[INFO] Verifying API Credentials...")
        try:
            # We use positionbook as a lightweight auth check
            test_resp = client.positionbook()
            if test_resp and test_resp.get("status") == "success":
                print("[SUCCESS] API Connection Verified!")
            else:
                msg = test_resp.get("message", "Unknown Error") if test_resp else "No Response"
                logger.error(f"API Connection Failed: {msg}")
                print(f"\n[CRITICAL] API Connection Failed: {msg}")
                print("Please check your API KEY in config.yaml!\n")
                # We don't exit to allow 'MOCK' testing if intended, but user is warned.
        except Exception as e:
             logger.error(f"API Connection Verification Error: {e}")
             print(f"[ERROR] API Verification crashed: {e}")
             
    except Exception as e:
        logger.error(f"Failed to initialize API client: {e}")
        logger.warning("Running in mock mode - no real orders will be placed")
        client = None
    
    # Create and start trading engine
    try:
        engine = TradingEngine(config, client)
        logger.info("Trading Engine created successfully")
        
        # Run until stopped
        await engine.start()
        
    except KeyboardInterrupt:
        logger.info("\nShutdown requested by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Goodbye!")


if __name__ == "__main__":
    # Run async main
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
