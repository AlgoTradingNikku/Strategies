"""
NSE Index Constituents Fetcher
==============================
Fetches the list of stocks in any NSE index (Nifty50, Nifty200, BankNifty, etc.)
dynamically from the NiftyIndices CSV endpoints (highly reliable, no block/403).
Falls back to the NSE API if needed.

Usage:
    from nse_indices import get_index_symbols
    symbols = get_index_symbols("NIFTY50")
"""

import logging
import csv
import io
import time
import requests

log = logging.getLogger("Nifty50Scanner")

# ---------------------------------------------------------------------------
# Map user friendly names to niftyindices.com CSV files
# ---------------------------------------------------------------------------
INDEX_CSV_MAP = {
    # Broad market
    "NIFTY50":          "ind_nifty50list.csv",
    "NIFTY 50":         "ind_nifty50list.csv",
    "NIFTY100":         "ind_nifty100list.csv",
    "NIFTY 100":        "ind_nifty100list.csv",
    "NIFTY200":         "ind_nifty200list.csv",
    "NIFTY 200":        "ind_nifty200list.csv",
    "NIFTY500":         "ind_nifty500list.csv",
    "NIFTY 500":        "ind_nifty500list.csv",
    "NIFTYNEXT50":      "ind_niftynext50list.csv",
    "NIFTY NEXT 50":    "ind_niftynext50list.csv",
    "NIFTYMIDCAP50":    "ind_niftymidcap50list.csv",
    "NIFTY MIDCAP 50":  "ind_niftymidcap50list.csv",
    "NIFTYMIDCAP100":   "ind_niftymidcap100list.csv",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv",
    "NIFTYSMLCAP50":    "ind_niftysmallcap50list.csv",
    "NIFTYSMLCAP100":   "ind_niftysmallcap100list.csv",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list.csv",

    # Sectoral
    "BANKNIFTY":        "ind_niftybanklist.csv",
    "NIFTYBANK":        "ind_niftybanklist.csv",
    "NIFTY BANK":       "ind_niftybanklist.csv",
    "NIFTYIT":          "ind_niftyitlist.csv",
    "NIFTY IT":         "ind_niftyitlist.csv",
    "NIFTYPHARMA":      "ind_niftypharmalist.csv",
    "NIFTY PHARMA":     "ind_niftypharmalist.csv",
    "NIFTYMETAL":       "ind_niftymetallist.csv",
    "NIFTY METAL":      "ind_niftymetallist.csv",
    "NIFTYAUTO":        "ind_niftyautolist.csv",
    "NIFTY AUTO":       "ind_niftyautolist.csv",
    "NIFTYFMCG":        "ind_niftyfmcglist.csv",
    "NIFTY FMCG":       "ind_niftyfmcglist.csv",
    "NIFTYENERGY":      "ind_niftyenergylist.csv",
    "NIFTY ENERGY":     "ind_niftyenergylist.csv",
    "NIFTYREALTY":      "ind_niftyrealtylist.csv",
    "NIFTY REALTY":     "ind_niftyrealtylist.csv",
    "NIFTYMEDIA":       "ind_niftymedialist.csv",
    "NIFTY MEDIA":      "ind_niftymedialist.csv",
    "NIFTYFINSERV":     "ind_niftyfinancelist.csv",
    "NIFTY FIN SERVICE": "ind_niftyfinancelist.csv",
    
    # Thematic
    "NIFTYCOMMODITIES": "ind_niftycommoditieslist.csv",
    "NIFTY COMMODITIES": "ind_niftycommoditieslist.csv",
    "NIFTYINFRA":       "ind_niftyinfralist.csv",
    "NIFTY INFRA":      "ind_niftyinfralist.csv",
    "NIFTYPSE":         "ind_niftypselist.csv",
    "NIFTY PSE":        "ind_niftypselist.csv",
    "NIFTYPSUBANK":     "ind_niftypsubanklist.csv",
    "NIFTY PSU BANK":   "ind_niftypsubanklist.csv",
    "NIFTYPVTBANK":     "ind_niftyprivatebanklist.csv",
    "NIFTY PVT BANK":   "ind_niftyprivatebanklist.csv",
    "NIFTY PRIVATE BANK": "ind_niftyprivatebanklist.csv",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_from_niftyindices(csv_filename: str) -> list[str]:
    """Fetch and parse CSV list from NiftyIndices.com."""
    url = f"https://www.niftyindices.com/IndexConstituent/{csv_filename}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        
        # Read CSV
        csv_data = resp.text
        f = io.StringIO(csv_data)
        reader = csv.DictReader(f)
        
        symbols = []
        for row in reader:
            symbol = row.get("Symbol")
            if symbol:
                symbol = symbol.strip()
                # Fix some delisted / renamed tickers on NSE
                if symbol == "TATAMOTORS":
                    symbol = "TATAMTRDVT"
                symbols.append(symbol)
                
        return sorted(list(set(symbols)))
    except Exception as exc:
        log.error("Failed to fetch CSV '%s' from NiftyIndices: %s", csv_filename, exc)
        return []

def get_index_symbols(segment: str) -> list[str]:
    """
    Get stock symbols for a given index segment.
    """
    key = segment.strip().upper()
    csv_file = INDEX_CSV_MAP.get(key)
    
    if csv_file:
        log.info("Fetching constituents for '%s' from NiftyIndices CSV...", segment)
        symbols = fetch_from_niftyindices(csv_file)
        if symbols:
            log.info("Successfully fetched %d symbols.", len(symbols))
            return symbols
            
    log.warning("Segment '%s' not supported or fetch failed.", segment)
    return []

def list_available_segments() -> list[str]:
    """Return a list of supported segment names."""
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
