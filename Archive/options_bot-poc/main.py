import utils
import logging
import time
import signal
import sys
import uuid
import traceback
from config import config
from mock_api import MockAPI
from data_handler import DataHandler
from indicators import calculate_ema, calculate_rsi, calculate_utbot, calculate_stochrsi, convert_to_heikin_ashi
from strategy import StrategyEngine
from pullback_manager import PullbackManager
from order_manager import OrderManager
from risk_manager import RiskManager
from command_processor import CommandProcessor
from openalgo_rest import OpenAlgoREST
from websocket_handler import WebSocketHandler

# Error Throttling state
last_ltp_error_time = {} # {symbol: timestamp}
ERROR_MUTE_SECONDS = 300 # Mute same error for 5 mins

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

def main_heartbeat(count, symbol, price, ws_active, ltf_df=None, htf_df=None, mode="LIVE", active_pos=None, rm=None, current_prices=None, wait_status=None, current_atr=0):
    """Logs a periodic status message with symbol price and indicator state."""
    import datetime
    # Retrieve active indicators and timeframes from config
    active_ltf = config.get("active_indicators.ltf") or []
    active_htf = config.get("active_indicators.htf") or []
    tf_ltf = config.get("strategy_settings.timeframe_ltf", 5)
    tf_htf = config.get("strategy_settings.timeframe_htf", 15)
    
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    
    # HTF Trends
    htf_str = ""
    if htf_df is not None and not htf_df.empty:
        last_htf = htf_df.iloc[-1]
        htf_parts = []
        
        # Use safe list for iteration
        safe_active_htf = [x for x in active_htf if x]
        
        if "ema" in safe_active_htf:
            h_ema9 = last_htf.get('ema_9', 0)
            h_ema21 = last_htf.get('ema_21', 0)
            h_trend = "BUY" if h_ema9 > h_ema21 else "SELL"
            htf_parts.append(f"EMA:{h_trend}")
        
        # Dynamic Signal Detection (UTBot / Supertrend)
        for ind in ["utbot", "supertrend"]:
            if ind in safe_active_htf:
                col = f"{ind}_signal"
                val = last_htf.get(col, 0)
                sig_str = "BUY" if val == 1 else ("SELL" if val == -1 else "WAIT")
                htf_parts.append(f"{ind.upper()}:{sig_str}")

        if htf_parts:
            htf_str = f" [HTF:{'|'.join(htf_parts)}]"

    # LTF Signals
    ltf_str = ""
    if ltf_df is not None and not ltf_df.empty:
        last_ltf = ltf_df.iloc[-1]
        ltf_parts = []
        
        # Use safe list for iteration
        safe_active_ltf = [x for x in active_ltf if x]

        # Dynamic Signal Detection (UTBot / Supertrend)
        for ind in ["utbot", "supertrend"]:
            if ind in safe_active_ltf:
                col = f"{ind}_signal"
                val = last_ltf.get(col, 0)
                sig_str = "BUY" if val == 1 else ("SELL" if val == -1 else "WAIT")
                ltf_parts.append(f"{ind.upper()}:{sig_str}")

        if "rsi" in safe_active_ltf:
            rsi_val = last_ltf.get('rsi_14', 0)
            ltf_parts.append(f"RSI:{rsi_val:.1f}")
        if "ema" in safe_active_ltf:
            ema9 = last_ltf.get('ema_9', 0)
            ema21 = last_ltf.get('ema_21', 0)
            ema_str = "BUY" if ema9 > ema21 else "SELL"
            ltf_parts.append(f"EMA:{ema_str}")
        if ltf_parts:
            ltf_str = f" [LTF:{' '.join(ltf_parts)}]"
    
    # Candle Type Indicator
    ctype = config.get("strategy_settings.candle_type", "OHLC")
    ctype_short = "[HA]" if ctype == "HEIKIN_ASHI" else "[OHLC]"

    # Prepare Position Details
    pos_str = ""
    if active_pos:
        pos_count = len(active_pos)
        pos_color = "🟢"
        
        details = []
        for pos in active_pos:
            sym = pos['symbol']
            entry = pos['entry_price']
            qty = pos['qty']
            ltp = current_prices.get(sym, 0) if current_prices else 0
            
            # Calculate PnL
            pnl = (ltp - entry) * qty if ltp > 0 else 0
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            
            # Calculate Current TSL
            stop_price = 0
            if rm:
                stop_price = rm.get_effective_stop(pos, ltp, current_atr)
            
            # Format: SYMBOL [Entry -> LTP] SL:price PnL
            details.append(f"{sym} [{entry:.1f} -> {ltp:.1f}] SL:{stop_price:.1f} {pnl_icon}{pnl:.0f} Rs.")
            
        pos_str = " | ".join(details)
        print(f"💓 {now_str} {symbol}: {price:8.2f} {ctype_short} |{htf_str}{ltf_str} | {pos_color} Pos: {pos_count} | {pos_str}", flush=True)
    else:
        # Add Wait Status if present
        if wait_status:
            print(f"💓 {now_str} {symbol}: {price:8.2f} {ctype_short} |{htf_str}{ltf_str} | ⚪ Pos: 0 | ⏳ {wait_status}", flush=True)
        else:
            print(f"💓 {now_str} {symbol}: {price:8.2f} {ctype_short} |{htf_str}{ltf_str} | ⚪ Pos: 0", flush=True)
    return True

