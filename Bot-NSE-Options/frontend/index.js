let autoRefreshInterval = null;
let currentScanData = { buy_results: [], sell_results: [] };

document.addEventListener("DOMContentLoaded", () => {
    // Sidebar Collapse Toggle Logic
    const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
    const sidebar = document.querySelector(".sidebar");

    if (btnSidebarToggle && sidebar) {
        const isCollapsed = localStorage.getItem("sidebar-collapsed") === "true";
        if (isCollapsed) sidebar.classList.add("collapsed");

        btnSidebarToggle.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
            localStorage.setItem("sidebar-collapsed", sidebar.classList.contains("collapsed") ? "true" : "false");
        });
    }

    // Navigation Tabs
    const navItems = document.querySelectorAll(".nav-item");
    const tabPanels = document.querySelectorAll(".tab-panel");

    navItems.forEach(item => {
        item.addEventListener("click", () => {
            const tabId = item.getAttribute("data-tab");
            navItems.forEach(i => i.classList.remove("active"));
            tabPanels.forEach(c => c.classList.remove("active"));

            item.classList.add("active");
            document.getElementById(`panel-${tabId}`).classList.add("active");

            if (tabId === "positions") loadPositions();
            if (tabId === "history") loadHistory();
            if (tabId === "stats") loadStats();
            if (tabId === "logs") loadLogs();
        });
    });

    // Run Scan Button
    document.getElementById("btn-run-scan").addEventListener("click", runScan);

    // Auto-refresh Toggle Button
    const btnAutoRefresh = document.getElementById("btn-toggle-auto-refresh");
    const autoRefreshState = document.getElementById("auto-refresh-state");

    btnAutoRefresh.addEventListener("click", () => {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
            autoRefreshState.textContent = "OFF";
            btnAutoRefresh.classList.replace("btn-primary", "btn-secondary");
        } else {
            startAutoRefresh();
        }
    });

    // Order Mode Buttons
    const btnManual = document.getElementById("btn-mode-manual");
    const btnAuto = document.getElementById("btn-mode-auto");

    btnManual.addEventListener("click", () => setOrderMode("manual"));
    btnAuto.addEventListener("click", () => setOrderMode("auto"));

    // Symbol Search Filters — Fixed HTML Element IDs
    document.getElementById("buy-search").addEventListener("input", (e) => {
        filterTable("buy-signals-table", currentScanData.buy_results, e.target.value.toLowerCase(), "BUY");
    });

    document.getElementById("sell-search").addEventListener("input", (e) => {
        filterTable("sell-signals-table", currentScanData.sell_results, e.target.value.toLowerCase(), "SELL");
    });

    // Quick Filter Switches
    const filterSwitches = ["dash-ut-enabled", "dash-sr-enabled", "dash-filters-ema", "dash-filters-volume", "dash-filters-mtf", "dash-filters-squeeze"];
    filterSwitches.forEach(id => {
        document.getElementById(id).addEventListener("change", updateQuickFilters);
    });

    // Initial Setup
    loadOptionsGrid();
    loadConfig();
    loadIndices();
    runScan();
    startAutoRefresh();

    // Order Modal
    const orderModal = document.getElementById("order-modal");
    document.getElementById("btn-modal-cancel").addEventListener("click", () => orderModal.classList.remove("active"));

    document.getElementById("form-place-order").addEventListener("submit", async (e) => {
        e.preventDefault();
        const reqData = {
            symbol: document.getElementById("modal-symbol").value,
            action: document.getElementById("modal-action").value,
            quantity: parseInt(document.getElementById("modal-quantity").value),
            product: document.getElementById("modal-product").value,
            exchange: "NFO"
        };
        try {
            const res = await fetch("/api/order", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(reqData)
            });
            const data = await res.json();
            alert(`Order Placed Successfully! Trade ID: ${data.trade_id}`);
            orderModal.classList.remove("active");
            loadPositions();
        } catch (err) {
            alert(`Error placing order: ${err}`);
        }
    });

    // Settings Form
    document.getElementById("form-config").addEventListener("submit", async (e) => {
        e.preventDefault();
        const strikeVal = document.getElementById("cfg-base-strike").value.trim();
        const gap = parseFloat(document.getElementById("cfg-strike-gap").value);
        try {
            await fetch("/api/options/grid", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ base_atm_strike: strikeVal, strike_gap: gap })
            });
            alert("Settings Saved!");
            loadOptionsGrid();
            runScan();
        } catch (err) {
            alert(`Failed: ${err}`);
        }
    });
});

