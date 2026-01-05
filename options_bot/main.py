import logging
import time
import signal
import sys
from config import config
from mock_api import MockAPI
from data_handler import DataHandler
from indicators import calculate_ema, calculate_rsi, calculate_utbot, calculate_stochrsi
from strategy import StrategyEngine
from order_manager import OrderManager
from risk_manager import RiskManager
from command_processor import CommandProcessor
from openalgo_rest import OpenAlgoREST  # Direct REST fallback
from websocket_handler import WebSocketHandler
import utils

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Main")

def signal_handler(sig, frame):
    logger.info("Exiting Bot...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def main_heartbeat(count, symbol, price, ws_active, df=None, mode="LIVE", active_pos=None):
    """Logs a periodic status message with symbol price and indicator state."""
    if count % 30 == 0:
        source = "WS" if ws_active else ("REST" if mode == "LIVE" else "MOCK")
        
        indicator_str = ""
        if df is not None and not df.empty:
            last = df.iloc[-1]
            
            # UTBOT: Map 1 to BUY, -1 to SELL
            ut_val = last.get('utbot_signal', 0)
            ut_str = "BUY" if ut_val == 1 else ("SELL" if ut_val == -1 else "WAIT")
            
            # RSI: The calculated column is rsi_14
            rsi_val = last.get('rsi_14', 0)
            
            # EMA: Fast/Slow alignment
            ema9 = last.get('ema_9', 0)
            ema21 = last.get('ema_21', 0)
            ema_str = "BUY" if ema9 > ema21 else "SELL"
            
            indicator_str = f" | UTBot: {ut_str} | RSI: {rsi_val:.1f} | EMA: {ema_str}"
        
        pos_count = len(active_pos) if active_pos else 0
        pos_str = f" | Positions: {pos_count}"
        
        # User requested clean log without 'Main - INFO' and with '[LTF]'
        print(f"💓 [LTF] {symbol} @ {price} ({source}){indicator_str}{pos_str}", flush=True)
        return True
    return False

def main():
    logger.info("--------------------------------")
    logger.info("   Options Bot - V1.0 (Live)    ")
    logger.info("--------------------------------")
    
    # 1. Initialize API Client (Auto-select based on live_trading flag)
    logger.info("Initializing Components...")
    
    live_trading = config.get("live_trading", False)
    
    if live_trading:
        # LIVE TRADING MODE
        logger.warning("=" * 60)
        logger.warning("⚠️  LIVE TRADING MODE ENABLED - REAL MONEY AT RISK!")
        logger.warning("=" * 60)
        
        try:
            logger.info("Connecting to OpenAlgo API via REST...")
            api_key = config.get("api.api_key")
            host = config.get("api.host")
            
            # Use our robust REST wrapper instead of the SDK
            api = OpenAlgoREST(api_key=api_key, host=host)
            logger.info("✅ Connected to OpenAlgo API (REST Mode)")
            
            # 1b. Initialize WebSocket for Real-time TSL
            ws_url = config.get("api.websocket_url", "ws://127.0.0.1:8765")
            ws_handler = WebSocketHandler(api_key=api_key, ws_url=ws_url)
            ws_handler.start()
            # Subscribe to NIFTY spot
            ws_handler.subscribe(["NIFTY.NSE_INDEX"])
            logger.info(f"✅ WebSocket Handler Started (Monitoring: NIFTY.NSE_INDEX)")
            
        except Exception as e:
            logger.error(f"❌ API Connection Failed: {e}")
            logger.error("💡 Check your API Key and Host URL in config.json")
            sys.exit(1)
    else:
        # PAPER TRADING MODE (Safe default)
        logger.info("📄 PAPER TRADING MODE - Using Mock Data (No real money)")
        api = MockAPI()
    
    # 2. Initialize other components
    dh = DataHandler(api)
    om = OrderManager(api, ws_handler=ws_handler if live_trading else None)
    rm = RiskManager(om)
    strat = StrategyEngine()
    cmd_proc = CommandProcessor(om, rm, dh)
    
    # 2. Start Command Listener
    cmd_proc.start()
    logger.info("✅ Command Interface Ready. Type 'help' for commands.")
    
    # Display Active Configuration
    logger.info("=" * 60)
    logger.info("  📊 SYSTEM CONFIGURATION SUMMARY")
    logger.info("-" * 60)
    
    # 1. Mode & API
    mode_str = "LIVE (Real Money)" if live_trading else "PAPER (Mock Data)"
    logger.info(f"  Mode:           {mode_str}")
    logger.info(f"  Symbols:        {', '.join(config.get('general.symbols', ['NIFTY']))}")
    
    # 2. Indicators
    logger.info("-" * 60)
    logger.info("  🚀 STRATEGY & INDICATORS:")
    htf_indicators = config.get("active_indicators.htf", [])
    ltf_indicators = config.get("active_indicators.ltf", [])
    logger.info(f"  HTF ({config.get('strategy_settings.timeframe_htf')}m):    {', '.join(htf_indicators).upper()}")
    logger.info(f"  LTF ({config.get('strategy_settings.timeframe_ltf')}m):    {', '.join(ltf_indicators).upper()}")
    
    # 3. Strike Selection
    logger.info("-" * 60)
    logger.info("  🎯 STRIKE SELECTION:")
    ss = config.get("strike_selection", {})
    logger.info(f"  Mode:           {ss.get('mode')}")
    if ss.get('mode') == "ATM_OFFSET":
        logger.info(f"  Strike Step:    {ss.get('strike_step')} (0=ATM)")
    else:
        logger.info(f"  Target Premium: ₹{ss.get('target_premium')}")
    logger.info(f"  Expiry Type:    {ss.get('expiry_type')}")
    
    # 4. Risk & Capital
    logger.info("-" * 60)
    logger.info("  🛡️ RISK MANAGEMENT:")
    rm_cfg = config.get("risk_management", {})
    logger.info(f"  Capital/Trade:  ₹{rm_cfg.get('capital_per_trade')}")
    logger.info(f"  Stop Loss:      {rm_cfg.get('stop_loss_pct')}%")
    logger.info(f"  Target Profit:  {rm_cfg.get('target_profit_pct')}%")
    logger.info(f"  TSL Trail:      {rm_cfg.get('trailing_stop_pct')}% (After {rm_cfg.get('trailing_activation_pct')}% profit)")
    logger.info(f"  Daily Max Loss: ₹{rm_cfg.get('max_daily_acceptable_loss')}")
    logger.info(f"  Max Positions:  {rm_cfg.get('max_positions')}")
    logger.info("=" * 60)
    
    # 3. Main Trading Loop
    if live_trading:
        logger.info("🚀 Bot is LIVE with REAL DATA. (Monitoring Market...)")
    else:
        logger.info("🚀 Bot is LIVE using MOCK Data. (Waiting for signals...)")
    
    try:
        while True:
            if cmd_proc.paused:
                time.sleep(1)
                continue
            try:
                # Loop counter for Heartbeat
                if not hasattr(main, 'loop_count'):
                    main.loop_count = 0
                main.loop_count += 1
                
                # 1. Fetch Data
                if live_trading:
                    symbol = "NIFTY"
                    exchange = "NSE_INDEX"
                    # Fetch history for indicators (5min candles)
                    data = api.history(symbol, resolution="5", exchange=exchange)
                    # Fetch current spot price (WS Priority)
                    spot_price = None
                    ws_ltp = ws_handler.get_ltp("NIFTY.NSE_INDEX")
                    if ws_ltp:
                        spot_price = ws_ltp
                    else:
                        # Fallback to REST if WS hasn't received tick yet
                        spot_quote = api.get_ltp(symbol, exchange=exchange)
                        spot_price = spot_quote.get('ltp')
                    
                    # Detailed log at DEBUG level
                    logger.debug(f"Monitoring Market - {symbol}: {spot_price} {'(WS)' if ws_ltp else '(REST)'}")
                    
                    if not spot_price:
                        logger.warning(f"⚠️ Could not fetch LTP for {symbol}")
                else:
                    # PAPER/MOCK MODE:
                    symbol = "NIFTY"
                    data = api.history(symbol, resolution="5", start=None, end=None)
                    spot_price = data['close'].iloc[-1]
                
                if data.empty or spot_price is None:
                    time.sleep(5)
                    continue

                # 2. Calculate Indicators
                df = data.copy()
                df = calculate_ema(df, 9)
                df = calculate_ema(df, 21)
                df = calculate_rsi(df, 14)
                df = calculate_stochrsi(df)
                df = calculate_utbot(df, config.get("indicators.utbot_key"), 10)
                
                # For simplicity in V1, htf == ltf in terms of data source, but logic remains
                htf_df = df.copy()
                ltf_df = df.copy()

                # 2.3 Log Heartbeat (Now with indicator info)
                main_heartbeat(
                    main.loop_count, 
                    symbol, 
                    spot_price, 
                    ws_active=bool(ws_ltp if live_trading else False), 
                    df=df, 
                    mode="LIVE" if live_trading else "PAPER",
                    active_pos=om.active_positions
                )

                # 3. Strategy Logic (Entry)
                if not om.active_positions: # Only look for entry if flat
                    signal = strat.generate_signal(htf_df, ltf_df)
                    if signal:
                        if rm.check_pre_entry_risk():
                            om.place_entry_order(signal, spot_price)
                
                # 4. Manage Active Positions (Exit)
                current_prices = {}
                for pos in om.active_positions:
                    if live_trading:
                        # WS Priority for active positions
                        ws_key = pos.get('ws_key')
                        ws_ltp = ws_handler.get_ltp(ws_key) if ws_key else None
                        
                        if ws_ltp:
                            current_prices[pos['symbol']] = ws_ltp
                        else:
                            # Fallback to REST
                            opt_quote = api.get_ltp(pos['symbol'], exchange="NFO")
                            if opt_quote.get('ltp'):
                                current_prices[pos['symbol']] = opt_quote['ltp']
                            else:
                                logger.warning(f"⚠️ Could not fetch price for active position {pos['symbol']}")
                    else:
                        # PAPER/MOCK MODE: Simulate price fluctuation
                        import random
                        fluctuation = random.uniform(0.99, 1.01)
                        current_prices[pos['symbol']] = pos['peak_price'] * fluctuation

                if current_prices:
                    rm.check_exit_conditions(current_prices)
                
                # Loop Interval
                poll_interval = config.get("api.polling_interval", 2)
                time.sleep(poll_interval)
                
            except Exception as e:
                logger.error(f"❌ Main Loop Error: {e}")
                time.sleep(5)
            
    except KeyboardInterrupt:
        logger.info("Stopping...")

if __name__ == "__main__":
    main()
