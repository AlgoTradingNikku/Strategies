"""
===============================================================================
  Bot-Options / core / expiry_manager.py
  Expiry calendar management — fetches live expiry dates from OpenAlgo,
  selects the target expiry based on config preference, and handles auto-roll.
===============================================================================
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NSE calendar constants
# ---------------------------------------------------------------------------
_EXPIRY_WEEKDAY = 3          # Thursday (Monday=0, Thursday=3)
_MONTHLY_MAP    = {1,2,3,4,5,6,7,8,9,10,11,12}  # all months

# ---------------------------------------------------------------------------
# TTL cache for expiry selection — keyed by (underlying, preference).
# Expiry dates change at most once a week; 10-minute cache eliminates
# repeated oa_client.expiry() calls on every scan tick.
# ---------------------------------------------------------------------------
_expiry_cache: dict = {}          # key → (result_tuple, mono_timestamp)
_EXPIRY_CACHE_TTL = 600           # seconds (10 minutes)


def _parse_nse_date(s: str) -> date:
    """Parse NSE expiry date strings in multiple formats.
    Handles: '10-JUL-25', '10-JUL-2025', '2025-07-10'.
    """
    s = s.strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse expiry date: {s!r}")


def _to_oa_expiry(d: date) -> str:
    """Convert a date object to OpenAlgo expiry format: '28OCT25'."""
    return d.strftime("%d%b%y").upper()          # e.g. 28OCT25


def get_expiry_dates(underlying: str, oa_client) -> list[date]:
    """
    Fetch all available option expiry dates for an underlying from OpenAlgo.
    Returns a list of date objects sorted ascending (nearest first).
    Falls back to an empty list on failure.
    """
    try:
        resp = oa_client.expiry(symbol=underlying, exchange="NFO", instrumenttype="options")
        raw = resp.get("data", []) if isinstance(resp, dict) else []
        dates = []
        for item in raw:
            try:
                dates.append(_parse_nse_date(item))
            except ValueError as e:
                log.debug("Skipping unparseable expiry %r: %s", item, e)
        dates.sort()
        return dates
    except Exception as e:
        log.error("Failed to fetch expiry dates for %s: %s", underlying, e)
        return []


def select_expiry(
    underlying: str,
    oa_client,
    preference: str = "WEEKLY",
    auto_roll_days: int = 1,
) -> Optional[tuple[date, str]]:
    """
    Select the target expiry based on preference and auto-roll logic.
    Result is cached for _EXPIRY_CACHE_TTL seconds — the underlying's expiry
    date list changes at most once a week so repeated broker API calls are
    unnecessary.

    Parameters
    ----------
    preference    : 'WEEKLY' | 'MONTHLY' | 'NEXT_WEEKLY' | 'NEXT_MONTHLY'
    auto_roll_days: Roll to next expiry when current expiry < N days away

    Returns
    -------
    Tuple of (date, oa_format_string) e.g. (date(2025,8,7), '07AUG25')
    or None if no expiry available.
    """
    cache_key = (underlying, preference, auto_roll_days)
    now_mono  = time.monotonic()
    cached    = _expiry_cache.get(cache_key)
    if cached and (now_mono - cached[1]) < _EXPIRY_CACHE_TTL:
        return cached[0]   # return cached result — no broker call

    all_expiries = get_expiry_dates(underlying, oa_client)
    if not all_expiries:
        log.warning("[%s] No expiry dates available from OpenAlgo.", underlying)
        return None

    today = date.today()
    roll_cutoff = today + timedelta(days=auto_roll_days)
    pref = preference.upper()

    # ---- classify expiries -------------------------------------------------
    weekly_expiries  = []
    monthly_expiries = []

    for d in all_expiries:
        if d <= today:
            continue  # already expired
        # NSE weekly options expire on Thursdays; monthly on last Thursday of month
        is_thursday = (d.weekday() == _EXPIRY_WEEKDAY)
        if not is_thursday:
            weekly_expiries.append(d)   # some indices expire on other days
            continue
        # Check if it's the last Thursday of the month
        next_thursday = d + timedelta(weeks=1)
        if next_thursday.month != d.month:
            monthly_expiries.append(d)
        else:
            weekly_expiries.append(d)

    # If classification is empty (e.g. all monthly), treat all as both
    if not weekly_expiries:
        weekly_expiries = list(all_expiries)
    if not monthly_expiries:
        monthly_expiries = list(all_expiries)

    def _nearest(lst: list[date], skip_rolling: bool = True) -> Optional[date]:
        """Return the nearest date, skipping those within auto_roll_days if skip_rolling."""
        for d in sorted(lst):
            if skip_rolling and d <= roll_cutoff:
                continue
            return d
        # If all are within roll period, return the very next one
        for d in sorted(lst):
            if d > today:
                return d
        return None

    if pref == "WEEKLY":
        chosen = _nearest(weekly_expiries)
    elif pref == "MONTHLY":
        chosen = _nearest(monthly_expiries)
    elif pref == "NEXT_WEEKLY":
        # Skip the current weekly and pick the one after
        near = _nearest(weekly_expiries, skip_rolling=False)
        idx = weekly_expiries.index(near) if near in weekly_expiries else -1
        chosen = weekly_expiries[idx + 1] if idx + 1 < len(weekly_expiries) else near
    elif pref == "NEXT_MONTHLY":
        near = _nearest(monthly_expiries, skip_rolling=False)
        idx = monthly_expiries.index(near) if near in monthly_expiries else -1
        chosen = monthly_expiries[idx + 1] if idx + 1 < len(monthly_expiries) else near
    else:
        log.warning("Unknown expiry_preference '%s', defaulting to WEEKLY.", pref)
        chosen = _nearest(weekly_expiries)

    if chosen is None:
        log.error("[%s] Could not select an expiry for preference '%s'.", underlying, pref)
        return None

    days_left = (chosen - today).days
    log.info("[%s] Selected expiry: %s (%d days left) [%s]", underlying, chosen, days_left, pref)
    result = (chosen, _to_oa_expiry(chosen))
    _expiry_cache[cache_key] = (result, now_mono)   # store in cache
    return result


def days_to_expiry(expiry: date) -> int:
    """Return calendar days until expiry (0 = expiry day, negative = already expired)."""
    return (expiry - date.today()).days


def is_expiry_day(expiry: date) -> bool:
    """True if today is the expiry date."""
    return date.today() == expiry
