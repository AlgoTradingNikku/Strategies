"""
═══════════════════════════════════════════════════════════════════════
  TradingView Browser Monitor
  ────────────────────────────
  Monitors a TradingView chart's Strategy Tester panel for new
  HalfTrend Buy/Sell signals using Playwright browser automation.

  Phase 1: Detects signals → prints to console + logs to file
  Phase 2: Detects signals → places orders via OpenAlgo API

  Usage:
    python monitor.py                     # Normal (opens browser window)
    python monitor.py --headless          # No visible browser
    python monitor.py --debug             # Save DOM snapshot for debugging
═══════════════════════════════════════════════════════════════════════
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml
from playwright.async_api import async_playwright

# ─── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
SIGNALS_LOG = SCRIPT_DIR / "signals.jsonl"

BANNER = """
╔═══════════════════════════════════════════════════════════════╗
║     TradingView Browser Monitor  v1.0                        ║
║     ─────────────────────────────────                        ║
║     Watches Strategy Tester for HalfTrend signals            ║
╚═══════════════════════════════════════════════════════════════╝
"""


# ─── Config & Logging ─────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[ERROR] Config not found: {CONFIG_PATH}")
        print(f"        Edit config.yaml with your TradingView chart URL.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> logging.Logger:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = SCRIPT_DIR / log_cfg.get("log_file", "browser_monitor.log")

    logger = logging.getLogger("BrowserMonitor")
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    # File
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ─── Signal Tracker (dedup) ───────────────────────────────────────────
class SignalTracker:
    """Prevents processing the same signal twice."""

    def __init__(self):
        self.seen: set[str] = set()
        self.count = 0

    def is_new(self, signal: dict) -> bool:
        h = hashlib.md5(json.dumps(signal, sort_keys=True).encode()).hexdigest()
        if h in self.seen:
            return False
        self.seen.add(h)
        self.count += 1
        return True


def log_signal(signal: dict):
    """Append signal to JSONL file."""
    with open(SIGNALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(signal) + "\n")


# ─── OpenAlgo Order (Phase 2) ────────────────────────────────────────
def place_order(signal: dict, config: dict, logger: logging.Logger):
    """Place order via OpenAlgo Smart Order API."""
    oa = config["openalgo"]
    defaults = oa.get("order_defaults", {})
    action = signal.get("action", "").upper()

    if action not in ("BUY", "SELL"):
        logger.warning(f"Unknown action '{action}', skipping order.")
        return

    payload = {
        "apikey": oa["api_key"],
        "strategy": defaults.get("strategy", "TVBrowserMonitor"),
        "symbol": defaults.get("symbol", "NIFTY"),
        "exchange": defaults.get("exchange", "NFO"),
        "action": action,
        "quantity": str(defaults.get("quantity", 65)),
        "pricetype": defaults.get("pricetype", "MARKET"),
        "product": defaults.get("product", "MIS"),
    }

    url = f"{oa['host']}/api/v1/placesmartorder"
    try:
        logger.info(f"📤 Placing {action} order → {url}")
        logger.info(f"   Payload: {json.dumps(payload, indent=2)}")
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"✅ Order response: {resp.json()}")
    except Exception as e:
        logger.error(f"❌ Order failed: {e}")


# ─── Parse a trade row from Strategy Tester ──────────────────────────
def parse_trade_row(cells: list[str]) -> dict | None:
    """
    Parse a row from TradingView's Strategy Tester "List of Trades" table.

    Typical columns:
      [0] Trade #      e.g. "1", "2"
      [1] Type         e.g. "Long Entry", "Short Entry", "Long Exit"
      [2] Signal       e.g. "Long", "Short"
      [3] Date/Time    e.g. "2026-02-20 14:30"
      [4] Price        e.g. "22450.50"
      [5] Contracts    e.g. "1"
      [6] Profit       e.g. "+₹450"
      ...

    Returns a dict with action=BUY/SELL, or None if not parseable.
    """
    if len(cells) < 4:
        return None

    trade_type = cells[1].strip().lower() if len(cells) > 1 else ""

    # Determine action
    action = ""
    if "entry" in trade_type:
        if "long" in trade_type:
            action = "BUY"
        elif "short" in trade_type:
            action = "SELL"
    elif "exit" in trade_type:
        if "long" in trade_type:
            action = "SELL"    # exiting long = sell
        elif "short" in trade_type:
            action = "BUY"     # exiting short = buy

    if not action:
        return None

    return {
        "trade_number": cells[0].strip(),
        "trade_type": cells[1].strip(),
        "signal": cells[2].strip() if len(cells) > 2 else "",
        "date": cells[3].strip() if len(cells) > 3 else "",
        "price": cells[4].strip() if len(cells) > 4 else "",
        "action": action,
        "detected_at": datetime.now().isoformat(),
    }


# ─── Main monitoring loop ────────────────────────────────────────────
async def run_monitor(args):
    print(BANNER)
    config = load_config()
    logger = setup_logging(config)

    chart_url = config["tradingview"]["chart_url"]
    poll_sec = config["tradingview"].get("poll_interval_seconds", 3)
    oa_enabled = config.get("openalgo", {}).get("enabled", False)

    logger.info(f"Chart URL      : {chart_url}")
    logger.info(f"Poll Interval  : {poll_sec}s")
    logger.info(f"OpenAlgo       : {'ENABLED' if oa_enabled else 'DISABLED (print only)'}")

    if "YOUR_CHART_ID" in chart_url:
        logger.error("⚠️  Please edit config.yaml and set your TradingView chart URL!")
        logger.error("   Open TradingView → your chart → copy URL from address bar")
        return

    async with async_playwright() as pw:
        # ─── Browser Launch Logic ──────────────────────────────────────────
        if args.remote:
            logger.info("🔗 Connecting to existing Chrome instance (localhost:9222)...")
            try:
                browser_context = await pw.chromium.connect_over_cdp("http://localhost:9222")
                # For CDP, we get a browser object, need to get context
                context = browser_context.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()
                logger.info("✅ Connected to open Chrome window.")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Chrome: {e}")
                logger.error("   Make sure Chrome is running with: chrome.exe --remote-debugging-port=9222")
                return
        else:
            logger.info("🚀 Launching Chrome browser...")
            
            # Use local Chrome User Data if possible to keep logins
            # If browser_data folder exists in script dir, use it. 
            # Otherwise, it creates one.
            user_data = SCRIPT_DIR / "browser_data"
            user_data.mkdir(exist_ok=True)

            browser_context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                channel="chrome",  # This forces it to use your installed Google Chrome
                headless=args.headless,
                viewport={"width": 1920, "height": 1080},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--start-maximized"
                ],
            )
            page = browser_context.pages[0]

        # ── Navigate ──────────────────────────────────────────────
        logger.info(f"📈 Opening chart: {chart_url}")
        try:
            await page.goto(chart_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            logger.error(f"Failed to load page: {e}")
            logger.info("Retrying in 10 seconds...")
            await asyncio.sleep(10)
            await page.goto(chart_url, wait_until="domcontentloaded", timeout=60_000)

        await asyncio.sleep(8)  # let chart + strategy fully render

        # ── Handle login ──────────────────────────────────────────
        if "signin" in page.url.lower() or "accounts" in page.url.lower():
            logger.warning("=" * 55)
            logger.warning("⚠️  LOGIN REQUIRED")
            logger.warning("   Please log in to TradingView in the browser window.")
            logger.warning("   The monitor will resume once you reach the chart.")
            logger.warning("=" * 55)
            while "chart" not in page.url.lower():
                await asyncio.sleep(3)
            logger.info("✅ Login successful! Chart loaded.")
            await asyncio.sleep(8)

        # ── Dismiss popups ────────────────────────────────────────
        for selector in ['[data-name="close"]', 'button[aria-label="Close"]',
                         '.tv-dialog__close', '.close-button']:
            try:
                btns = page.locator(selector)
                for i in range(await btns.count()):
                    await btns.nth(i).click(timeout=1500)
            except:
                pass

        # ── Open Strategy Tester → List of Trades ─────────────────
        logger.info("📊 Opening Strategy Tester panel...")
        try:
            # Click the "Strategy Tester" tab at the bottom panel
            st_tab = page.locator('[data-name="backtesting"]')
            if await st_tab.count() > 0:
                await st_tab.click()
                await asyncio.sleep(2)
                logger.info("   ✅ Strategy Tester opened")
            else:
                logger.warning("   ⚠️  Could not find Strategy Tester tab.")
                logger.warning("   Please open it manually: click 'Strategy Tester' at the bottom.")

            # Click "List of Trades" tab inside Strategy Tester
            lot = page.locator('button:has-text("List of Trades")')
            if await lot.count() > 0:
                await lot.click()
                await asyncio.sleep(1)
                logger.info("   ✅ List of Trades view active")
            else:
                logger.warning("   ⚠️  Could not find 'List of Trades' button.")
                logger.warning("   Please click it manually in the Strategy Tester panel.")
        except Exception as e:
            logger.warning(f"   Could not auto-open Strategy Tester: {e}")
            logger.info("   💡 Please manually open: Strategy Tester → List of Trades")

        # ── Debug DOM snapshot ────────────────────────────────────
        if args.debug:
            html = await page.content()
            debug_file = SCRIPT_DIR / "debug_page.html"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"📄 DOM snapshot → {debug_file}")
            # Also take a screenshot
            ss = SCRIPT_DIR / "debug_screenshot.png"
            await page.screenshot(path=str(ss), full_page=False)
            logger.info(f"📸 Screenshot → {ss}")

        # ── Start monitoring ──────────────────────────────────────
        logger.info("=" * 55)
        logger.info("🟢 MONITORING STARTED — watching for new trades")
        logger.info("   Press Ctrl+C to stop")
        logger.info("=" * 55)

        tracker = SignalTracker()
        prev_row_count = 0

        try:
            while True:
                try:
                    # Find trade rows in Strategy Tester table
                    # TradingView uses various class names — try multiple selectors
                    rows = page.locator(
                        'table.report-data tbody tr, '
                        '[class*="listOfTrades"] table tbody tr, '
                        '[data-name="list-of-trades"] table tbody tr, '
                        '.reports-content table tbody tr'
                    )
                    row_count = await rows.count()

                    if row_count > prev_row_count and prev_row_count > 0:
                        # ── New trades appeared! ──
                        for i in range(prev_row_count, row_count):
                            try:
                                row = rows.nth(i)
                                cells_loc = row.locator("td")
                                n_cells = await cells_loc.count()
                                cells = []
                                for c in range(n_cells):
                                    cells.append((await cells_loc.nth(c).inner_text()).strip())

                                signal = parse_trade_row(cells)
                                if signal and tracker.is_new(signal):
                                    # ═════════════════════════════════
                                    #  🎯 SIGNAL DETECTED
                                    # ═════════════════════════════════
                                    logger.info("")
                                    logger.info("🚨" + "═" * 50)
                                    logger.info(f"🚨  NEW SIGNAL: {signal['action']}")
                                    logger.info(f"🚨  Type      : {signal['trade_type']}")
                                    logger.info(f"🚨  Signal    : {signal['signal']}")
                                    logger.info(f"🚨  Date      : {signal['date']}")
                                    logger.info(f"🚨  Price     : {signal['price']}")
                                    logger.info(f"🚨  Detected  : {signal['detected_at']}")
                                    logger.info("🚨" + "═" * 50)
                                    logger.info("")

                                    log_signal(signal)

                                    if oa_enabled and signal["action"]:
                                        place_order(signal, config, logger)

                            except Exception as e:
                                logger.debug(f"Row {i} parse error: {e}")

                    elif row_count > 0 and prev_row_count == 0:
                        logger.info(f"📋 Found {row_count} existing trades. Watching for NEW ones only.")

                    prev_row_count = row_count

                except Exception as e:
                    logger.debug(f"Poll error: {e}")

                await asyncio.sleep(poll_sec)

        except KeyboardInterrupt:
            pass

        logger.info("")
        logger.info(f"🛑 Stopped. Total new signals detected: {tracker.count}")
        await browser_context.close()
        logger.info("Browser closed. Goodbye! 👋")


# ─── Entry Point ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="TradingView Browser Monitor")
    parser.add_argument("--headless", action="store_true",
                        help="Run without visible browser window")
    parser.add_argument("--remote", action="store_true",
                        help="Connect to already open Chrome on port 9222")
    parser.add_argument("--debug", action="store_true",
                        help="Save DOM snapshot and screenshot for debugging")
    args = parser.parse_args()

    try:
        asyncio.run(run_monitor(args))
    except KeyboardInterrupt:
        print("\n🛑 Stopped.")


if __name__ == "__main__":
    main()
