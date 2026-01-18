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
    
    idx_ltf_tf = idx_ltf_cfg.get("timeframe", "3m")
    idx_ltf_sens = idx_ltf_cfg.get("sensitivity", 1.0)
    idx_ltf_atr = idx_ltf_cfg.get("atr_period", 10)
    
    idx_htf_tf = idx_htf_cfg.get("timeframe", "15m")
    idx_htf_sens = idx_htf_cfg.get("sensitivity", 1.0)
    idx_htf_atr = idx_htf_cfg.get("atr_period", 10)
    idx_htf_enabled = idx_htf_cfg.get("enabled", False)
    
    opt_ltf_tf = opt_ltf_cfg.get("timeframe", "1m")
    opt_ltf_sens = opt_ltf_cfg.get("sensitivity", 1.0)
    opt_ltf_atr = opt_ltf_cfg.get("atr_period", 10)
    
    # TSL settings
    tsl_mode = config.get("tsl_mode", "ATR").upper()
    tsl_detail = ""
    if tsl_mode == "ATR":
        tsl_detail = f"{config.get('tsl_atr_multiplier', 2.5)}"
    elif tsl_mode == "PERCENT":
        tsl_detail = f"{config.get('tsl_percent', 4.0)}%"
    elif tsl_mode == "POINTS":
        tsl_detail = f"{config.get('tsl_points', 8.0)} points"
    
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
    print("  PureOptionsBot v2.0 - [EMA/ADX/RSI Strategy]")
    print("=" * 60)
    print(f"Index Source:     {index_query} ({index_exchange})")
    print(f"Trend TF:         {config.get('trend_tf', '15m')}")
    print(f"Execution TF:     {config.get('execution_tf', '3m')}")
    print(f"Signal Source:    {signal_source}")
    if manual_strikes:
        print(f"Manual Strikes:   {manual_strikes}")
    
    # Indicator details
    print(f"utbot Index LTF:        {idx_ltf_tf} (Sens: {idx_ltf_sens}, ATR: {idx_ltf_atr})")
    print(f"utbot Option LTF:       {opt_ltf_tf} (Sens: {opt_ltf_sens}, ATR: {opt_ltf_atr})")
    
    # Trading config
    print(f"Bot Mode:         {mode_str}")
    print(f"Lots (Mult):      {config.get('lots', 1)}")
    print(f"Heikin Ashi:      Index: {'ON' if config.get('index_use_ha', True) else 'OFF'}, Option: {'ON' if config.get('option_use_ha', False) else 'OFF'}")
    print(f"TSL Mode:         {tsl_mode}")
    if tsl_mode == "PERCENT":
        print(f"TSL Percent:      {tsl_detail}")
    else:
        print(f"TSL {tsl_mode}:        {tsl_detail}")
    print(f"Min Trail Gap:    {config.get('min_trailing_gap', 2.0)} points")
    print("")
    print(f"[INFO] Trend Persistence: Index Max Age={idx_max_age}, Option Max Age={opt_max_age}")
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
        
    except Exception as e:
        logger.error(f"Failed to initialize API client: {e}")
        logger.warning("Running in mock mode - no real orders will be placed")
        client = None
    
    # Fetch security master (with caching)
    if client:
        try:
            import pickle
            import os
            from datetime import date
            
            cache_file = "instruments_cache.pkl"
            instruments = None
            loaded_from_cache = False
            
            # Check cache validity
            if os.path.exists(cache_file):
                mtime = date.fromtimestamp(os.path.getmtime(cache_file))
                if mtime == date.today():
                    try:
                        with open(cache_file, "rb") as f:
                            instruments = pickle.load(f)
                        print(f"[INFO] Master loaded from cache ({len(instruments)} instruments).")
                        loaded_from_cache = True
                    except Exception as e:
                        logger.warning(f"Failed to load cache: {e}")
            
            # Fetch from API if not in cache
            should_fetch = False
            if instruments is None:
                should_fetch = True
            elif isinstance(instruments, list) and len(instruments) == 0:
                should_fetch = True
            elif hasattr(instruments, 'empty') and instruments.empty:
                should_fetch = True

            if should_fetch:
                print("[INFO] Fetching security master from API (this may take a moment)...")
                instruments = client.instruments(exchange="NSE")
                
                has_data = False
                if instruments is not None:
                    if isinstance(instruments, list) and len(instruments) > 0:
                        has_data = True
                    elif hasattr(instruments, 'empty') and not instruments.empty:
                        has_data = True
                
                if has_data:
                    count = len(instruments)
                    print(f"[INFO] Master fetched from API ({count} instruments).")
                    
                    # Save to cache
                    try:
                        with open(cache_file, "wb") as f:
                            pickle.dump(instruments, f)
                        print(f"[INFO] Master saved to {cache_file}")
                    except Exception as e:
                        logger.warning(f"Failed to write cache: {e}")
                else:
                    print("[WARN] API returned empty instrument list.")

            # Pass instruments to engine if needed (currently engine doesn't explicitly take it, 
            # but we can set it on the client or engine if the architecture supports it.
            # The original code just printed the count, so we'll stick to that for now 
            # unless we need to inject it into the engine)
            
        except Exception as e:
            logger.debug(f"Could not fetch/cache instruments: {e}")
            print(f"[WARN] Security master error: {e}")
    
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