function startAutoRefresh() {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
    autoRefreshInterval = setInterval(() => {
        runScan();
        loadIndices();
    }, 60000);
    document.getElementById("auto-refresh-state").textContent = "ON";
    const btn = document.getElementById("btn-toggle-auto-refresh");
    btn.classList.replace("btn-secondary", "btn-primary");
}

async function setOrderMode(mode) {
    document.getElementById("btn-mode-manual").classList.toggle("active", mode === "manual");
    document.getElementById("btn-mode-auto").classList.toggle("active", mode === "auto");
    try {
        const cfgRes = await fetch("/api/config");
        const cfg = await cfgRes.json();
        cfg.trading = cfg.trading || {};
        cfg.trading.order_mode = mode;
        await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(cfg)
        });
    } catch (e) {
        console.error("Order mode toggle error:", e);
    }
}

async function updateQuickFilters() {
    const payload = {
        ut_enabled: document.getElementById("dash-ut-enabled").checked,
        sr_enabled: document.getElementById("dash-sr-enabled").checked,
        ema_enabled: document.getElementById("dash-filters-ema").checked,
        volume_enabled: document.getElementById("dash-filters-volume").checked,
        mtf_enabled: document.getElementById("dash-filters-mtf").checked,
        squeeze_enabled: document.getElementById("dash-filters-squeeze").checked
    };
    try {
        await fetch("/api/filters", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        runScan();
    } catch (e) {
        console.error("Filter update error:", e);
    }
}

async function loadIndices() {
    try {
        const res = await fetch("/api/indices");
        const idx = await res.json();
        if (idx.nifty) {
            document.getElementById("index-val-nifty").textContent = idx.nifty.val;
            document.getElementById("index-chg-nifty").textContent = idx.nifty.chg;
        }
        if (idx.banknifty) {
            document.getElementById("index-val-banknifty").textContent = idx.banknifty.val;
            document.getElementById("index-chg-banknifty").textContent = idx.banknifty.chg;
        }
        if (idx.niftyit) {
            document.getElementById("index-val-niftyit").textContent = idx.niftyit.val;
            document.getElementById("index-chg-niftyit").textContent = idx.niftyit.chg;
        }
    } catch (e) {
        console.error("Indices quote error:", e);
    }
}

async function loadOptionsGrid() {
    try {
        const res = await fetch("/api/options/grid");
        const grid = await res.json();

        const levels = grid.contract_details
            ? Math.floor(grid.contract_details.length / 2)
            : 3;

        // Populate compact profile info bar
        setText("info-atm",        `${grid.atm_strike} (±${levels} strikes)`);
        setText("info-expiry",     grid.expiry  || "—");
        setText("info-underlying", grid.underlying || "—");
    } catch (err) {
        console.error("Failed to load options grid:", err);
    }
}

async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        const cfg = await res.json();
        const mode = cfg.trading?.order_mode || "manual";
        setOrderMode(mode);

        // Populate info bar from config
        setText("info-tf",      cfg.options?.timeframe    || "5m");
        setText("info-profile", (cfg.data_source || "openalgo").toUpperCase());

        // Build Mode label: combine active engines
        const baseMode  = (cfg.signal_mode || "UTBot").toUpperCase();
        const srEnabled = cfg.sr_channels?.enabled === true;
        const modeLabel = srEnabled && !baseMode.includes("SR")
            ? `${baseMode} + SR`
            : baseMode;
        setText("info-mode", modeLabel);
    } catch (err) {
        console.error("Failed to load config:", err);
    }
}

// Helper: safe setText
function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

async function runScan() {
    const btn = document.getElementById("btn-run-scan");
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Running...';
    try {
        const res = await fetch("/api/signals");
        const data = await res.json();
        currentScanData = data;

        document.getElementById("stat-scanned-count").textContent = data.total_scanned;
        document.getElementById("stat-buy-count").textContent = data.buy_results.length;
        document.getElementById("stat-sell-count").textContent = data.sell_results.length;
        document.getElementById("stat-last-scan-time").textContent = data.timestamp;

        renderSignals(data.buy_results, "buy-signals-table", "buy-last-updated", "BUY");
        renderSignals(data.sell_results, "sell-signals-table", "sell-last-updated", "SELL");
    } catch (err) {
        console.error("Scan error:", err);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-play"></i> Run Scanner';
    }
}

