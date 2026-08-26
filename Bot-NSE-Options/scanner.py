"""
===============================================================================
  Bot-NSE-Options — UTBot + S/R Channels Options Scanner Engine
===============================================================================

Scans 3 levels up & 3 levels down NSE Option strike contracts (14 contracts: 7 CE + 7 PE)
for Buy/Sell entry signals with setup scoring (Grade A/B/C/D), confluence matrix, and filter rules.
Flushes logs immediately to stdout and scanner.log for real-time console output.
"""

import sys
import copy
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yaml

_bot_dir = Path(__file__).resolve().parent

# Ensure UTF-8 output streams on Windows to prevent UnicodeEncodeError
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    import io as _io
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure UTF-8 logging/output
log = logging.getLogger("UTBotSRChannelsScanner")
log.setLevel(logging.INFO)
for h in list(log.handlers):
    log.removeHandler(h)

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
            self.flush()
        except Exception:
            pass

class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        try:
            super().emit(record)
            self.flush()
        except Exception:
            pass

console_handler = FlushStreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(console_handler)

file_handler = FlushFileHandler(_bot_dir / "scanner.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
log.addHandler(file_handler)
log.propagate = False

sys.path.insert(0, str(_bot_dir))
from telegram import send_telegram_alert
from signal_db import log_signals_batch
from signals import (
    compute_utbot_signals,
    compute_sr_signals,
    evaluate_composite_signals,
    calculate_risk_reward,
)
from options_grid import generate_option_strike_grid
import trading_adapter
import trade_db
import instrument_master
import risk_manager
import signal_quality
import position_sizer
import alpha_enhancers


def load_config(path: Path | str = None) -> dict:
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # [Sprint-5] Overlay secrets from environment / .env before returning.
    try:
        from secrets_loader import apply_env_overrides
        apply_env_overrides(cfg)
    except Exception as _exc:  # fail-open: keep yaml values if secrets_loader breaks
        log.debug("[config] secrets overlay skipped: %s", _exc)
    return cfg


def fetch_history(symbol: str, timeframe: str, config: dict, exchange: str = "NFO") -> pd.DataFrame | None:
    """Fetch historical OHLCV data for an option or index contract via OpenAlgo."""
    lookback_days = int(config.get("data", {}).get("lookback_days", 5))

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        from openalgo import api as oa_api
        oa_cfg = config.get("openalgo", {})
        client = oa_api(
            api_key=oa_cfg.get("apikey", ""),
            host=oa_cfg.get("base_url", "http://127.0.0.1:5000"),
        )

        raw = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=timeframe,
            start_date=start_str,
            end_date=end_str,
        )

        if isinstance(raw, pd.DataFrame):
            df = raw
        elif isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, pd.DataFrame):
                df = data
            elif isinstance(data, list) and data:
                df = pd.DataFrame(data)
            else:
                return None
        else:
            return None

        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]

        time_cols = [c for c in df.columns if c in ("time", "timestamp", "datetime", "date")]
        if time_cols:
            df[time_cols[0]] = pd.to_datetime(df[time_cols[0]])
            df = df.set_index(time_cols[0])

        req_cols = ["open", "high", "low", "close"]
        for col in req_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=req_cols)
        return df if not df.empty else None

    except Exception as e:
        log.warning("[%s] Fetch history error: %s", symbol, e)
        return None


def fetch_indices_quotes(config: dict) -> dict:
    """Fetch live quote ticks for Nifty 50, Bank Nifty, and Nifty IT."""
    indices = {
        "nifty": {"val": 24386.95, "chg": "-49.00 (-0.20%)", "color": "red"},
        "banknifty": {"val": 57631.25, "chg": "-254.60 (-0.44%)", "color": "red"},
        "niftyit": {"val": 31419.20, "chg": "+86.65 (+0.28%)", "color": "green"},
    }
    for idx_key, sym_name in [("nifty", "NIFTY"), ("banknifty", "BANKNIFTY"), ("niftyit", "NIFTYIT")]:
        ltp = trading_adapter.get_ltp(config, sym_name, exchange="NSE_INDEX")
        if ltp > 0:
            indices[idx_key]["val"] = round(ltp, 2)
    return indices


