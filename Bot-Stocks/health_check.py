"""
Health Check Module
===================
Validates system components on startup and provides health status endpoint.
"""

import logging
import requests
from datetime import datetime

log = logging.getLogger("UTBotSRChannelsScanner")


def run_health_check(config: dict) -> dict:
    """
    Run all health checks and return summary.
    
    Returns
    -------
    dict
        {
            "status": "ok" | "degraded" | "critical",
            "checks": {
                "database": "✅ OK" | "❌ FAILED: ...",
                "broker_api": "✅ OK" | "⚠️ HTTP 500",
                "data_source": "✅ OK",
                "telegram": "✅ Configured" | "⚙️ Disabled"
            },
            "timestamp": "2026-08-28 10:00:00"
        }
    """
    results = {}
    overall_status = "ok"
    
    # 1. Database check
    try:
        import signal_db
        conn = signal_db._get_connection(config)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        results["database"] = "✅ OK"
    except Exception as e:
        results["database"] = f"❌ FAILED: {e}"
        overall_status = "critical"
    
    # 2. Broker API check
    try:
        oa_cfg = config.get("openalgo", {}) or {}
        base_url = oa_cfg.get("base_url", "http://127.0.0.1:5000")
        
        # Try a lightweight endpoint
        response = requests.get(f"{base_url}/", timeout=5)
        
        if response.status_code in [200, 404]:  # 404 is OK (means server is up)
            results["broker_api"] = "✅ OK"
        else:
            results["broker_api"] = f"⚠️ HTTP {response.status_code}"
            overall_status = "degraded"
    except Exception as e:
        results["broker_api"] = f"❌ FAILED: {e}"
        overall_status = "degraded"
    
    # 3. Data source check (quick test)
    try:
        data_source = config.get("data_source", "yfinance")
        
        if data_source == "yfinance":
            import yfinance as yf
            # Quick test - don't actually download
            ticker = yf.Ticker("RELIANCE.NS")
            # Just check if import works
            results["data_source"] = "✅ OK (yfinance)"
        else:
            results["data_source"] = f"✅ Configured ({data_source})"
            
    except Exception as e:
        results["data_source"] = f"⚠️ {e}"
        # Don't fail overall status for data source (might be temp)
    
    # 4. Telegram check
    try:
        tg_cfg = config.get("telegram", {}) or {}
        if tg_cfg.get("enabled"):
            if tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
                results["telegram"] = "✅ Configured"
            else:
                results["telegram"] = "⚠️ Missing credentials"
        else:
            results["telegram"] = "⚙️ Disabled"
    except Exception as e:
        results["telegram"] = f"❌ {e}"
    
    # Log summary
    log.info("=" * 60)
    log.info("HEALTH CHECK - %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)
    for component, status in results.items():
        log.info("  %-20s : %s", component.upper(), status)
    log.info("=" * 60)
    
    failures = [k for k, v in results.items() if "❌" in v]
    if failures:
        log.warning("⚠️ %d component(s) failing: %s", len(failures), ", ".join(failures))
    else:
        log.info("✅ All systems operational")
    log.info("=" * 60)
    
    return {
        "status": overall_status,
        "checks": results,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