function filterTable(tableId, dataList, query, signalType) {
    const filtered = query ? dataList.filter(d => d.symbol.toLowerCase().includes(query)) : dataList;
    renderSignals(filtered, tableId, null, signalType);
}

// Month abbreviation to 2-digit number mapping
const MONTH_MAP = { JAN:'01', FEB:'02', MAR:'03', APR:'04', MAY:'05', JUN:'06',
                    JUL:'07', AUG:'08', SEP:'09', OCT:'10', NOV:'11', DEC:'12' };

/**
 * Converts NSE option symbol format to TradingView format.
 * e.g.  NIFTY18AUG2624300CE  →  NSE:NIFTY260818C24300
 *       BANKNIFTY18AUG2652000PE → NSE:BANKNIFTY260818P52000
 */
function convertToTVSymbol(nseSymbol) {
    // Regex: UNDERLYING + DD + MMM + YY + STRIKE + CE|PE
    const re = /^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$/i;
    const m = nseSymbol.toUpperCase().match(re);
    if (!m) return null;
    const [, underlying, dd, mon, yy, strike, type] = m;
    const mm = MONTH_MAP[mon];
    if (!mm) return null;
    const cp = type === 'CE' ? 'C' : 'P';
    // TradingView format: UNDERLYING + YY + MM + DD + C/P + STRIKE
    return `NSE:${underlying}${yy}${mm}${dd}${cp}${strike}`;
}

function openTradingViewChart(nseSymbol, fallbackUnderlying) {
    const tvSym = nseSymbol ? convertToTVSymbol(nseSymbol) : null;
    const sym = tvSym || `NSE:${(fallbackUnderlying || 'NIFTY').toUpperCase()}`;
    const tvUrl = `https://in.tradingview.com/chart/?symbol=${encodeURIComponent(sym)}`;
    window.open(tvUrl, "_blank", "noopener,noreferrer");
}

async function placeDirectOrder(symbol, action, btnEl) {
    const row = btnEl.closest("tr");
    const qtyInput = row.querySelector(".order-qty-input");
    const qty = parseInt(qtyInput.value) || 65;

    btnEl.disabled = true;
    btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Placing...';

    try {
        const res = await fetch("/api/order", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                symbol: symbol,
                action: action,
                quantity: qty,
                product: "NRML",
                exchange: "NFO"
            })
        });
        const data = await res.json();
        alert(`Order ${action} placed successfully for ${symbol}! (Qty: ${qty}, Trade ID: ${data.trade_id})`);
        loadPositions();
    } catch (err) {
        alert(`Order placement failed: ${err}`);
    } finally {
        btnEl.disabled = false;
        btnEl.innerHTML = `<i class="fa-solid fa-cart-shopping"></i> ${action === 'BUY' ? 'Buy' : 'Sell'}`;
    }
}

function renderSignals(results, tableId, badgeId, signalType) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    if (badgeId) {
        const badge = document.getElementById(badgeId);
        if (badge) badge.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    }

    if (!results || results.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-placeholder">No active ${signalType} signals.</td></tr>`;
        return;
    }

    tbody.innerHTML = results.map(r => {
        const c = r.confluence || {};
        const confHtml = `
            <div class="confluence-icons">
                <span class="icon-tag ${c.ema ? 'active' : ''}">EMA</span>
                <span class="icon-tag ${c.rsi ? 'active' : ''}">RSI</span>
                <span class="icon-tag ${c.vol ? 'active' : ''}">Vol</span>
                <span class="icon-tag ${c.sr ? 'active' : ''}">S/R</span>
                <span class="icon-tag ${c.sqz ? 'active' : ''}">Sqz</span>
            </div>
        `;
        return `
            <tr>
                <td style="font-weight: 700; font-family: var(--font-mono);">${r.symbol}</td>
                <td style="font-weight: 700; color: var(--accent-blue);">₹${r.price.toFixed(2)}</td>
                <td style="font-family: var(--font-mono);">${r.win_rate}</td>
                <td>
                    <div class="score-container">
                        <span class="grade-badge grade-${r.grade.toLowerCase()}">${r.grade} ${r.setup_score}</span>
                        <button class="btn-analyze" onclick="openTradingViewChart('${r.symbol}', '${r.underlying}')" title="View ${r.symbol} chart on TradingView">
                            <i class="fa-solid fa-chart-line"></i>
                        </button>
                        <button class="btn-analyze" onclick="openTradingViewChart('', '${r.underlying}')" title="Open Underlying Index (${r.underlying}) Chart on TradingView" style="opacity: 0.6; margin-left: 2px;">
                            <i class="fa-solid fa-chart-area"></i>
                        </button>
                    </div>
                </td>
                <td>${confHtml}</td>
                <td>
                    <div class="action-cell">
                        <div class="order-qty-wrap">
                            <span class="order-qty-label">QTY</span>
                            <input type="number" class="order-qty-input" value="${r.lot_size || 65}" min="1" step="1">
                        </div>
                        <button class="btn-order-${r.signal_type.toLowerCase()}" onclick="placeDirectOrder('${r.symbol}', '${r.signal_type}', this)">
                            <i class="fa-solid fa-cart-shopping"></i> ${r.signal_type === 'BUY' ? 'Buy' : 'Sell'}
                        </button>
                    </div>
                </td>
                <td style="color: var(--color-sell);">₹${r.stop_loss.toFixed(2)}</td>
                <td style="color: var(--color-buy);">₹${r.target.toFixed(2)}</td>
                <td style="font-family: var(--font-mono);">${r.risk_reward}</td>
            </tr>
        `;
    }).join("");
}

