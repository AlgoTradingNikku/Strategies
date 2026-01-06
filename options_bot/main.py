import logging
import time
import signal
import sys
from config import config
from mock_api import MockAPI
from data_handler import DataHandler
from indicators import calculate_ema, calculate_rsi, calculate_utbot, calculate_stochrsi, convert_to_heikin_ashi
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
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Main")

def signal_handler(sig, frame):
    logger.info("Exiting Bot...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def main_heartbeat(count, symbol, price, ws_active, ltf_df=None, htf_df=None, mode="LIVE", active_pos=None, rm=None):
    """Logs a periodic status message with symbol price and indicator state."""
    import datetime
    # Retrieve active indicators and timeframes from config
    active_ltf = config.get("active_indicators.ltf", [])
    active_htf = config.get("active_indicators.htf", [])
    tf_ltf = config.get("strategy_settings.timeframe_ltf", 5)
    tf_htf = config.get("strategy_settings.timeframe_htf", 15)
    
    if count % 30 == 0:
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        # HTF Trends
        htf_str = ""
        if htf_df is not None and not htf_df.empty:
            last_htf = htf_df.iloc[-1]
            htf_parts = []
            if "ema" in active_htf:
                h_ema9 = last_htf.get('ema_9', 0)
                h_ema21 = last_htf.get('ema_21', 0)
                h_trend = "BUY" if h_ema9 > h_ema21 else "SELL"
                htf_parts.append(f"EMA:{h_trend}")
            if "utbot" in active_htf:
                ut_val = last_htf.get('utbot_signal', 0)
                ut_str = "BUY" if ut_val == 1 else ("SELL" if ut_val == -1 else "WAIT")
                htf_parts.append(f"UTBot:{ut_str}")
            if htf_parts:
                htf_str = f" [HTF:{'|'.join(htf_parts)}]"

        # LTF Signals
        ltf_str = ""
        if ltf_df is not None and not ltf_df.empty:
            last_ltf = ltf_df.iloc[-1]
            ltf_parts = []
            if "utbot" in active_ltf:
                ut_val = last_ltf.get('utbot_signal', 0)
                ut_str = "BUY" if ut_val == 1 else ("SELL" if ut_val == -1 else "WAIT")
                ltf_parts.append(f"UTBot:{ut_str}")
            if "rsi" in active_ltf:
                rsi_val = last_ltf.get('rsi_14', 0)
                ltf_parts.append(f"RSI:{rsi_val:.1f}")
            if "ema" in active_ltf:
                ema9 = last_ltf.get('ema_9', 0)
                ema21 = last_ltf.get('ema_21', 0)
                ema_str = "BUY" if ema9 > ema21 else "SELL"
                ltf_parts.append(f"EMA:{ema_str}")
            if ltf_parts:
                ltf_str = f" [LTF:{' '.join(ltf_parts)}]"
        
        pos_count = len(active_pos) if active_pos else 0
        pos_color = "🟢" if pos_count > 0 else "⚪"
        
        # Candle Type Indicator
        ctype = config.get("strategy_settings.candle_type", "OHLC")
        ctype_short = "[HA]" if ctype == "HEIKIN_ASHI" else "[OHLC]"
        
        # Compact Heartbeat: 10:45:01 NIFTY: 26230.5 [HA] | [HTF:EMA:BUY] [LTF:UTBot:BUY RSI:52.1] | Pos: 0
        print(f"💓 {now_str} {symbol}: {price:8.2f} {ctype_short} |{htf_str}{ltf_str} | {pos_color} Pos: {pos_count}", flush=True)
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
    om.risk_manager = rm # Link back for brokerage tracking
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
    logger.info(f"  Hard Stop Loss (Fix): {rm_cfg.get('stop_loss_pct')}%")
    logger.info(f"  Hard Target Profit (Fix): {rm_cfg.get('target_profit_pct')}%")
    logger.info(f"  TSL Trail:      {rm_cfg.get('trailing_stop_pct')}% (After {rm_cfg.get('trailing_activation_pct')}% profit)")
    
    dml = rm_cfg.get('max_daily_acceptable_loss', 0)
    dml_str = f"₹{dml}" + (" (disabled)" if dml == 0 else "")
    logger.info(f"  Daily Max Loss: {dml_str}")
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
                
                # Fetch poll interval early to avoid reference errors
                poll_interval = config.get("api.polling_interval", 2)
                ws_ltp = None
                spot_price = None
                
                # 1. Fetch Data
                if live_trading:
                    symbol = "NIFTY"
                    exchange = "NSE_INDEX"
                    
                    # Fetch history for both timeframes
                    tf_ltf = str(config.get("strategy_settings.timeframe_ltf", 5))
                    tf_htf = str(config.get("strategy_settings.timeframe_htf", 15))
                    
                    data_ltf = api.history(symbol, resolution=tf_ltf, exchange=exchange)
                    data_htf = api.history(symbol, resolution=tf_htf, exchange=exchange)
                    
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
                    
                    if data_ltf.empty or data_htf.empty:
                        if main.loop_count % 5 == 1:
                            logger.info(f"⏳ Syncing historical data for {symbol} (LTF:{tf_ltf}, HTF:{tf_htf})...")
                        time.sleep(poll_interval)
                        continue
                    
                    if main.loop_count == 1:
                        logger.info(f"✅ Data synced successfully. Starting market monitoring.")
                else:
                    # PAPER/MOCK MODE:
                    symbol = "NIFTY"
                    data_ltf = api.history(symbol, resolution="5", start=None, end=None)
                    data_htf = api.history(symbol, resolution="15", start=None, end=None)
                    spot_price = data_ltf['close'].iloc[-1]
                
                # 1.5 Handle Heikin-Ashi Conversion if requested
                candle_type = config.get("strategy_settings.candle_type", "OHLC")
                if candle_type == "HEIKIN_ASHI":
                    data_ltf = convert_to_heikin_ashi(data_ltf)
                    data_htf = convert_to_heikin_ashi(data_htf)

                # 2. Calculate Indicators (Only active ones)
                active_ltf = config.get("active_indicators.ltf", [])
                active_htf = config.get("active_indicators.htf", [])

                # 2.1 LTF Calculations
                ltf_df = data_ltf.copy()
                if "ema" in active_ltf:
                    ltf_df = calculate_ema(ltf_df, 9)
                    ltf_df = calculate_ema(ltf_df, 21)
                if "rsi" in active_ltf:
                    ltf_df = calculate_rsi(ltf_df, 14)
                if "stochrsi" in active_ltf:
                    ltf_df = calculate_stochrsi(ltf_df)
                if "utbot" in active_ltf:
                    ltf_df = calculate_utbot(ltf_df, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr", 10))
                
                # 2.2 HTF Calculations
                htf_df = data_htf.copy()
                if "ema" in active_htf:
                    htf_df = calculate_ema(htf_df, 9)
                    htf_df = calculate_ema(htf_df, 21)
                if "utbot" in active_htf:
                    htf_df = calculate_utbot(htf_df, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr", 10))
                
                # 2.3 Log Heartbeat (Now with HTF + LTF info)
                # Tune frequency: every 10 loops for start, then every 150 (~5 mins)
                hb_freq = 10 if main.loop_count < 100 else 150
                if main.loop_count % hb_freq == 0:
                     main_heartbeat(
                        0, # Force print by passing 0 % count logic inside if needed, 
                           # but we'll just handle it here:
                        symbol, 
                        spot_price, 
                        ws_active=bool(ws_ltp if live_trading else False), 
                        ltf_df=ltf_df, 
                        htf_df=htf_df,
                        mode="LIVE" if live_trading else "PAPER",
                        active_pos=om.active_positions,
                        rm=rm
                    )

                # 3. Strategy Logic (Entry & Reversal Exit)
                signal = strat.generate_signal(htf_df, ltf_df)
                
                # REVERSAL EXIT: Close position if signal flips against us
                if om.active_positions:
                    for pos in list(om.active_positions):
                        if pos['type'] == 'CE' and signal == 'PE_REVERSAL':
                            logger.info(f"🔄 REVERSAL: Closing {pos['symbol']} because chart turned RED")
                            om.close_position(pos, "Signal Reversal")
                            rm.record_sell_order(pos['symbol'], (current_prices.get(pos['symbol'], pos['peak_price']) - pos['entry_price']) * pos['qty'])
                        elif pos['type'] == 'PE' and signal == 'CE_REVERSAL':
                            logger.info(f"🔄 REVERSAL: Closing {pos['symbol']} because chart turned GREEN")
                            om.close_position(pos, "Signal Reversal")
                            rm.record_sell_order(pos['symbol'], (current_prices.get(pos['symbol'], pos['peak_price']) - pos['entry_price']) * pos['qty'])

                if not om.active_positions: # Only look for entry if flat
                    if signal and isinstance(signal, dict) and signal.get('action') == 'BUY':
                        if rm.check_pre_entry_risk():
                            om.place_entry_order(signal, spot_price)
                
                # 4. Manage Active Positions (Exit via TSL/Target)
                # (Existing current_prices loop remains here)
                if current_prices:
                    # Get current ATR for ATR-based TSL
                    current_atr = 0
                    if ltf_df is not None and not ltf_df.empty:
                        current_atr = ltf_df.iloc[-1].get('atr', 0)
                    
                    rm.check_exit_conditions(current_prices, current_atr=current_atr)
                
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
