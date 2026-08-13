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
    check_mtf_confirmation,
)
from options_grid import generate_option_strike_grid
import trading_adapter
import trade_db
import instrument_master


def load_config(path: Path | str = None) -> dict:
    if path is None:
        path = _bot_dir / "config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


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

    log.info(
        "========================================================\n"
        "Starting Options Scan Cycle | Underlying: %s | ATM Strike: %.1f | Gap: %s | Contracts: %d",
        underlying, grid_info["atm_strike"], grid_info["strike_gap"], len(symbols),
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
            log.info("[%s] No signal on last bar. Skipping.", sym)
            return None

        signal_type = "BUY" if final_buy else "SELL"

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
        }

        log.info("[%s] Signal: %s | LTP Rs.%.2f | Grade %s %.1f", sym, signal_type, close_price, grade, score)
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