async function loadPositions() {
    try {
        const res = await fetch("/api/positions");
        const data = await res.json();
        const tbody = document.getElementById("table-positions");
        if (!tbody) return;

        if (!data.active || data.active.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="empty-placeholder">No open positions.</td></tr>';
            return;
        }

        tbody.innerHTML = data.active.map(p => `
            <tr>
                <td style="font-family: var(--font-mono);">${p.trade_id}</td>
                <td style="font-weight: 700;">${p.symbol}</td>
                <td><span class="badge-${p.action === 'BUY' ? 'ce' : 'pe'}">${p.action}</span></td>
                <td>${p.quantity}</td>
                <td>₹${p.entry_price.toFixed(2)}</td>
                <td>₹${p.current_price.toFixed(2)}</td>
                <td>₹${p.trailing_sl ? p.trailing_sl.toFixed(2) : '-'}</td>
                <td style="font-weight: 700; color: ${p.pnl_pts >= 0 ? 'var(--color-buy)' : 'var(--color-sell)'};">
                    ${p.pnl_pts >= 0 ? '+' : ''}${p.pnl_pts.toFixed(2)} pts
                </td>
                <td>
                    <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 0.75rem;" onclick="closePosition(${p.trade_id})">Close</button>
                </td>
            </tr>
        `).join("");
    } catch (err) {
        console.error("Positions error:", err);
    }
}

async function closePosition(tradeId) {
    if (!confirm(`Close trade #${tradeId}?`)) return;
    try {
        await fetch(`/api/positions/${tradeId}/close`, { method: "POST" });
        loadPositions();
    } catch (err) {
        alert("Failed to close position");
    }
}

async function loadHistory() {
    try {
        const res = await fetch("/api/history");
        const data = await res.json();
        const tbody = document.getElementById("table-history");
        if (!tbody) return;
        tbody.innerHTML = data.map(h => `
            <tr>
                <td>${h.timestamp}</td>
                <td style="font-weight: 700;">${h.symbol}</td>
                <td><span class="badge-${h.option_type ? h.option_type.toLowerCase() : 'ce'}">${h.signal_type}</span></td>
                <td>₹${h.price.toFixed(2)}</td>
                <td>₹${h.stop_loss ? h.stop_loss.toFixed(2) : '-'}</td>
                <td>₹${h.target ? h.target.toFixed(2) : '-'}</td>
                <td>${h.risk_reward || '-'}</td>
            </tr>
        `).join("");
    } catch (err) {
        console.error("History error:", err);
    }
}

async function loadStats() {
    try {
        const res = await fetch("/api/stats");
        const data = await res.json();
        if (document.getElementById("stat-total")) document.getElementById("stat-total").textContent = data.total_signals;
        if (document.getElementById("stat-buy")) document.getElementById("stat-buy").textContent = data.buy_signals;
        if (document.getElementById("stat-sell")) document.getElementById("stat-sell").textContent = data.sell_signals;
    } catch (err) {
        console.error("Stats error:", err);
    }
}

async function loadLogs() {
    try {
        const res = await fetch("/api/logs");
        const data = await res.json();
        const logBox = document.getElementById("log-output");
        if (!logBox) return;
        logBox.textContent = data.logs.join("\n");
        logBox.scrollTop = logBox.scrollHeight;
    } catch (err) {
        console.error("Logs error:", err);
    }
}
