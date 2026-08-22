"""
NSE Index Constituents Fetcher
==============================
Fetches the list of stocks in any NSE index (Nifty50, Nifty200, BankNifty, etc.)
dynamically from the NiftyIndices CSV endpoints (highly reliable, no block/403).

Caching Strategy
----------------
Segment symbol lists are cached to a local JSON file (segment_cache.json) with
today's date. On the first call of the day the list is fetched from NiftyIndices;
every subsequent call that same day (or any restart) reads from the cache file.
This avoids repeated HTTP round-trips for the same segment on the same trading day.

Usage:
    from nse_indices import get_index_symbols
    symbols = get_index_symbols("NIFTY50")
"""

import json
import logging
import csv
import io
from datetime import date
from pathlib import Path

import requests

log = logging.getLogger("UTBotSRChannelsScanner")

# ---------------------------------------------------------------------------
# Daily cache file — stored alongside this module
# ---------------------------------------------------------------------------
_CACHE_FILE = Path(__file__).resolve().parent / "segment_cache.json"

# In-memory cache for the current process (avoids re-reading the file)
_memory_cache: dict = {}   # {"date": "YYYY-MM-DD", "segments": {SEGMENT: [...]}}


def _today() -> str:
    return date.today().isoformat()


def _load_cache() -> dict:
    """Load cache from file if it exists and is dated today."""
    global _memory_cache
    if _memory_cache.get("date") == _today():
        return _memory_cache

    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("date") == _today():
                _memory_cache = data
                return _memory_cache
        except Exception as exc:
            log.debug("Could not read segment cache file: %s", exc)

    return {}


def _load_full_cache() -> dict:
    """Load the raw cache file regardless of date — used for stale fallback access."""
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            log.debug("Could not read segment cache file for fallback: %s", exc)
    return {}


def _save_cache(segment_key: str, symbols: list[str]) -> None:
    """Save fetched symbols for a segment into the daily cache file.

    Also updates the 'previous' fallback layer so the data survives the next
    day's cache expiry.  Structure:
        {
            "date": "YYYY-MM-DD",
            "segments": { "NIFTY50": [...], ... },
            "previous": { "NIFTY50": {"date": "YYYY-MM-DD", "symbols": [...]}, ... }
        }
    """
    global _memory_cache

    cache = _load_cache()
    if not cache or cache.get("date") != _today():
        cache = {"date": _today(), "segments": {}}

    cache["segments"][segment_key] = symbols

    # Always persist the freshly fetched list as a dated fallback entry
    if "previous" not in cache:
        cache["previous"] = {}
    cache["previous"][segment_key] = {"date": _today(), "symbols": symbols}

    _memory_cache = cache

    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
    except Exception as exc:
        log.debug("Could not write segment cache file: %s", exc)


