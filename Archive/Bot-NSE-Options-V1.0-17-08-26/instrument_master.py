"""
instrument_master.py
=====================
Options-contract metadata resolver for Bot-NSE-Options.

Resolves underlying, strike, option type, expiry date, lot size, and tick size.
Supports instruments_cache.pkl or standard NSE option naming regex.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("UTBotSRChannelsScanner")

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    _IST = None


@dataclass
class ContractInfo:
    symbol: str
    exchange: str
    is_option: bool
    resolved: bool                       # True if metadata identified
    underlying: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None    # "CE" | "PE"
    expiry: Optional[date] = None
    lot_size: Optional[int] = None
    source: str = "unresolved"           # "cache" | "regex" | "unresolved" | "equity"


# Fallback lot sizes when cache missing
_FALLBACK_LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 15,
    "FINNIFTY": 65,
    "MIDCPNIFTY": 140,
    "SENSEX": 20,
    "BANKEX": 30,
}

_OPT_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z]+)"
    r"(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yr>\d{2})"
    r"(?P<strike>\d+(?:\.\d+)?)"
    r"(?P<type>CE|PE)$"
)


class InstrumentMaster:
    _RELOAD_CHECK_INTERVAL = 30.0

    def __init__(self, cache_path: Path):
        self._cache_path = cache_path
        self._lock = threading.Lock()
        self._index: dict[tuple[str, str], dict] = {}
        self._loaded_mtime: Optional[float] = None
        self._last_check: float = 0.0

    def _maybe_reload(self) -> None:
        import time as _time
        now = _time.time()
        if now - self._last_check < self._RELOAD_CHECK_INTERVAL and self._index:
            return
        self._last_check = now

        if not self._cache_path.exists():
            return

        try:
            mtime = self._cache_path.stat().st_mtime
            if mtime == self._loaded_mtime:
                return

            import pandas as pd
            df = pd.read_pickle(self._cache_path)
            index: dict[tuple[str, str], dict] = {}
            for row in df.itertuples(index=False):
                r = row._asdict() if hasattr(row, "_asdict") else dict(zip(df.columns, row))
                sym = r.get("symbol")
                exch = r.get("exchange")
                if sym and exch:
                    index[(str(sym), str(exch))] = r

            with self._lock:
                self._index = index
                self._loaded_mtime = mtime
        except Exception as exc:
            log.error("[InstrumentMaster] Error loading cache: %s", exc)

    def lookup(self, symbol: str, exchange: str = "NFO") -> ContractInfo:
        self._maybe_reload()

        with self._lock:
            row = self._index.get((symbol, exchange))

        if row is not None:
            expiry_raw = row.get("expiry")
            expiry_val = _parse_cache_expiry(expiry_raw)
            instrumenttype = str(row.get("instrumenttype") or "")
            is_option = instrumenttype in ("OPTIDX", "OPTSTK") or symbol.endswith(("CE", "PE"))
            return ContractInfo(
                symbol=symbol,
                exchange=exchange,
                is_option=is_option,
                resolved=True,
                underlying=row.get("name"),
                strike=float(row["strike"]) if row.get("strike") not in (None, "") else None,
                option_type="CE" if symbol.endswith("CE") else ("PE" if symbol.endswith("PE") else None),
                expiry=expiry_val,
                lot_size=int(row["lotsize"]) if row.get("lotsize") not in (None, "") else None,
                source="cache",
            )

        # Regex fallback
        m = _OPT_SYMBOL_RE.match(symbol)
        if not m:
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

        lot_size = _FALLBACK_LOT_SIZES.get(underlying, 65)

        return ContractInfo(
            symbol=symbol,
            exchange=exchange,
            is_option=True,
            resolved=expiry_val is not None,
            underlying=underlying,
            strike=float(m.group("strike")),
            option_type=m.group("type"),
            expiry=expiry_val,
            lot_size=lot_size,
            source="regex",
        )


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
    return None


def expiry_countdown(expiry_val: Optional[date]) -> dict:
    if expiry_val is None:
        return {"expiry": None, "days_left": None, "label": "—", "urgency": "unknown"}

    now = datetime.now(_IST) if _IST else datetime.now()
    expiry_dt = datetime(expiry_val.year, expiry_val.month, expiry_val.day, 15, 30, 0, tzinfo=now.tzinfo)
    delta = expiry_dt - now
    total_seconds = delta.total_seconds()

    if total_seconds <= 0:
        return {"expiry": expiry_val.isoformat(), "days_left": 0, "label": "Expired", "urgency": "expired"}

    days = int(total_seconds // 86400)
    hours = int((total_seconds % 86400) // 3600)
    minutes = int((total_seconds % 3600) // 60)

    label = f"{days}d {hours}h" if days >= 1 else f"{hours}h {minutes}m"
    urgency = "today" if (total_seconds <= 21600 or days < 1) else ("near" if days <= 2 else "safe")

    return {
        "expiry": expiry_val.isoformat(),
        "days_left": days,
        "label": label,
        "urgency": urgency,
    }


_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "instruments_cache.pkl"
_master: Optional[InstrumentMaster] = None
_master_lock = threading.Lock()


def get_instrument_master(cache_path: Optional[str] = None) -> InstrumentMaster:
    global _master
    with _master_lock:
        if _master is None:
            path = Path(cache_path).expanduser() if cache_path else _DEFAULT_CACHE_PATH
            _master = InstrumentMaster(path)
        return _master