def main():
    logger.info("--------------------------------")
    logger.info("   Options Bot - V1.0 (Live)    ")
    logger.info("--------------------------------")
    
    # 1. Initialize API Client (Auto-select based on live_trading flag)
    import uuid
    session_id = str(uuid.uuid4())[:8]
    logger.info(f"Session ID: {session_id} | Time: {time.ctime()}")
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
    
    # 2.5 Sync existing positions if live
    if live_trading:
        om.sync_positions()
    strat = StrategyEngine()
    pm = PullbackManager() # Initialize Pullback Manager
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
    htf_indicators = [x for x in (config.get("active_indicators.htf") or []) if x]
    ltf_indicators = [x for x in (config.get("active_indicators.ltf") or []) if x]
    logger.info(f"  HTF ({config.get('strategy_settings.timeframe_htf')}):    {', '.join(htf_indicators).upper()}")
    logger.info(f"  LTF ({config.get('strategy_settings.timeframe_ltf')}):    {', '.join(ltf_indicators).upper()}")
    
    pb_enabled = config.get("strategy_settings.pullback_strategy_settings.enabled", False)
    logger.info(f"  Pullback Logic: {'ENABLED ✅' if pb_enabled else 'DISABLED ❌'}")
    
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
    logger.info(f"  Initial Stop Loss (Fix): {rm_cfg.get('entry_stop_loss_pct')}%")
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
                        # Fallback to last close from history if possible
                        if not data_ltf.empty:
                            spot_price = data_ltf['close'].iloc[-1]
                            logger.warning(f"⚠️ Could not fetch real-time LTP for {symbol}. Using last candle close: {spot_price}")
                        else:
                            logger.error(f"❌ Critical: Could not fetch LTP for {symbol}")
                            time.sleep(poll_interval)
                            continue
                    
                    # 2.2 Calculate ATR for Heartbeat
                    current_atr = 0
                    if not data_ltf.empty:
                        current_atr = data_ltf.iloc[-1].get('atr', 0)

                    if main.loop_count == 1:
                        logger.info(f"✅ Data synced successfully. Starting market monitoring.")
                        # Force heartbeat on first successful loop for feedback
                        main_heartbeat(0, symbol, spot_price, bool(ws_handler.authenticated), ltf_df=data_ltf, htf_df=data_htf, mode="LIVE", active_pos=om.active_positions, rm=rm, current_prices={}, wait_status="Syncing...", current_atr=current_atr)

                else:
                    # PAPER/MOCK MODE:
                    symbol = "NIFTY"
                    data_ltf = api.history(symbol, resolution="5", start=None, end=None)
                    data_htf = api.history(symbol, resolution="15", start=None, end=None)
                    spot_price = data_ltf['close'].iloc[-1]
                    current_atr = data_ltf.iloc[-1].get('atr', 0) if not data_ltf.empty else 0
                
                # 1.5 Handle Heikin-Ashi Conversion if requested
                candle_type = config.get("strategy_settings.candle_type", "OHLC")
                if candle_type == "HEIKIN_ASHI":
                    data_ltf = convert_to_heikin_ashi(data_ltf)
                    data_htf = convert_to_heikin_ashi(data_htf)

                # 2. Calculate Indicators (Only active ones)
                active_ltf = [x for x in (config.get("active_indicators.ltf") or []) if x]
                active_htf = [x for x in (config.get("active_indicators.htf") or []) if x]

                # 2.1 LTF Calculations
                ltf_df = data_ltf.copy()
                # Force dependencies for Pullback/Recovery Strategies
                pb_settings = config.get("strategy_settings.pullback_strategy_settings", {})
                rec_settings = config.get("strategy_settings.renter_trend_mode", {})
                
                # Need RSI check
                need_rsi = "rsi" in active_ltf or pb_settings.get("enabled", False) or rec_settings.get("enabled", False)
                
                # Debug Logging (Temporary)
                # logger.info(f"[DEBUG] Active: {active_ltf} | PB: {pb_settings.get('enabled')} | Rec: {rec_settings.get('enabled')} -> Need RSI: {need_rsi}")

                if "ema" in active_ltf or pb_settings.get("enabled", False):
                    # Pullback strategy usually needs EMA (EMA_TOUCH)
                    ltf_df = calculate_ema(ltf_df, 9)
                    ltf_df = calculate_ema(ltf_df, 21)
                
                if need_rsi:
                    ltf_df = calculate_rsi(ltf_df, 14)
                    # logger.info(f"[DEBUG] RSI Calc Done. Cols: {ltf_df.columns}")
                if "stochrsi" in active_ltf:
                    ltf_df = calculate_stochrsi(ltf_df)
                if "utbot" in active_ltf:
                    ltf_df = calculate_utbot(ltf_df, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr", 10))
                if "supertrend" in active_ltf:
                    from indicators import calculate_supertrend
                    ltf_df = calculate_supertrend(ltf_df, config.get("indicators.supertrend_period", 10), config.get("indicators.supertrend_multiplier", 3.0))
                
                # 2.2 HTF Calculations
                htf_df = data_htf.copy()
                if "ema" in active_htf:
                    htf_df = calculate_ema(htf_df, 9)
                    htf_df = calculate_ema(htf_df, 21)
                if "utbot" in active_htf:
                    htf_df = calculate_utbot(htf_df, config.get("indicators.utbot_key"), config.get("indicators.utbot_atr", 10))
                if "supertrend" in active_htf:
                    from indicators import calculate_supertrend
                    htf_df = calculate_supertrend(htf_df, config.get("indicators.supertrend_period", 10), config.get("indicators.supertrend_multiplier", 3.0))
                
                # --- Fetch Current Prices for Active Positions (Moved Up for Heartbeat) ---
                current_prices = {}
                if om.active_positions:
                    for pos in om.active_positions:
                        sym = pos['symbol']
                        # Default to NFO for options if exchange not found
                        exc = pos.get('exchange', 'NFO') 
                        ws_key = f"{sym}.{exc}"
                        
                        price = 0
                        if live_trading:
                            # 1. Try WebSocket (Priority)
                            ws_price = ws_handler.get_ltp(ws_key)
                            if ws_price:
                                price = ws_price
                            else:
                                # 2. Fallback to REST (only if WS has no data)
                                try:
                                    import time as t_time
                                    now = t_time.time()
                                    
                                    # Throttle REST calls slightly if failing? No, history is needed.
                                    # But we can throttle the ERRORS.
                                    q = api.get_ltp(sym, exchange=exc)
                                    price = q.get('ltp', 0)
                                    
                                    if not price:
                                        # Only log if not muted
                                        if now - api._last_error_time > api._error_mute_interval:
                                            logger.warning(f"⚠️ Unable to fetch price for {sym} (WS/REST both failed). Muting for {api._error_mute_interval}s.")
                                            api._last_error_time = now
                                except Exception as e:
                                    now = t_time.time()
                                    if now - api._last_error_time > api._error_mute_interval:
                                        logger.error(f"Error fetching price for {sym}: {e}")
                                        api._last_error_time = now
                        else:
                            # Mock Mode: Use Entry Price (or simulate based on Spot if needed)
                            price = pos.get('entry_price', 0)
                        
                        if price:
                            current_prices[sym] = price
                # -------------------------------------------------

                # Re-calculate ATR after indicators (in case ATR only just calculated)
                current_atr = ltf_df.iloc[-1].get('atr', 0) if not ltf_df.empty else 0

                # 2.3 Log Heartbeat (Now with HTF + LTF info)
                # Tune frequency: every 10 loops for start, then every 150 (~5 mins)
                hb_freq = 10 if main.loop_count < 100 else 150
                
                # Capture Wait Status for Heartbeat
                wait_status = None
                if not om.active_positions:
                    # 1. Detect LTF Trend
                    ltf_is_bullish, ltf_is_bearish = utils.detect_trend(ltf_df, active_ltf)
                    
                    # 2. Detect HTF Trend (Default to True if not used)
                    htf_is_bullish, htf_is_bearish = utils.detect_trend(htf_df, active_htf, default=True)
                    
                    # 3. Check for wait status
                    if htf_is_bullish and ltf_is_bullish:
                        wait_status = pm.get_wait_status('CE', spot_price, ltf_df)
                    elif htf_is_bearish and ltf_is_bearish:
                        wait_status = pm.get_wait_status('PE', spot_price, ltf_df)
                    else:
                        wait_status = "Waiting for Trend Alignment (Both Green/Red)"

                if main.loop_count % hb_freq == 0:
                     main_heartbeat(
                        0, # Force print trigger
                        symbol, 
                        spot_price, 
                        ws_active=bool(ws_handler.authenticated), 
                        ltf_df=ltf_df, 
                        htf_df=htf_df,
                        mode="LIVE" if live_trading else "PAPER",
                        active_pos=om.active_positions,
                        rm=rm,
                        current_prices=current_prices,
                        wait_status=wait_status,
                        current_atr=current_atr
                    )

                # 3. Strategy Logic (Entry & Reversal Exit)
                signal = strat.generate_signal(htf_df, ltf_df)
                

                
                # REVERSAL EXIT: Close position if signal flips against us
                if om.active_positions:
                    current_pos = om.active_positions[0] # Assuming single position for V1
                    pos_type = current_pos['type'] # CE or PE
                    
                    # Check if reversal exit should be triggered
                    reversal_detected = False
                    if pos_type == "CE" and signal == "PE_REVERSAL":
                        reversal_detected = True
                    elif pos_type == "PE" and signal == "CE_REVERSAL":
                        reversal_detected = True
                    
                    if reversal_detected:
                        # Apply Reversal Exit Refinement Checks
                        refinement_cfg = config.get("strategy_settings.reversal_exit_refinement", {})
                        allow_exit = True
                        
                        # Check 1: Minimum Hold Time
                        min_hold_cfg = refinement_cfg.get("min_hold_time", {})
                        if min_hold_cfg.get("enabled", False):
                            entry_time = current_pos.get('entry_time', 0)
                            hold_duration = time.time() - entry_time
                            min_seconds = min_hold_cfg.get("seconds", 180)
                            
                            if hold_duration < min_seconds:
                                allow_exit = False
                                # logger.debug(f"Reversal blocked: Hold time {hold_duration:.0f}s < {min_seconds}s")
                        
                        # Check 2: Confirmation Candles
                        confirm_cfg = refinement_cfg.get("confirmation_candles", {})
                        if confirm_cfg.get("enabled", False) and allow_exit:
                            required_count = confirm_cfg.get("count", 2)
                            om.reversal_confirmation_count += 1
                            
                            if om.reversal_confirmation_count < required_count:
                                allow_exit = False
                                # logger.debug(f"Reversal blocked: Confirmation {om.reversal_confirmation_count}/{required_count}")
                        
                        # Execute Exit if allowed
                        if allow_exit:
                            reversal_type = "RED" if signal == "PE_REVERSAL" else "GREEN"
                            logger.info(f"🔄 REVERSAL: Closing {current_pos['symbol']} because chart turned {reversal_type}")
                            om.close_position(current_pos, f"Reversal (Signal turned {reversal_type})")
                            rm.record_sell_order(current_pos['symbol'], (current_prices.get(current_pos['symbol'], current_pos['peak_price']) - current_pos['entry_price']) * current_pos['qty'])
                            om.consecutive_loss_count = 0 # Reset on Fresh Trend
                            om.reversal_confirmation_count = 0 # Reset confirmation counter
                    else:
                        # No reversal detected, reset confirmation counter
                        om.reversal_confirmation_count = 0
                
                # ALSO RESET if we are flat but signal flips (caught by Fresh Buy logic in strategy, but good to be explicit)
                # Note: 'signal' variable above is complex (dict or string). 
                # If signal is a dict (BUY CE/PE), it means strategy found a valid entry condition.
                # If we want to reset counter on "Fresh" signal, we can rely on order_manager or do it here.
                # For V1, the simplest way is: strategy.py 'fresh_buy' implies a flip. 
                # But 'signal' dict doesn't tell us if it was fresh or mid. 
                # Improvement: Let strategy return metadata or just rely on 'Win' to reset.
                # Actually, if trend flips, we MUST reset to allow the new trend to trade.
                # We can check previous signal state in main loop or just trust the 'close_position' reset above.
                # But what if we were flat?
                if isinstance(signal, dict):
                     # If we are entering a trade, we assume it's valid. 
                     # Only need to handle "Blocking" logic if count is high.
                     pass
                
                if not om.active_positions: # Only look for entry if flat
                    if signal and isinstance(signal, dict) and signal.get('action') == 'BUY':
                        is_fresh_alignment = False # Initialize to prevent NameError
                        
                        # Filter: Check if this option type is allowed
                        allowed_types = config.get("strategy_settings.allowed_option_types", ["CE", "PE"])
                        signal_type = signal.get('type')  # 'CE' or 'PE'
                        
                        if signal_type not in allowed_types:
                            # logger.debug(f"Signal {signal_type} filtered out. Allowed: {allowed_types}")
                            continue  # Skip this signal
                        
                        # ============================================================
                        # TREND-BASED ARCHITECTURE
                        # Entry Rule: HTF and LTF must BOTH be aligned (both Green or both Red)
                        # We don't care WHEN they flipped, only that they ARE aligned now.
                        # ============================================================
                        
                        # 1. Check Trend Alignment
                        # LTF Trend Detection
                        ltf_is_bullish, ltf_is_bearish = utils.detect_trend(ltf_df, active_ltf)
                        
                        # HTF Trend Detection (Default to True if not used)
                        htf_is_bullish, htf_is_bearish = utils.detect_trend(htf_df, active_htf, default=True)
                        
                        # 2. Determine if trends are aligned for this signal type
                        trends_aligned = False
                        if signal_type == 'CE':
                            trends_aligned = htf_is_bullish and ltf_is_bullish
                        elif signal_type == 'PE':
                            trends_aligned = htf_is_bearish and ltf_is_bearish
                        
                        if not trends_aligned:
                            continue  # WAIT for alignment
                        
                        # ============================================================
                        # CLEAN LOGIC: FRESH vs MID-TREND
                        # ============================================================
                        
                        # Calculate Fresh Alignment status (Did a flip JUST happen?)
                        ltf_just_flipped = False
                        if len(ltf_df) >= 2:
                            col_l = utils.get_signal_col(ltf_df, active_ltf)
                            if col_l:
                                curr_sig = ltf_df.iloc[-1][col_l]
                                prev_sig = ltf_df.iloc[-2][col_l]
                                ltf_just_flipped = (curr_sig == 1 and prev_sig != 1) or (curr_sig == -1 and prev_sig != -1)
                            
                        htf_just_flipped = False
                        if not htf_df.empty and len(htf_df) >= 2:
                            col_h = utils.get_signal_col(htf_df, active_htf)
                            if col_h:
                                curr_sig_h = htf_df.iloc[-1][col_h]
                                prev_sig_h = htf_df.iloc[-2][col_h]
                                htf_just_flipped = (curr_sig_h == 1 and prev_sig_h != 1) or (curr_sig_h == -1 and prev_sig_h != -1)

                        is_fresh_alignment = ltf_just_flipped or htf_just_flipped
                        
                        # B. Entry Decision Logic
                        should_enter = False
                        entry_reason = ""
                        
                        if is_fresh_alignment:
                             should_enter = True
                             entry_reason = "Fresh Trend Alignment"
                             om.consecutive_loss_count = 0 # Reset on Fresh
                        else:
                             # MID-TREND LOGIC (Unified Pullback)
                             # Applies to BOTH Startup (count==0) and Re-entries (count>0)
                             
                             pb_cfg = config.get("strategy_settings.pullback_strategy_settings", {})
                             if pb_cfg.get("enabled", False):
                                  if pm.is_pullback_valid(signal_type, spot_price, ltf_df):
                                      should_enter = True
                                      entry_reason = f"Mid-Trend Pullback ({pm.get_wait_status(signal_type, spot_price, ltf_df)})"
                                  else:
                                      entry_reason = f"Waiting for Pullback ({pm.get_wait_status(signal_type, spot_price, ltf_df)})"
                             else:
                                  entry_reason = "Pullback Strategy Disabled"
                        
                        # Execute Entry
                        
                        if should_enter:
                             if rm.check_pre_entry_risk():
                                  # Extract Candle Timestamp
                                  candle_time = None
                                  if not ltf_df.empty:
                                      if 'datetime' in ltf_df.columns:
                                          candle_time = str(ltf_df.iloc[-1]['datetime'])
                                      elif 'date' in ltf_df.columns:
                                          candle_time = str(ltf_df.iloc[-1]['date'])
                                          
                                  om.place_entry_order(signal, spot_price, candle_time, reason=entry_reason)
                
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
                import traceback
                # Specifically catch NameError for utils to see where it happens
                err_msg = traceback.format_exc()
                logger.error(f"❌ Main Loop Error [Session:{session_id}]: {e}")
                logger.error(f"Traceback:\n{err_msg}")
                time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("Stopping...")

if __name__ == "__main__":
    main()
