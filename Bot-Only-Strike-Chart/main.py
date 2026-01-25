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
    
    # Signal source
    signal_source = config.get("signal_source", "OPTION").upper()
    
    # Manual strikes
    strike_cfg = config.get("strike_selection", {})
    manual_strikes = strike_cfg.get("manual_strikes", [])
    
    # Indicator settings
    idx_ltf_cfg = config.get("index", {}).get("ltf", {})
    idx_htf_cfg = config.get("index", {}).get("htf", {})
    opt_ltf_cfg = config.get("option", {}).get("ltf", {})
    opt_htf_cfg = config.get("option", {}).get("htf", {})
    
    idx_ltf_tf = idx_ltf_cfg.get("timeframe", opt_ltf_cfg.get("timeframe", "3m"))
    idx_ltf_sens = idx_ltf_cfg.get("sensitivity", 1.0)
    idx_ltf_atr = idx_ltf_cfg.get("atr_period", 10)
    
    idx_htf_tf = idx_htf_cfg.get("timeframe", "15m")
    idx_htf_sens = idx_htf_cfg.get("sensitivity", 1.0)
    idx_htf_atr = idx_htf_cfg.get("atr_period", 10)
    idx_htf_enabled = idx_htf_cfg.get("enabled", False)
    
    opt_ltf_tf = opt_ltf_cfg.get("timeframe", "3m")
    opt_ltf_sens = opt_ltf_cfg.get("sensitivity", 1.0)
    opt_ltf_atr = opt_ltf_cfg.get("atr", 10)
    
    # TSL settings
    tsl_cfg = config.get("tsl", {})
    tsl_mode = tsl_cfg.get("mode", "ATR").upper()
    tsl_detail = ""
    if tsl_mode == "ATR":
        tsl_detail = f"{tsl_cfg.get('atr_multiplier', 1.5)}"
    elif tsl_mode == "PERCENT":
        tsl_detail = f"{tsl_cfg.get('trail_pct', 4.0)}%"
    elif tsl_mode == "POINTS":
        tsl_detail = f"{tsl_cfg.get('trail_points', 50)} points"
    
    # Age settings
    entry_logic = config.get("entry_logic", {})
    idx_max_age = entry_logic.get("index_max_trend_age", 8)
    opt_max_age = entry_logic.get("option_max_trend_age", 8)
    
    # Bot mode
    live_mode = config.get("live_trade", False)
    mode_str = "LIVE TRADE" if live_mode else "PAPER/OBSERVE"
    
    # Print banner
    print("")
    print("=" * 60)
    print("  Bot-Only-Strike-Chart - [Option-Centric Strategy]")
    print("=" * 60)
    print(f"Mode:             {mode_str}")
    print(f"Signals:          OPTION-CENTRIC (UTBot on Strike Charts)")
    print(f"Max Positions:    {config.get('max_positions', 2)}")
    print(f"Max Lots:         {config.get('max_lots', 1)}")
    print(f"Lot Size (Nifty): {config.get('nifty_lot_size', 65)}")
    if manual_strikes:
        print(f"Manual Strikes:   {len(manual_strikes)} configured")
        for strike in manual_strikes[:4]:  # Show first 4
            print(f"  - {strike}")
        if len(manual_strikes) > 4:
            print(f"  ... and {len(manual_strikes) - 4} more")
    
    # Option indicator details
    print(f"Option TF:        {opt_ltf_tf} (Sens: {opt_ltf_sens}, ATR: {opt_ltf_atr})")
    print(f"HA Mode:          {'ON' if config.get('option', {}).get('ltf', {}).get('use_ha', False) else 'OFF'}")
    
    # Entry conditions summary
    entry_cfg = config.get("entry_conditions", {})
    checks = []
    if entry_cfg.get("check_vwap_bullish", True): checks.append("VWAP")
    if entry_cfg.get("check_ema_trend", True): checks.append("EMA")
    if entry_cfg.get("check_volume", True): checks.append("VOL")
    if entry_cfg.get("check_wick_ratio", True): checks.append("WICK")
    if entry_cfg.get("check_adx", False): checks.append("ADX")
    if entry_cfg.get("check_rsi", False): checks.append("RSI")
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
        
        api_key = config.get("api_key")
        api_host = config.get("api_host", "http://127.0.0.1:5000")
        
        if not api_key:
            raise ValueError("api_key not found in config.yaml")
        
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