def run_scan(config: dict = None) -> dict:
    """
    Run single scan across all 14 contracts in the active options strike grid.
    Returns results dictionary populating dashboard tables.
    """
    if config is None:
        config = load_config()

    opt_cfg = config.get("options", {})
    underlying = opt_cfg.get("underlying", "NIFTY")
    index_exchange = opt_cfg.get("index_exchange", "NSE_INDEX")
    option_exchange = opt_cfg.get("option_exchange", "NFO")
    base_atm_strike = opt_cfg.get("base_atm_strike", "")
    levels_up_down = int(opt_cfg.get("levels_up_down", 3))
    strike_gap = opt_cfg.get("strike_gap", 50)
    timeframe = opt_cfg.get("timeframe", "5m")
    signal_mode = config.get("signal_mode", "UTBot")

    grid_info = generate_option_strike_grid(
        base_symbol_or_params=base_atm_strike,
        levels_up_down=levels_up_down,
        configured_gap=strike_gap,
        exchange=index_exchange,
        config=config,
    )

    symbols = grid_info["symbols"]
    im = instrument_master.get_instrument_master()
    
    # Get signal evaluation settings for logging
    ut_cfg = config.get("strategy", {})
    lookback_candles = int(opt_cfg.get("signal_lookback_candles", 2))
    
    # Handle both old and new config naming
    if "signal_on_closed_bar" in ut_cfg:
        signal_on_closed_bar = bool(ut_cfg.get("signal_on_closed_bar", True))
    else:
        signal_on_closed_bar = not bool(ut_cfg.get("signal_on_running_bar", False))
    
    bar_mode = "Closed-bar only (TradingView parity)" if signal_on_closed_bar else "Running-bar included"

    log.info(
        "========================================================\n"
        "Starting Options Scan Cycle | Underlying: %s | ATM Strike: %.1f | Gap: %s | Contracts: %d\n"
        "Signal Mode: %s | Timeframe: %s | Lookback: %d candles | Bar Mode: %s",
        underlying, grid_info["atm_strike"], grid_info["strike_gap"], len(symbols),
        signal_mode, timeframe, lookback_candles, bar_mode,
    )

    buy_results = []
    sell_results = []
    signals_to_log = []

    def process_symbol(sym: str) -> dict | None:
        c_info = im.lookup(sym, exchange=option_exchange)
        option_type = c_info.option_type or ("CE" if sym.endswith("CE") else "PE")

        df = fetch_history(sym, timeframe, config, exchange=option_exchange)

        if df is None or len(df) < 5:
            # Not enough data to run engines — skip this contract
            log.warning("[%s] Insufficient history (%s bars). Skipping.", sym, len(df) if df is not None else 0)
            return None

        df_sig = evaluate_composite_signals(df, signal_mode=signal_mode, cfg=config)
        last_bar = df_sig.iloc[-1]

        close_price = float(last_bar["close"])

        # final_buy / final_sell are the authoritative signals set by evaluate_composite_signals
        # They are True ONLY on the bar where a UTBot crossover fires (not on every trending bar)
        final_buy  = bool(last_bar.get("final_buy",  False))
        final_sell = bool(last_bar.get("final_sell", False))
        ut_buy     = bool(last_bar.get("ut_buy",  False))
        ut_sell    = bool(last_bar.get("ut_sell", False))

        score      = df_sig.attrs.get("setup_score", 75.0)
        grade      = df_sig.attrs.get("grade", "B")
        confluence = df_sig.attrs.get("confluence", {})

        # Only emit a result if the engine fired a signal on this bar
        if not final_buy and not final_sell:
            # Updated to use new config naming
            ut_cfg = config.get("strategy", {})
            if "signal_on_closed_bar" in ut_cfg:
                bar_label = "last completed bar" if ut_cfg.get("signal_on_closed_bar", True) else "running bar"
            else:
                # Backward compatibility with old naming
                bar_label = "running bar" if ut_cfg.get("signal_on_running_bar", False) else "last completed bar"
            log.info("[%s] No signal on %s. Skipping.", sym, bar_label)
            return None

        signal_type = "BUY" if final_buy else "SELL"

        # ── [Sprint-2] Signal-Quality: compute ATR%, ADX, quote spread, then filter ──
        atr_pct_val = signal_quality.compute_atr_pct(df_sig)

        # Underlying ADX from index history (spot). Fail-open on errors.
        adx_val = 0.0
        try:
            spot_tf = opt_cfg.get("timeframe", "5m")
            df_spot = fetch_history(underlying, spot_tf, config, exchange=index_exchange)
            if df_spot is not None and len(df_spot) >= 30:
                adx_val = signal_quality.compute_adx(df_spot, period=14)
        except Exception as _adx_exc:
            log.debug("[%s] ADX compute skipped: %s", sym, _adx_exc)

        # Spread / OI check — best-effort quote fetch; missing quotes fail-open.
        quote_info = None
        try:
            quote_info = trading_adapter.get_quote(config, sym, exchange=option_exchange) if hasattr(trading_adapter, "get_quote") else None
        except Exception:
            quote_info = None
        spread_ok, spread_reason = signal_quality.check_spread_liquidity(config, quote_info, close_price)

        # Individual gate checks (early reject before scoring so we don't waste it)
        sq_cfg = config.get("signal_quality", {})
        sq_reject_reason = ""
        if sq_cfg.get("enabled", True):
            ok_atr, r_atr = signal_quality.check_atr_range(config, atr_pct_val)
            ok_adx, r_adx = signal_quality.check_adx_trend(config, adx_val)
            if not ok_atr:
                sq_reject_reason = r_atr
            elif not ok_adx:
                sq_reject_reason = r_adx
            elif not spread_ok:
                sq_reject_reason = spread_reason

        # MTF alignment flag from confluence (already computed by evaluate_composite_signals)
        mtf_pass = bool(confluence.get("mtf", True))
        sr_pass = bool(confluence.get("sr", False))
        vol_pass = bool(confluence.get("vol", True))
        ut_active_pos = int(df_sig["ut_pos"].iloc[-1]) if "ut_pos" in df_sig.columns else 0

        # ── [Sprint-2] Transparent weighted score + grade override ──
        sq_result = signal_quality.compute_signal_score(
            ut_fired=(ut_buy or ut_sell),
            ut_active_pos=ut_active_pos,
            sr_pass=sr_pass,
            mtf_pass=mtf_pass,
            vol_pass=vol_pass,
            adx=adx_val,
            atr_pct=atr_pct_val,
            spread_ok=spread_ok,
            cfg=config,
        )
        # Override the legacy score if signal_quality scoring is enabled
        if sq_cfg.get("scoring_enabled", True):
            score = sq_result["score"]
            grade = sq_result["grade"]

        # ── [Sprint-4] Alpha Enhancements — regime / session / POC / greeks ──
        try:
            regime, vix_val = alpha_enhancers.get_vix_regime(config)
        except Exception:
            regime, vix_val = "UNKNOWN", 0.0
        try:
            session_bucket = alpha_enhancers.get_session_bucket(config)
            session_bonus = alpha_enhancers.get_session_bonus(config, session_bucket)
        except Exception:
            session_bucket, session_bonus = "prime", 0.0

        # POC from the option's own intraday history (df is already fetched)
        try:
            poc_price = alpha_enhancers.compute_poc(
                df, price_bins=int(config.get("alpha_enhancers", {}).get("volume_profile", {}).get("price_bins", 40))
            )
        except Exception:
            poc_price = 0.0

        # Aggregate alpha filters
        try:
            alpha_res = alpha_enhancers.run_alpha_filters(
                config,
                price=close_price,
                poc=poc_price,
                quote=quote_info,
                mtf_results={},   # strict_mtf disabled by default; extend later if per-tf pass data available
            )
        except Exception as _aexc:
            log.debug("[%s] alpha_filters skipped: %s", sym, _aexc)
            alpha_res = {"reject_reason": "", "poc_distance_pct": 0.0, "greeks": {}, "mtf_strict_pass": True, "enabled": False}

        # Apply session bonus to score (bounded 0..100)
        if config.get("alpha_enhancers", {}).get("enabled", True) and session_bonus:
            score = max(0.0, min(100.0, float(score) + float(session_bonus)))
            if sq_cfg.get("scoring_enabled", True):
                grade = signal_quality.score_to_grade(score)

        alpha_reject_reason = alpha_res.get("reject_reason", "")

        rr = calculate_risk_reward(
            entry_price=close_price,
            signal_type=signal_type,
            stop_loss_pct=float(config.get("trade_management", {}).get("stop_loss_pct", 20.0)),
            target_pct=float(config.get("trade_management", {}).get("target_pct", 40.0)),
        )

        res = {
            "symbol": sym,
            "underlying": c_info.underlying or underlying,
            "option_type": option_type,
            "strike": c_info.strike or 0.0,
            "expiry": c_info.expiry.isoformat() if c_info.expiry else opt_cfg.get("expiry_date", ""),
            "lot_size": c_info.lot_size or 65,
            "price": close_price,
            "win_rate": f"{min(88, max(52, int(score)))}%",
            "setup_score": score,
            "grade": grade,
            "confluence": confluence,
            "final_buy": final_buy,
            "final_sell": final_sell,
            "signal_type": signal_type,
            "ut_buy": ut_buy,
            "ut_sell": ut_sell,
            "ut_trail": close_price * 0.9,
            "stop_loss": rr["stop_loss"],
            "target": rr["target"],
            "risk_reward": rr["risk_reward"],
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "timeframe": timeframe,
            # Sprint-2: signal-quality breakdown for dashboard transparency
            "atr_pct": round(atr_pct_val, 3),
            "adx": round(adx_val, 1),
            "score_breakdown": sq_result["breakdown"],
            "sq_reject_reason": sq_reject_reason,
            # [Sprint-4] Alpha enhancement telemetry
            "vix": round(vix_val, 2),
            "regime": regime,
            "session": session_bucket,
            "session_bonus": session_bonus,
            "poc": round(poc_price, 2),
            "poc_distance_pct": alpha_res.get("poc_distance_pct", 0.0),
            "delta": round(float(alpha_res.get("greeks", {}).get("delta", 0.0)), 3),
            "theta": round(float(alpha_res.get("greeks", {}).get("theta", 0.0)), 3),
            "alpha_reject_reason": alpha_reject_reason,
        }

        # ── [Sprint-3] Position sizing — compute dynamic quantity ─────────
        _lot = int(c_info.lot_size or 65)
        _sizing = position_sizer.compute_position_size(
            config,
            entry_price=close_price,
            stop_loss=rr["stop_loss"],
            lot_size=_lot,
            grade=grade,
        )
        res["position_sizing"] = _sizing
        res["sized_quantity"] = int(_sizing.get("quantity", 0) or 0)

        log.info("[%s] Signal: %s | LTP Rs.%.2f | Grade %s %.1f", sym, signal_type, close_price, grade, score)

        # Handle Automated Order Execution (Auto-Buy without manual intervention)
        trading_cfg = config.get("trading", {})
        trading_enabled = bool(trading_cfg.get("enabled", False))
        order_mode = str(trading_cfg.get("order_mode", "manual")).lower()
        allowed_actions = str(trading_cfg.get("allowed_actions", "BUY_ONLY")).upper()
        opt_trade_cfg = trading_cfg.get("options", {})

        if trading_enabled and order_mode == "auto":
            if allowed_actions == "BUY_ONLY" and signal_type != "BUY":
                log.info("[%s] Skipped auto order for %s signal (trading.allowed_actions = BUY_ONLY)", sym, signal_type)
            elif allowed_actions == "SELL_ONLY" and signal_type != "SELL":
                log.info("[%s] Skipped auto order for %s signal (trading.allowed_actions = SELL_ONLY)", sym, signal_type)
            else:
                # ── [Sprint-4] Alpha reject BEFORE Sprint-2/1/3 gates ──
                if alpha_reject_reason:
                    log.info("[%s] ✨ Alpha REJECT: %s", sym, alpha_reject_reason)
                    res["risk_block_reason"] = alpha_reject_reason
                    return res

                # ── [Sprint-2] Signal-quality reject BEFORE risk gates ──
                if sq_reject_reason:
                    log.info("[%s] 🎯 Signal-Quality REJECT: %s", sym, sq_reject_reason)
                    res["risk_block_reason"] = sq_reject_reason
                    return res

                # ── [Sprint-1] Risk Manager pre-trade gate ─────────────────────
                # Runs: kill-switch, market-hours, daily-loss-limit, min-grade,
                # directional-gate (spot trend), duplicate-entry / cool-down.
                allowed, reason = risk_manager.can_place_order(
                    cfg=config,
                    symbol=sym,
                    option_type=option_type,
                    signal_type=signal_type,
                    grade=grade,
                    score=score,
                )
                if not allowed:
                    log.info("[%s] 🛡️ Order BLOCKED by risk manager: %s", sym, reason)
                    res["risk_block_reason"] = reason
                    return res

                # ── [Sprint-3] Skip if sizer produced 0 quantity (below 1 lot / invalid) ──
                _sized_qty = int(res.get("sized_quantity", 0) or 0)
                if _sized_qty <= 0:
                    _ps_reason = _sizing.get("reason", "SIZING_ZERO")
                    log.info("[%s] 📏 Order BLOCKED by position sizer: %s", sym, _ps_reason)
                    res["risk_block_reason"] = f"SIZING_{_ps_reason}"
                    return res

                # Also enforce portfolio exposure with THIS specific premium
                _extra_prem = close_price * _sized_qty
                _ok_exp, _exp_reason = position_sizer.check_portfolio_exposure(config, extra_premium=_extra_prem)
                if not _ok_exp:
                    log.info("[%s] 📏 Order BLOCKED — %s", sym, _exp_reason)
                    res["risk_block_reason"] = _exp_reason
                    return res

                log.info("[%s] Auto-executing %s order via OpenAlgo without manual intervention...", sym, signal_type)
                order_req = {
                    "symbol": sym,
                    "exchange": option_exchange,
                    "action": signal_type,
                    "quantity": _sized_qty,
                    "product": str(opt_trade_cfg.get("product", "NRML")),
                    "price_type": str(opt_trade_cfg.get("price_type", "MARKET")),
                    "price": close_price,
                    "strategy": str(trading_cfg.get("strategy_name", "UTBot_Options")),
                }
                ord_res = trading_adapter.place_order(config, order_req)
                res["order_response"] = ord_res

                # Register position in trade database for trade_management monitor
                trade_id = trade_db.add_trade({
                    "order_id": ord_res.get("order_id") or f"AUTO_{int(datetime.now().timestamp()*1000)}",
                    "symbol": sym,
                    "exchange": option_exchange,
                    "action": signal_type,
                    "quantity": _sized_qty,
                    "entry_price": close_price,
                    "product": str(opt_trade_cfg.get("product", "NRML")),
                    "stop_loss": rr["stop_loss"],
                    "target": rr["target"],
                })
                res["trade_id"] = trade_id

                # Dispatch Telegram Notification
                tg_msg = (
                    f"🚀 <b>AUTOMATED ORDER EXECUTED</b>\n"
                    f"Symbol: <b>{sym}</b>\n"
                    f"Action: <b>{signal_type}</b>\n"
                    f"Price: ₹{close_price:.2f}\n"
                    f"Qty: {opt_trade_cfg.get('quantity', 65)}\n"
                    f"Grade: <b>{grade}</b> ({score:.1f})"
                )
                send_telegram_alert(config, tg_msg)

        return res

    with ThreadPoolExecutor(max_workers=min(10, len(symbols))) as executor:
        futures = {executor.submit(process_symbol, s): s for s in symbols}
        for future in as_completed(futures):
            try:
                r = future.result()
                if r:
                    # Route using actual signal from engine, not option type
                    if r["final_buy"]:
                        buy_results.append(r)
                        signals_to_log.append(r)
                    elif r["final_sell"]:
                        sell_results.append(r)
                        signals_to_log.append(r)
            except Exception as exc:
                log.error("Error processing contract: %s", exc)

    buy_results.sort(key=lambda x: -x["setup_score"])
    sell_results.sort(key=lambda x: -x["setup_score"])

    if signals_to_log:
        log_signals_batch(signals_to_log)

    # [Sprint-6] Emit metrics for accepted BUY / SELL signals.
    try:
        import metrics as _metrics
        for _r in buy_results:
            _metrics.record_signal("BUY", accepted=True)
        for _r in sell_results:
            _metrics.record_signal("SELL", accepted=True)
    except Exception:
        pass

    indices = fetch_indices_quotes(config)
    last_scan_time = datetime.now().strftime("%H:%M:%S")

    log.info("Scan Cycle Finished at %s | Scanned: %d | BUY Signals: %d | SELL Signals: %d", last_scan_time, len(symbols), len(buy_results), len(sell_results))
    log.info("========================================================\n")

    return {
        "timestamp": last_scan_time,
        "grid_info": grid_info,
        "buy_results": buy_results,
        "sell_results": sell_results,
        "total_scanned": len(symbols),
        "indices": indices,
    }


if __name__ == "__main__":
    res = run_scan()
    print(f"Scanned {res['total_scanned']} symbols. Buy: {len(res['buy_results'])}, Sell: {len(res['sell_results'])}")
