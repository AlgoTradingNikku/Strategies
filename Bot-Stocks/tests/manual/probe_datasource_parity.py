"""
Manual parity probe: verify Bot-Stocks openalgo vs yfinance data sources
produce structurally-equivalent DataFrames after fetch_history normalisation.

Usage (from Bot-Stocks/):
    python tests/manual/probe_datasource_parity.py

Not part of the pytest suite because it needs a live openalgo server at
127.0.0.1:5000 and live network access to Yahoo Finance.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BOT_DIR))

import yaml  # noqa: E402
from scanner import fetch_history  # noqa: E402


def _load_cfg() -> dict:
    with open(_BOT_DIR / "config.yml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def probe(symbol: str = "IOC", tf: str = "5m") -> None:
    base = _load_cfg()

    cfg_oa = copy.deepcopy(base)
    cfg_oa["data_source"] = "openalgo"
    cfg_yf = copy.deepcopy(base)
    cfg_yf["data_source"] = "yfinance"

    oa = fetch_history(symbol, tf, cfg_oa)
    yf_df = fetch_history(symbol, tf, cfg_yf)

    print(f"\n=== {symbol} @ {tf} ===")
    print(f"OA rows={None if oa is None else len(oa)}  cols={None if oa is None else list(oa.columns)}"
          f"  tz={None if oa is None else oa.index.tz}")
    print(f"YF rows={None if yf_df is None else len(yf_df)}  cols={None if yf_df is None else list(yf_df.columns)}"
          f"  tz={None if yf_df is None else yf_df.index.tz}")

    assert oa is not None, "openalgo returned None"
    assert yf_df is not None, "yfinance returned None"
    assert oa.index.tz is None, f"openalgo index still tz-aware: {oa.index.tz}"
    assert yf_df.index.tz is None, f"yfinance index still tz-aware: {yf_df.index.tz}"
    assert "oi" not in oa.columns, "oi column not stripped"
    assert set(("open", "high", "low", "close", "volume")).issubset(oa.columns), "OHLCV missing"
    assert set(("open", "high", "low", "close", "volume")).issubset(yf_df.columns), "OHLCV missing"

    overlap = oa.index.intersection(yf_df.index)
    print(f"overlap bars: {len(overlap)}")
    if len(overlap) > 0:
        diffs = (oa.loc[overlap, "close"] - yf_df.loc[overlap, "close"]).abs()
        print(f"close abs diff -> max={diffs.max():.4f}  mean={diffs.mean():.4f}  p95={diffs.quantile(0.95):.4f}")

    # No synthetic flat zero-vol bars survive
    flat = (
        (oa["volume"] == 0)
        & (oa["open"] == oa["close"])
        & (oa["high"] == oa["low"])
        & (oa["open"] == oa["high"])
    )
    assert not flat.any(), f"{int(flat.sum())} synthetic flat bars still present"
    print("✓ no synthetic flat zero-vol bars in openalgo output")


def probe_daily(symbol: str = "IOC") -> None:
    """Regression: `1d` timeframe must now succeed on openalgo (mapped to `D`)."""
    base = _load_cfg()
    cfg_oa = copy.deepcopy(base)
    cfg_oa["data_source"] = "openalgo"
    df = fetch_history(symbol, "1d", cfg_oa)
    print(f"\n=== {symbol} @ 1d (openalgo) ===")
    print(f"rows={None if df is None else len(df)}  cols={None if df is None else list(df.columns)}"
          f"  tz={None if df is None else df.index.tz}")
    assert df is not None and len(df) > 0, "openalgo 1d fetch returned no data"
    assert df.index.tz is None
    assert "oi" not in df.columns
    print("✓ openalgo 1d timeframe works")


if __name__ == "__main__":
    probe("IOC", "5m")
    probe_daily("IOC")
    print("\nAll parity checks passed.")