# ---------------------------------------------------------------------------
# Map user-friendly names to niftyindices.com CSV files
# ---------------------------------------------------------------------------
INDEX_CSV_MAP = {
    # Broad market
    "NIFTY50":             "ind_nifty50list.csv",
    "NIFTY 50":            "ind_nifty50list.csv",
    "NIFTY100":            "ind_nifty100list.csv",
    "NIFTY 100":           "ind_nifty100list.csv",
    "NIFTY200":            "ind_nifty200list.csv",
    "NIFTY 200":           "ind_nifty200list.csv",
    "NIFTY500":            "ind_nifty500list.csv",
    "NIFTY 500":           "ind_nifty500list.csv",
    "NIFTYNEXT50":         "ind_niftynext50list.csv",
    "NIFTY NEXT 50":       "ind_niftynext50list.csv",
    "NIFTYMIDCAP50":       "ind_niftymidcap50list.csv",
    "NIFTY MIDCAP 50":     "ind_niftymidcap50list.csv",
    "NIFTYMIDCAP100":      "ind_niftymidcap100list.csv",
    "NIFTY MIDCAP 100":    "ind_niftymidcap100list.csv",
    "NIFTYSMLCAP50":       "ind_niftysmallcap50list.csv",
    "NIFTYSMLCAP100":      "ind_niftysmallcap100list.csv",
    "NIFTY SMALLCAP 100":  "ind_niftysmallcap100list.csv",

    # Sectoral
    "BANKNIFTY":           "ind_niftybanklist.csv",
    "NIFTYBANK":           "ind_niftybanklist.csv",
    "NIFTY BANK":          "ind_niftybanklist.csv",
    "NIFTYIT":             "ind_niftyitlist.csv",
    "NIFTY IT":            "ind_niftyitlist.csv",
    "NIFTYPHARMA":         "ind_niftypharmalist.csv",
    "NIFTY PHARMA":        "ind_niftypharmalist.csv",
    "NIFTYMETAL":          "ind_niftymetallist.csv",
    "NIFTY METAL":         "ind_niftymetallist.csv",
    "NIFTYAUTO":           "ind_niftyautolist.csv",
    "NIFTY AUTO":          "ind_niftyautolist.csv",
    "NIFTYFMCG":           "ind_niftyfmcglist.csv",
    "NIFTY FMCG":          "ind_niftyfmcglist.csv",
    "NIFTYENERGY":         "ind_niftyenergylist.csv",
    "NIFTY ENERGY":        "ind_niftyenergylist.csv",
    "NIFTYREALTY":         "ind_niftyrealtylist.csv",
    "NIFTY REALTY":        "ind_niftyrealtylist.csv",
    "NIFTYMEDIA":          "ind_niftymedialist.csv",
    "NIFTY MEDIA":         "ind_niftymedialist.csv",
    "NIFTYFINSERV":        "ind_niftyfinancelist.csv",
    "NIFTY FIN SERVICE":   "ind_niftyfinancelist.csv",

    # Thematic
    "NIFTYCOMMODITIES":    "ind_niftycommoditieslist.csv",
    "NIFTY COMMODITIES":   "ind_niftycommoditieslist.csv",
    "NIFTYINFRA":          "ind_niftyinfralist.csv",
    "NIFTY INFRA":         "ind_niftyinfralist.csv",
    "NIFTYPSE":            "ind_niftypselist.csv",
    "NIFTY PSE":           "ind_niftypselist.csv",
    "NIFTYPSUBANK":        "ind_niftypsubanklist.csv",
    "NIFTY PSU BANK":      "ind_niftypsubanklist.csv",
    "NIFTYPVTBANK":        "ind_niftyprivatebanklist.csv",
    "NIFTY PVT BANK":      "ind_niftyprivatebanklist.csv",
    "NIFTY PRIVATE BANK":  "ind_niftyprivatebanklist.csv",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_from_niftyindices(csv_filename: str) -> list[str]:
    """Fetch and parse a constituent CSV list from NiftyIndices.com."""
    url = f"https://www.niftyindices.com/IndexConstituent/{csv_filename}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()

        f = io.StringIO(resp.text)
        reader = csv.DictReader(f)

        symbols = []
        for row in reader:
            symbol = row.get("Symbol")
            if symbol:
                symbol = symbol.strip()
                # Fix renamed / relisted tickers
                if symbol == "TATAMOTORS":
                    symbol = "TATAMTRDVT"
                symbols.append(symbol)

        return sorted(list(set(symbols)))

    except Exception as exc:
        log.error("Failed to fetch CSV '%s' from NiftyIndices: %s", csv_filename, exc)
        return []


def get_index_symbols(segment: str) -> list[str]:
    """
    Return the list of NSE stock symbols for a given index segment name.

    Symbols are cached per segment per calendar day in segment_cache.json.
    The remote endpoint is only called once per segment per day, regardless of
    how many times the scanner is run or restarted.

    Parameters
    ----------
    segment : str
        Index name, e.g. "NIFTY50", "BANKNIFTY", "NIFTY100".

    Returns
    -------
    list[str]
        Sorted list of NSE ticker symbols, or [] on failure.
    """
    key = segment.strip().upper()

    # --- 1. Check in-memory / file cache first ---
    cache = _load_cache()
    cached_symbols = cache.get("segments", {}).get(key)
    if cached_symbols:
        log.info(
            "Using cached constituents for '%s' (%d symbols, date: %s).",
            segment, len(cached_symbols), cache.get("date"),
        )
        return cached_symbols

    # --- 2. Cache miss — fetch from NiftyIndices ---
    csv_file = INDEX_CSV_MAP.get(key)
    if not csv_file:
        log.warning("Segment '%s' is not supported.", segment)
        return []

    log.info("Fetching constituents for '%s' from NiftyIndices CSV...", segment)
    symbols = fetch_from_niftyindices(csv_file)

    if symbols:
        log.info(
            "Successfully fetched %d symbols for '%s'. Caching for today (%s).",
            len(symbols), segment, _today(),
        )
        _save_cache(key, symbols)
        return symbols

    # --- 3. HTTP fetch failed — try the 'previous' fallback layer ---
    log.warning("Segment '%s' fetch failed or returned empty list. Checking stale fallback...", segment)
    full_cache = _load_full_cache()
    prev_entry = full_cache.get("previous", {}).get(key)
    if prev_entry and prev_entry.get("symbols"):
        stale_date = prev_entry.get("date", "unknown date")
        stale_syms = prev_entry["symbols"]
        log.warning(
            "Using stale fallback for '%s': %d symbols last fetched on %s. "
            "Live data unavailable — scanner will run on cached constituent list.",
            segment, len(stale_syms), stale_date,
        )
        return stale_syms

    log.warning("No stale fallback available for '%s'. Returning empty list.", segment)
    return []


def list_available_segments() -> list[str]:
    """Return a sorted list of supported segment short-names (no spaces)."""
    seen = set()
    short_names = []
    for key in INDEX_CSV_MAP:
        if " " not in key and key not in seen:
            seen.add(key)
            short_names.append(key)
    return sorted(short_names)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    seg = sys.argv[1] if len(sys.argv) > 1 else "NIFTY50"
    syms = get_index_symbols(seg)
    if syms:
        print(f"\n{seg} ({len(syms)} symbols): {', '.join(syms[:10])}...")
    else:
        print(f"Failed to fetch {seg}")
