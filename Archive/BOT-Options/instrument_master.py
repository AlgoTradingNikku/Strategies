"""
instrument_master.py
=====================
Options-contract metadata resolver for BOT-Options.

Answers one question well: given a trading symbol (e.g. "NIFTY30MAR2624500PE"),
what is its underlying, strike, option type, expiry date and lot size?

Resolution order
-----------------
  1. instruments_cache.pkl  — the OpenAlgo instrument master (authoritative).
     A pandas DataFrame with columns:
       symbol, exchange, name, strike, expiry, instrumenttype, lotsize,
       tick_size, token, brsymbol, brexchange
     Expiry is stored as a "DD-MON-YY" string (e.g. "30-MAR-26").
     This is the source of truth — lot sizes get revised by NSE periodically
     and this cache should be refreshed from OpenAlgo whenever that happens.

  2. Regex fallback — parses the standard NSE contract naming convention
     (UNDERLYING + DDMMMYY + STRIKE + CE/PE) when the symbol isn't found in
     the cache (e.g. cache is stale, or a brand new weekly contract). Lot
     size in this path comes from a small last-resort static map and is
     logged as a warning since it can silently go stale.

  3. Unresolved — the symbol looks like an option but neither path could
     identify it. Callers get an explicit `resolved=False` rather than a
     guessed number, so the dashboard can show "—" instead of a wrong P&L.

Equity symbols (no option suffix) resolve with is_option=False; lot size is
not meaningful for them and the field is left as None.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("UTBot.TradeManagement")

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - zoneinfo always available on py3.9+
    _IST = None

# ---------------------------------------------------------------------------
# Contract metadata result
# ---------------------------------------------------------------------------


@dataclass
class ContractInfo:
    symbol: str
    exchange: str
    is_option: bool
    resolved: bool                       # True if we found real metadata
    underlying: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None    # "CE" | "PE"
    expiry: Optional[date] = None
    lot_size: Optional[int] = None
    source: str = "unresolved"           # "cache" | "regex" | "unresolved" | "equity"


# ---------------------------------------------------------------------------
# Last-resort static lot-size map
# ---------------------------------------------------------------------------
# Only used when the symbol isn't in instruments_cache.pkl AND the regex
# parser identified the underlying. NSE revises these periodically (last
# major revision: NIFTY 25->75, BANKNIFTY ~35->15, effective 2025). Treat
# this as a stopgap, not a source of truth — refresh instruments_cache.pkl
# instead of editing these numbers when a mismatch is suspected.
_FALLBACK_LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 65,
    "MIDCPNIFTY": 140,
    "SENSEX": 20,
    "BANKEX": 30,
}

# Standard NSE/BSE option contract pattern: UNDERLYING + DDMMMYY + STRIKE + CE/PE
_OPT_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z]+)"
    r"(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yr>\d{2})"
    r"(?P<strike>\d+(?:\.\d+)?)"
    r"(?P<type>CE|PE)$"
)


class InstrumentMaster:
    """
    Loads and indexes instruments_cache.pkl. Thread-safe, lazy, auto-reloads
    when the file's mtime changes (checked at most once every
    `_RELOAD_CHECK_INTERVAL` seconds to avoid stat()-ing on every tick).
    """

    _RELOAD_CHECK_INTERVAL = 30.0

    def __init__(self, cache_path: Path):
        self._cache_path = cache_path
        self._lock = threading.Lock()
        self._index: dict[tuple[str, str], dict] = {}
        self._loaded_mtime: Optional[float] = None
        self._last_check: float = 0.0
        self._load_error_logged = False

    # -- loading ------------------------------------------------------------

    def _maybe_reload(self) -> None:
        import time as _time
        now = _time.time()
        if now - self._last_check < self._RELOAD_CHECK_INTERVAL and self._index:
            return
        self._last_check = now

        if not self._cache_path.exists():
            if not self._load_error_logged:
                log.warning(
                    "[InstrumentMaster] %s not found — lot size/expiry lookups "
                    "will rely on the regex fallback only.",
                    self._cache_path,
                )
                self._load_error_logged = True
            return

        try:
            mtime = self._cache_path.stat().st_mtime
        except OSError:
            return

        if mtime == self._loaded_mtime:
            return  # unchanged since last load

        try:
            import pandas as pd
            df = pd.read_pickle(self._cache_path)
            index: dict[tuple[str, str], dict] = {}
            for row in df.itertuples(index=False):
                r = row._asdict() if hasattr(row, "_asdict") else dict(zip(df.columns, row))
                sym = r.get("symbol")
                exch = r.get("exchange")
                if not sym or not exch:
                    continue
                index[(str(sym), str(exch))] = r

            with self._lock:
                self._index = index
                self._loaded_mtime = mtime
            log.info(
                "[InstrumentMaster] Loaded %d instruments from %s",
                len(index), self._cache_path,
            )
            self._load_error_logged = False
        except Exception as exc:
            log.error("[InstrumentMaster] Failed to load %s: %s", self._cache_path, exc)

    # -- lookup ---------------------------------------------------------------

    def lookup(self, symbol: str, exchange: str = "NFO") -> ContractInfo:
        self._maybe_reload()

        with self._lock:
            row = self._index.get((symbol, exchange))

        if row is not None:
            expiry_raw = row.get("expiry")
            expiry_val = _parse_cache_expiry(expiry_raw)
            instrumenttype = str(row.get("instrumenttype") or "")
            is_option = instrumenttype in ("OPTIDX", "OPTSTK")
            return ContractInfo(
                symbol=symbol,
                exchange=exchange,
                is_option=is_option,
                resolved=True,
                underlying=row.get("name"),
                strike=float(row["strike"]) if row.get("strike") not in (None, "") else None,
                option_type=_infer_option_type(symbol, row),
                expiry=expiry_val,
                lot_size=int(row["lotsize"]) if row.get("lotsize") not in (None, "") else None,
                source="cache",
            )

        # Not in cache — try the regex fallback for standard option symbols
        m = _OPT_SYMBOL_RE.match(symbol)
        if not m:
            # Doesn't look like an option contract at all — treat as equity/other
            return ContractInfo(
                symbol=symbol, exchange=exchange, is_option=False,
                resolved=True, source="equity",
            )

        underlying = m.group("underlying")
        try:
            expiry_val = datetime.strptime(
                f"{m.group('day')}{m.group('mon')}{m.group('yr')}", "%d%b%y"
            ).date()
        except ValueError:
            expiry_val = None

        lot_size = _FALLBACK_LOT_SIZES.get(underlying)
        if lot_size is not None:
            log.warning(
                "[InstrumentMaster] %s not found in instruments_cache.pkl — using "
                "static fallback lot size %d for %s. Refresh the cache to confirm "
                "this is still correct.",
                symbol, lot_size, underlying,
            )

        return ContractInfo(
            symbol=symbol,
            exchange=exchange,
            is_option=True,
            resolved=lot_size is not None and expiry_val is not None,
            underlying=underlying,
            strike=float(m.group("strike")),
            option_type=m.group("type"),
            expiry=expiry_val,
            lot_size=lot_size,
            source="regex",
        )


def _infer_option_type(symbol: str, row: dict) -> Optional[str]:
    if symbol.endswith("CE"):
        return "CE"
    if symbol.endswith("PE"):
        return "PE"
    return None


def _parse_cache_expiry(expiry_raw) -> Optional[date]:
    if not expiry_raw:
        return None
    if isinstance(expiry_raw, date):
        return expiry_raw
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(expiry_raw).strip(), fmt).date()
        except ValueError:
            continue
    log.debug("[InstrumentMaster] Could not parse expiry value: %r", expiry_raw)
    return None


# ---------------------------------------------------------------------------
# Expiry countdown helper — used directly by server.py for dashboard payloads
# ---------------------------------------------------------------------------

def expiry_countdown(expiry_val: Optional[date]) -> dict:
    """
    Return a dashboard-ready countdown block for an expiry date.

    {
      "expiry": "2026-03-30",
      "days_left": 2,
      "label": "2d 4h",
      "urgency": "safe" | "near" | "today" | "expired" | "unknown"
    }
    """
    if expiry_val is None:
        return {"expiry": None, "days_left": None, "label": "—", "urgency": "unknown"}

    now = datetime.now(_IST) if _IST else datetime.now()
    # Options expire at end of the trading session (15:30 IST) on expiry day
    expiry_dt = _combine_expiry(expiry_val, now)

    delta = expiry_dt - now
    total_seconds = delta.total_seconds()

    if total_seconds <= 0:
        return {
            "expiry": expiry_val.isoformat(), "days_left": 0,
            "label": "Expired", "urgency": "expired",
        }

    days = int(total_seconds // 86400)
    hours = int((total_seconds % 86400) // 3600)

    if days >= 1:
        label = f"{days}d {hours}h"
    else:
        minutes = int((total_seconds % 3600) // 60)
        label = f"{hours}h {minutes}m"

    if total_seconds <= 3600 * 6:      # <= 6h left (typically expiry-day afternoon)
        urgency = "today"
    elif days < 1:
        urgency = "today"
    elif days <= 2:
        urgency = "near"
    else:
        urgency = "safe"

    return {
        "expiry": expiry_val.isoformat(),
        "days_left": days,
        "label": label,
        "urgency": urgency,
    }


def _combine_expiry(expiry_val: date, reference_now: datetime) -> datetime:
    tzinfo = reference_now.tzinfo
    return datetime(
        expiry_val.year, expiry_val.month, expiry_val.day, 15, 30, 0,
        tzinfo=tzinfo,
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "instruments_cache.pkl"
_master: Optional[InstrumentMaster] = None
_master_lock = threading.Lock()


def get_instrument_master(cache_path: Optional[str] = None) -> InstrumentMaster:
    """Return the process-wide InstrumentMaster singleton (created on first use)."""
    global _master
    with _master_lock:
        if _master is None:
            path = Path(cache_path).expanduser() if cache_path else _DEFAULT_CACHE_PATH
            _master = InstrumentMaster(path)
        return _master
