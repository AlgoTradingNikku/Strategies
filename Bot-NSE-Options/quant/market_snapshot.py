"""
quant/market_snapshot.py
=========================
Assembles all quant engine outputs into the canonical MarketDataModel.
This is the single function that orchestrates all quant modules.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_bot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_bot_dir))

from ai.schemas.market import MarketDataModel, OISignals, TechnicalData, VolatilityData
from ai.schemas.settings import PlatformSettings
from quant.oi_analysis import analyze_oi
from quant.technicals import compute_multi_tf_technicals
from quant.volatility import compute_volatility_metrics

log = logging.getLogger("UTBotSRChannelsScanner")


def build_market_data_model(
    snapshot,  # OptionChainSnapshot
    vix: float,
    spot_histories: dict[str, Optional[pd.DataFrame]],
    cfg: dict,
    settings: Optional[PlatformSettings] = None,
) -> MarketDataModel:
    """
    Orchestrate all quant engine modules into a complete MarketDataModel.

    Args:
        snapshot:       OptionChainSnapshot — the frozen chain data
        vix:            India VIX level
        spot_histories: dict of timeframe -> OHLCV DataFrame (e.g. {"5m": df, "15m": df})
        cfg:            full config dict
        settings:       PlatformSettings with toggle state

    Returns:
        MarketDataModel with all pre-computed signals. Fail-open on component errors.
    """
    # OI signals
    oi_signals = OISignals()
    try:
        oi_signals = analyze_oi(snapshot, settings)
    except Exception as exc:
        log.warning("[MarketSnapshot] OI analysis failed: %s", exc)

    # Technicals (multi-timeframe)
    technicals = TechnicalData(spot_ltp=snapshot.underlying_ltp)
    try:
        technicals = compute_multi_tf_technicals(spot_histories, cfg, settings)
        if technicals.spot_ltp <= 0:
            technicals.spot_ltp = snapshot.underlying_ltp
    except Exception as exc:
        log.warning("[MarketSnapshot] Technicals failed: %s", exc)

    # Volatility metrics
    volatility = VolatilityData()
    try:
        volatility = compute_volatility_metrics(snapshot, vix, cfg, settings)
    except Exception as exc:
        log.warning("[MarketSnapshot] Volatility failed: %s", exc)

    return MarketDataModel(
        underlying=snapshot.underlying,
        expiry_date=snapshot.expiry_date,
        snapshot_age_sec=snapshot.age_seconds,
        technicals=technicals,
        volatility=volatility,
        oi_signals=oi_signals,
    )
