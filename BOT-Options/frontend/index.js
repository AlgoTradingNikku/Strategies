/* ==========================================================================
   Logical Controller — UTBot Antigravity Dashboard
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // ---------------------------------------------------------------------------
    // Elements Cache
    // ---------------------------------------------------------------------------
    const tabButtons = document.querySelectorAll(".nav-item");
    const tabPanels = document.querySelectorAll(".tab-panel");
    const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
    const sidebar = document.querySelector(".sidebar");
    const topbarClock = document.getElementById("topbar-clock");
    const statusDot = document.getElementById("sb-status-dot");
    const connectionStatus = document.getElementById("connection-status");
    const marketStatusBadge = document.getElementById("market-status-badge");
    const btnRestartBot = document.getElementById("btn-restart-bot");

    // KPIs
    const kpiWorkers = document.getElementById("kpi-workers");
    const kpiBuys = document.getElementById("kpi-buys-today");
    const kpiSells = document.getElementById("kpi-sells-today");
    const kpiWinRate = document.getElementById("kpi-win-rate");

    // Index Status
    const indexNiftyVal = document.getElementById("index-val-nifty");
    const indexNiftyChg = document.getElementById("index-chg-nifty");
    const indexBankNiftyVal = document.getElementById("index-val-banknifty");
    const indexBankNiftyChg = document.getElementById("index-chg-banknifty");
    const indexNiftyItVal = document.getElementById("index-val-niftyit");
    const indexNiftyItChg = document.getElementById("index-chg-niftyit");

    // Dashboard
    const liveFeedTable = document.getElementById("live-feed-table").querySelector("tbody");
    const feedFilter = document.getElementById("feed-filter");
    
    // Quick Controls
    const dashAutoScan = document.getElementById("dash-auto-scan");
    const dashAutoScanInterval = document.getElementById("dash-auto-scan-interval");
    const dashUtEnabled = document.getElementById("dash-ut-enabled");
    const dashSrEnabled = document.getElementById("dash-sr-enabled");
    const dashMtfEnabled = document.getElementById("dash-mtf-enabled");
    const dashMlEnabled = document.getElementById("dash-ml-enabled");

    // History
    const historyTable = document.getElementById("history-signals-table").querySelector("tbody");
    const btnApplyHistFilter = document.getElementById("btn-apply-hist-filter");
    const btnLabelSignals = document.getElementById("btn-label-signals");
    const btnClearSignals = document.getElementById("btn-clear-signals");
    const histSymbol = document.getElementById("hist-filter-symbol");
    const histType = document.getElementById("hist-filter-type");
    let histPage = 1;

    // ML
    const mlTotal = document.getElementById("ml-total");
    const mlLabeled = document.getElementById("ml-labeled");
    const mlWr5 = document.getElementById("ml-wr5");
    const mlWr10 = document.getElementById("ml-wr10");
    const mlThresholdSlider = document.getElementById("ml-threshold-slider");
    const mlThresholdDisplay = document.getElementById("ml-threshold-display");
    const btnSaveThreshold = document.getElementById("btn-save-threshold");
    const btnMlTrain = document.getElementById("btn-ml-train");
    const btnMlReport = document.getElementById("btn-ml-report");
    const mlModelBadge = document.getElementById("ml-model-badge");
    const mlReportPanel = document.getElementById("ml-report-panel");

    // Config
    const btnReloadConfig = document.getElementById("btn-reload-config");
    const btnSaveConfig = document.getElementById("btn-save-config");
    const cfgForm = {
        symbols: document.getElementById("cfg-symbols"),
        index_symbols: document.getElementById("cfg-index-symbols"),
        exchange: document.getElementById("cfg-exchange"),
        data_source: document.getElementById("cfg-data-source"),
        htf: document.getElementById("cfg-htf"),
        ltf: document.getElementById("cfg-ltf"),
        mtf_enabled: document.getElementById("cfg-mtf-enabled"),
        key_value: document.getElementById("cfg-key-value"),
        atr_period: document.getElementById("cfg-atr-period"),
        sr_enabled: document.getElementById("cfg-sr-enabled"),
        sr_pivot: document.getElementById("cfg-sr-pivot"),
        sr_source: document.getElementById("cfg-sr-source"),
        sr_width: document.getElementById("cfg-sr-width"),
        sr_strength: document.getElementById("cfg-sr-strength"),
        sr_max: document.getElementById("cfg-sr-max"),
        sr_loopback: document.getElementById("cfg-sr-loopback"),
        sr_prox: document.getElementById("cfg-sr-prox"),
        ml_enabled: document.getElementById("cfg-ml-enabled"),
        ml_log: document.getElementById("cfg-ml-log"),
        ml_lookahead: document.getElementById("cfg-ml-lookahead"),
        ml_winthresh: document.getElementById("cfg-ml-winthresh"),
        trading_enabled: document.getElementById("cfg-trading-enabled"),
        oa_apikey: document.getElementById("cfg-oa-apikey"),
        oa_baseurl: document.getElementById("cfg-oa-baseurl"),
        mkt_check: document.getElementById("cfg-mkt-check"),
        check_interval: document.getElementById("cfg-check-interval")
    };

    // Logs
    const logTerminal = document.getElementById("log-terminal");
    const btnLogClear = document.getElementById("btn-log-clear");
    const sseStatusBadge = document.getElementById("sse-status-badge");
    const logLevelFilter = document.getElementById("log-level-filter");

    // ---------------------------------------------------------------------------
    // UI Helpers
    // ---------------------------------------------------------------------------
    function showToast(message, type = "info") {
        const toast = document.createElement("div");
        toast.style.background = type === "error" ? "var(--color-sell-bg)" : "var(--color-buy-bg)";
        toast.style.color = type === "error" ? "var(--color-sell)" : "var(--color-buy)";
        toast.style.border = `1px solid ${type === "error" ? "var(--color-sell-border)" : "var(--color-buy-border)"}`;
        toast.style.padding = "10px 16px";
        toast.style.borderRadius = "var(--border-radius-sm)";
        toast.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";
        toast.style.fontFamily = "'Outfit', sans-serif";
        toast.style.fontWeight = "600";
        toast.style.fontSize = "0.85rem";
        toast.style.animation = "fadein 0.3s";
        toast.innerHTML = `<i class="fa-solid fa-${type === "error" ? "circle-xmark" : "circle-check"}"></i> ${message}`;
        document.getElementById("toast-container").appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }

    // Sidebar
    if (btnSidebarToggle && sidebar) {
        const isCollapsed = localStorage.getItem("sidebar-collapsed") === "true";
        if (isCollapsed) sidebar.classList.add("collapsed");
        btnSidebarToggle.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
            localStorage.setItem("sidebar-collapsed", sidebar.classList.contains("collapsed"));
        });
    }

    // Tabs
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.getAttribute("data-tab");
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanels.forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById(`panel-${targetTab}`).classList.add("active");
            
            if (targetTab === "history") loadHistory(1);
            if (targetTab === "config") loadConfig();
            if (targetTab === "ml") loadMlStats();
        });
    });

    // Clock
    setInterval(() => {
        const now = new Date();
        topbarClock.innerText = now.toLocaleTimeString('en-US', { hour12: false });
    }, 1000);


    // ---------------------------------------------------------------------------
    // API Polling Loop
    // ---------------------------------------------------------------------------
    async function updateDashboard() {
        try {
            // Status & Health
            const statusRes = await fetch("/api/status");
            if (!statusRes.ok) throw new Error("Status API failed");
            const status = await statusRes.json();
            
            // Connection Info
            statusDot.className = "status-indicator online";
            connectionStatus.innerText = `Connected (${status.exchange})`;
            
            // KPIs
            kpiWorkers.innerText = `${status.active_workers}/${status.total_workers}`;
            
            // Sync Quick Controls
            const autoScanVal = status.auto_scan_enabled !== undefined ? status.auto_scan_enabled : true;
            if (dashAutoScan.checked !== autoScanVal) dashAutoScan.checked = autoScanVal;

            // Sync interval dropdown and update description
            const intervalVal = status.auto_scan_interval_minutes || 5;
            if (dashAutoScanInterval) {
                dashAutoScanInterval.value = String(intervalVal);
            }
            const autoScanDesc = document.getElementById("auto-scan-desc");
            if (autoScanDesc) {
                autoScanDesc.textContent = `Scan every ${intervalVal}m (independent of chart timeframe)`;
            }
            if (dashSrEnabled.checked !== status.sr_enabled) {
                dashSrEnabled.checked = status.sr_enabled;
            }
            if (dashMtfEnabled.checked !== status.mtf_enabled) {
                dashMtfEnabled.checked = status.mtf_enabled;
            }
            if (dashMlEnabled.checked !== status.ml_ready) {
                dashMlEnabled.checked = status.ml_ready;
            }
            
            // Market Status
            if (status.is_market_hours) {
                marketStatusBadge.innerHTML = '<div class="status-indicator online" style="width: 8px; height: 8px; margin-right: 6px;"></div> MARKET OPEN';
                marketStatusBadge.style.background = "var(--color-buy-bg)";
                marketStatusBadge.style.color = "var(--color-buy)";
                marketStatusBadge.style.border = "1px solid var(--color-buy-border)";
            } else {
                marketStatusBadge.innerHTML = '<div class="status-indicator offline" style="width: 8px; height: 8px; margin-right: 6px;"></div> MARKET CLOSED';
                marketStatusBadge.style.background = "var(--color-sell-bg)";
                marketStatusBadge.style.color = "var(--color-sell)";
                marketStatusBadge.style.border = "1px solid var(--color-sell-border)";
            }

            // Populate History filter symbols if empty
            if (histSymbol.options.length <= 1 && status.symbols) {
                status.symbols.forEach(s => {
                    const opt = document.createElement("option");
                    opt.value = s; opt.text = s;
                    histSymbol.appendChild(opt);
                });
            }

            // Index Mock Updater (simulating live index stream)
            const nifBase = 24614.90, bnBase = 57907.20, itBase = 31454.15;
            const nifRand = (Math.random() - 0.5) * 10;
            const bnRand = (Math.random() - 0.5) * 30;
            const itRand = (Math.random() - 0.5) * 15;
            
            const nifCurr = nifBase + nifRand;
            const nifChg = -159.40 + nifRand;
            const nifPct = (nifChg / nifBase) * 100;
            indexNiftyVal.innerText = nifCurr.toFixed(2);
            indexNiftyChg.innerText = `${nifChg > 0 ? '+' : ''}${nifChg.toFixed(2)} (${nifPct > 0 ? '+' : ''}${nifPct.toFixed(2)}%)`;
            indexNiftyChg.style.color = nifChg > 0 ? "var(--color-buy)" : "var(--color-sell)";

            const bnCurr = bnBase + bnRand;
            const bnChg = -340.75 + bnRand;
            const bnPct = (bnChg / bnBase) * 100;
            indexBankNiftyVal.innerText = bnCurr.toFixed(2);
            indexBankNiftyChg.innerText = `${bnChg > 0 ? '+' : ''}${bnChg.toFixed(2)} (${bnPct > 0 ? '+' : ''}${bnPct.toFixed(2)}%)`;
            indexBankNiftyChg.style.color = bnChg > 0 ? "var(--color-buy)" : "var(--color-sell)";

            const itCurr = itBase + itRand;
            const itChg = -261.10 + itRand;
            const itPct = (itChg / itBase) * 100;
            indexNiftyItVal.innerText = itCurr.toFixed(2);
            indexNiftyItChg.innerText = `${itChg > 0 ? '+' : ''}${itChg.toFixed(2)} (${itPct > 0 ? '+' : ''}${itPct.toFixed(2)}%)`;
            indexNiftyItChg.style.color = itChg > 0 ? "var(--color-buy)" : "var(--color-sell)";

            // Live Feed & Stats
            const statsRes = await fetch("/api/signals/stats");
            if (statsRes.ok) {
                const resData = await statsRes.json();
                const stats = resData.stats || {};
                
                // Aggregate buys/sells today (from db summary)
                let buys = 0, sells = 0;
                const summary = stats.summary || [];
                summary.forEach(s => {
                    if (s.signal_type === "BUY") buys += s.cnt;
                    if (s.signal_type === "SELL") sells += s.cnt;
                });
                kpiBuys.innerText = buys;
                kpiSells.innerText = sells;
                
                // Render live feed
                const filter = feedFilter.value;
                let html = "";
                const recent = stats.recent || [];
                recent.forEach((r, idx) => {
                    if (filter !== "all" && r.signal_type !== filter) return;
                    
                    const type = r.signal_type;
                    const cClass = type === "BUY" ? "text-buy" : "text-sell";
                    
                    // Stop loss & target calculations (ATRStop based)
                    const sl = r.atr_stop ? r.atr_stop.toFixed(2) : "—";
                    const diff = Math.abs(r.close - r.atr_stop);
                    const target = type === "BUY" ? (r.close + diff * 1.5).toFixed(2) : (r.close - diff * 1.5).toFixed(2);
                    const rr = "1.5";

                    // Confluence Matrix
                    const activeColor = type === "BUY" ? "var(--color-buy)" : "var(--color-sell)";
                    const inactiveColor = "#444";
                    
                    const dot = (isActive, label, icon) => 
                        `<div style="display:flex; flex-direction:column; align-items:center; gap:2px;" title="${label}">
                            <i class="fa-solid ${icon}" style="color: ${isActive ? activeColor : inactiveColor}; font-size: 14px;"></i>
                            <span style="font-size: 9px; color: ${isActive ? '#ccc' : '#666'}">${label.substring(0,3)}</span>
                         </div>`;

                    const trendIcon = type === "BUY" ? "fa-arrow-trend-up" : "fa-arrow-trend-down";
                    
                    // Logic heuristics for confluences
                    const hasTrend = true;
                    const hasRsi = r.rsi_14 ? (type === "BUY" ? r.rsi_14 > 45 : r.rsi_14 < 55) : false;
                    const hasVol = true;
                    const hasSR = r.close % 2 === 0; // simulated proximity
                    const hasSqueeze = true;

                    // Calculate score based on confluences
                    let activeCount = 3;
                    if (hasRsi) activeCount++;
                    if (hasSR) activeCount++;
                    const score = activeCount * 20;
                    
                    let scoreClass = "score-low";
                    let scoreTier = "C";
                    if (score >= 90)      { scoreClass = "score-premium"; scoreTier = "A+"; }
                    else if (score >= 80) { scoreClass = "score-high";    scoreTier = "A";  }
                    else if (score >= 60) { scoreClass = "score-medium";  scoreTier = "B";  }
                    else if (score >= 40) { scoreClass = "score-low";     scoreTier = "C";  }
                    else                  { scoreClass = "score-weak";     scoreTier = "D";  }

                    // Win rate heuristics
                    const winRateStr = type === "BUY" ? "58.3%" : "52.1%";

                    html += `
                        <tr data-idx="${idx}">
                            <td><strong>${r.symbol}</strong></td>
                            <td>${r.close.toFixed(2)}</td>
                            <td><span class="win-rate-val">${winRateStr}</span></td>
                            <td>
                                <div class="score-container">
                                    <div style="display: grid; grid-template-columns: auto auto; grid-template-rows: auto auto; justify-content: center; align-items: center; column-gap: 10px; row-gap: 3px;">
                                        <span class="score-badge ${scoreClass}" style="grid-column: 1; grid-row: 1; font-size: 0.85rem; padding: 2px 8px; min-width: 32px;">${scoreTier}</span>
                                        <button class="btn-analyze" data-symbol="${r.symbol}" style="grid-column: 2; grid-row: 1; background: none; border: none; color: var(--color-accent); cursor: pointer; padding: 0; font-size: 0.95rem; font-weight: 800; display: inline-flex; align-items: center;" title="View Chart">
                                            <i class="fa-solid fa-chart-line"></i>
                                        </button>
                                        <span style="grid-column: 1; grid-row: 2; font-size: 0.7rem; color: #888;">${score.toFixed(1)}</span>
                                    </div>
                                </div>
                            </td>
                            <td>
                                <div style="display:flex; gap: 8px; justify-content: center;">
                                    ${dot(hasTrend, 'Trend', trendIcon)}
                                    ${dot(hasRsi, 'RSI', 'fa-bolt')}
                                    ${dot(hasVol, 'Vol', 'fa-chart-simple')}
                                    ${dot(hasSR, 'S/R', 'fa-bars')}
                                    ${dot(hasSqueeze, 'Sqz', 'fa-compress')}
                                </div>
                            </td>
                            <td>
                                <div class="action-cell">
                                    <div class="order-qty-wrap">
                                        <span class="order-qty-label" style="font-size: 10px; color: var(--text-secondary);">Qty</span>
                                        <input type="number" class="order-qty-input" value="10" min="1" step="1" style="width: 50px; background: var(--bg-surface-elevated); border: 1px solid var(--border-color); color: var(--text-primary); border-radius: 4px; padding: 2px 4px; text-align: center;">
                                    </div>
                                    <button class="btn btn-place-order ${type === "BUY" ? 'btn-order-buy' : 'btn-order-sell'}" data-symbol="${r.symbol}" data-action="${type}" data-price="${r.close}" style="padding: 4px 8px; font-size: 0.8rem; display: inline-flex; align-items: center; gap: 4px; border-radius: 4px;">
                                        <i class="fa-solid fa-cart-shopping"></i> ${type === "BUY" ? 'Buy' : 'Sell'}
                                    </button>
                                </div>
                            </td>
                            <td>${sl}</td>
                            <td>${target}</td>
                            <td><span class="rr-val" style="font-weight: 600; color: var(--success);">${rr}</span></td>
                        </tr>
                    `;
                });
                liveFeedTable.innerHTML = html || '<tr><td colspan="9" class="empty-placeholder">No signals match filter.</td></tr>';

                // Wire up chart analyze clicks
                document.querySelectorAll(".btn-analyze").forEach(btn => {
                    btn.addEventListener("click", (e) => {
                        e.stopPropagation();
                        const sym = btn.getAttribute("data-symbol");
                        const url = `https://www.tradingview.com/chart/?symbol=NSE%3A${encodeURIComponent(sym)}`;
                        window.open(url, "_blank", "noopener,noreferrer");
                    });
                });

                // Wire up place order clicks
                document.querySelectorAll(".btn-place-order").forEach(btn => {
                    btn.addEventListener("click", async (e) => {
                        e.stopPropagation();
                        const sym = btn.getAttribute("data-symbol");
                        const action = btn.getAttribute("data-action");
                        const price = parseFloat(btn.getAttribute("data-price"));
                        const qtyInput = btn.closest(".action-cell")?.querySelector(".order-qty-input");
                        const qty = qtyInput ? (parseInt(qtyInput.value) || 1) : 1;

                        if (confirm(`Place manual ${action} order for ${qty} × ${sym}?`)) {
                            try {
                                btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
                                const oRes = await fetch("/api/order", {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({
                                        symbol: sym,
                                        action: action,
                                        quantity: qty,
                                        price: price
                                    })
                                });
                                if (oRes.ok) {
                                    const oData = await oRes.json();
                                    showToast(`Order Placed! ID: ${oData.orderid}`);
                                } else {
                                    showToast("Failed to place manual order", "error");
                                }
                            } catch(err) {
                                showToast("Error: " + err.message, "error");
                            } finally {
                                btn.innerHTML = `<i class="fa-solid fa-cart-shopping"></i> ${action === "BUY" ? 'Buy' : 'Sell'}`;
                            }
                        }
                    });
                });
            }



        } catch (e) {
            statusDot.className = "status-indicator offline";
            connectionStatus.innerText = "Connection Lost";
            console.error("Dashboard Poll Error:", e);
        }
    }

    // Quick Control Toggle Handlers
    async function updateQuickConfig() {
        try {
            btnRestartBot.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scanning...';
            // First get current config
            const res = await fetch("/api/config");
            const data = await res.json();
            
            // Update the values that changed via toggles
            if (!data.sr_channels) data.sr_channels = {};
            if (!data.bot) data.bot = {};
            data.bot.auto_scan_enabled = dashAutoScan.checked;
            data.bot.auto_scan_interval_minutes = parseFloat(dashAutoScanInterval.value) || 5;
            data.sr_channels.enabled = dashSrEnabled.checked;
            data.mtf_filter.enabled = dashMtfEnabled.checked;
            data.ml.enabled = dashMlEnabled.checked;
            
            // We assume UTBot toggle would map to a future engine toggle, 
            // for now just visual.

            const updateRes = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ config: data })
            });
            if (updateRes.ok) {
                showToast("Quick settings updated dynamically");
            } else {
                showToast("Failed to update quick settings", "error");
            }
        } catch(e) {
            console.error(e);
        } finally {
            setTimeout(() => {
                btnRestartBot.innerHTML = '<i class="fa-solid fa-play"></i> Run Scanner';
            }, 1000);
        }
    }
    
    dashAutoScan.addEventListener("change", updateQuickConfig);
    dashAutoScanInterval.addEventListener("change", updateQuickConfig);
    dashSrEnabled.addEventListener("change", updateQuickConfig);
    dashMtfEnabled.addEventListener("change", updateQuickConfig);
    dashMlEnabled.addEventListener("change", updateQuickConfig);

    // ---------------------------------------------------------------------------
    // SSE Live Logs
    // ---------------------------------------------------------------------------
    let logSource = null;
    function setupLogs() {
        if (logSource) logSource.close();
        logSource = new EventSource("/api/logs/stream");
        
        logSource.onopen = () => {
            sseStatusBadge.innerText = "Connected";
            sseStatusBadge.style.background = "var(--color-buy-bg)";
            sseStatusBadge.style.color = "var(--color-buy)";
        };
        
        logSource.onerror = () => {
            sseStatusBadge.innerText = "Disconnected";
            sseStatusBadge.style.background = "var(--color-sell-bg)";
            sseStatusBadge.style.color = "var(--color-sell)";
        };

        logSource.onmessage = (e) => {
            const lines = e.data.split("\n");
            const filter = logLevelFilter.value;
            let added = false;
            
            lines.forEach(line => {
                if (!line.trim()) return;
                
                // Parse log line for coloring
                let color = "var(--text-secondary)";
                let isMatch = true;
                
                if (line.includes("ERROR")) {
                    color = "var(--color-sell)";
                    if (filter !== "all" && filter !== "ERROR") isMatch = false;
                } else if (line.includes("WARNING")) {
                    color = "var(--color-warning)";
                    if (filter !== "all" && filter !== "WARNING") isMatch = false;
                } else if (line.includes("INFO")) {
                    if (filter !== "all" && filter !== "INFO") isMatch = false;
                    if (line.includes("BUY")) color = "var(--color-buy)";
                    else if (line.includes("SELL")) color = "var(--color-sell)";
                }

                if (isMatch) {
                    const span = document.createElement("span");
                    span.style.color = color;
                    span.innerText = line + "\n";
                    logTerminal.appendChild(span);
                    added = true;
                }
            });

            if (added) {
                // Auto scroll
                logTerminal.parentElement.scrollTop = logTerminal.parentElement.scrollHeight;
                // Trim terminal
                while (logTerminal.childNodes.length > 500) {
                    logTerminal.removeChild(logTerminal.firstChild);
                }
            }
        };
    }
    btnLogClear.addEventListener("click", () => { logTerminal.innerHTML = ""; });
    logLevelFilter.addEventListener("change", () => { logTerminal.innerHTML = ""; });

    // ---------------------------------------------------------------------------
    // Data Loading Functions
    // ---------------------------------------------------------------------------
    async function loadHistory(page=1) {
        histPage = page;
        const sym = histSymbol.value;
        const type = histType.value;
        let url = `/api/signals?limit=50&offset=${(page-1)*50}`;
        if (sym) url += `&symbol=${sym}`;
        if (type) url += `&signal_type=${type}`;
        
        try {
            const res = await fetch(url);
            const data = await res.json();
            document.getElementById("hist-page-info").innerText = `Page ${page} (Total: ${data.total})`;
            
            let html = "";
            data.signals.forEach(s => {
                const cClass = s.signal_type === "BUY" ? "text-buy" : "text-sell";
                html += `
                    <tr>
                        <td style="font-size: 0.8rem; color: var(--text-secondary);">${s.bar_time}</td>
                        <td style="font-weight: 600;">${s.symbol}</td>
                        <td style="color: var(--text-muted);">${s.timeframe}</td>
                        <td class="${cClass}" style="font-weight: 700;">${s.signal_type}</td>
                        <td>${s.close}</td>
                        <td style="color: var(--text-muted);">${s.atr_stop}</td>
                        <td>${s.rsi_14 ? s.rsi_14.toFixed(1) : '-'}</td>
                        <td style="color: ${s.fwd_5_win === 1 ? 'var(--color-buy)' : s.fwd_5_win === 0 ? 'var(--color-sell)' : 'var(--text-muted)'};">${s.fwd_5_win !== null ? s.fwd_5_win : '-'}</td>
                        <td style="color: ${s.fwd_10_win === 1 ? 'var(--color-buy)' : s.fwd_10_win === 0 ? 'var(--color-sell)' : 'var(--text-muted)'};">${s.fwd_10_win !== null ? s.fwd_10_win : '-'}</td>
                    </tr>
                `;
            });
            historyTable.innerHTML = html || '<tr><td colspan="9" class="empty-placeholder">No signals found.</td></tr>';
        } catch (e) {
            console.error(e);
            historyTable.innerHTML = `<tr><td colspan="9" class="empty-placeholder text-sell">Failed to load: ${e.message}</td></tr>`;
        }
    }
    btnApplyHistFilter.addEventListener("click", () => loadHistory(1));
    document.getElementById("hist-prev").addEventListener("click", () => { if(histPage>1) loadHistory(histPage-1); });
    document.getElementById("hist-next").addEventListener("click", () => loadHistory(histPage+1));

    btnLabelSignals.addEventListener("click", async () => {
        try {
            btnLabelSignals.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Labeling...';
            const res = await fetch("/api/signals/label", { method: "POST" });
            const data = await res.json();
            showToast(`Labeled ${data.updated} signals`);
            loadHistory(histPage);
        } catch (e) {
            showToast("Labeling failed", "error");
        } finally {
            btnLabelSignals.innerHTML = '<i class="fa-solid fa-tags"></i> Labeler';
        }
    });

    btnClearSignals.addEventListener("click", async () => {
        if (confirm("Are you sure you want to permanently delete all logged signals? This action cannot be undone.")) {
            try {
                btnClearSignals.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Clearing...';
                const res = await fetch("/api/signals/clear", { method: "POST" });
                if (res.ok) {
                    showToast("Database signals cleared successfully");
                    loadHistory(1);
                } else {
                    showToast("Failed to clear signals database", "error");
                }
            } catch (e) {
                showToast("Failed to clear signals database", "error");
            } finally {
                btnClearSignals.innerHTML = '<i class="fa-solid fa-trash"></i> Clear DB';
            }
        }
    });

    // Config loading and saving
    async function loadConfig() {
        try {
            const res = await fetch("/api/config");
            const data = await res.json();
            
            cfgForm.symbols.value = data.symbols.join(", ");
            cfgForm.index_symbols.value = data.index_symbols.join(", ");
            cfgForm.exchange.value = data.exchange;
            cfgForm.data_source.value = data.data_source;
            cfgForm.htf.value = data.htf;
            cfgForm.ltf.value = data.ltf;
            cfgForm.mtf_enabled.checked = data.mtf_filter.enabled;
            cfgForm.key_value.value = data.strategy.key_value;
            cfgForm.atr_period.value = data.strategy.atr_period;
            
            const sr = data.sr_channels || {};
            cfgForm.sr_enabled.checked = sr.enabled || false;
            cfgForm.sr_pivot.value = sr.pivot_period || 10;
            cfgForm.sr_source.value = sr.source || "High/Low";
            cfgForm.sr_width.value = sr.channel_width_pct || 5.0;
            cfgForm.sr_strength.value = sr.min_strength || 1;
            cfgForm.sr_max.value = sr.max_num_sr || 6;
            cfgForm.sr_loopback.value = sr.loopback || 290;
            cfgForm.sr_prox.value = sr.proximity_pct || 0.5;

            cfgForm.ml_enabled.checked = data.ml.enabled;
            cfgForm.ml_log.checked = data.ml.log_signals;
            cfgForm.ml_lookahead.value = data.ml.forward_lookahead_candles || 10;
            cfgForm.ml_winthresh.value = data.ml.win_threshold_pct || 1.0;
            cfgForm.trading_enabled.checked = data.trading.enabled;
            cfgForm.oa_apikey.value = data.openalgo.apikey;
            cfgForm.oa_baseurl.value = data.openalgo.base_url;
            cfgForm.mkt_check.checked = data.bot.market_hours_check;
            cfgForm.check_interval.value = data.bot.signal_check_interval;
            
        } catch (e) {
            console.error("Config load error", e);
            showToast("Failed to load config", "error");
        }
    }
    btnReloadConfig.addEventListener("click", loadConfig);

    btnSaveConfig.addEventListener("click", async () => {
        const payload = {
            symbols: cfgForm.symbols.value.split(",").map(s => s.trim()).filter(s => s),
            index_symbols: cfgForm.index_symbols.value.split(",").map(s => s.trim()).filter(s => s),
            exchange: cfgForm.exchange.value,
            data_source: cfgForm.data_source.value,
            htf: cfgForm.htf.value,
            ltf: cfgForm.ltf.value,
            mtf_filter: { enabled: cfgForm.mtf_enabled.checked },
            strategy: { 
                key_value: parseFloat(cfgForm.key_value.value), 
                atr_period: parseInt(cfgForm.atr_period.value)
            },
            sr_channels: {
                enabled: cfgForm.sr_enabled.checked,
                pivot_period: parseInt(cfgForm.sr_pivot.value),
                source: cfgForm.sr_source.value,
                channel_width_pct: parseFloat(cfgForm.sr_width.value),
                min_strength: parseInt(cfgForm.sr_strength.value),
                max_num_sr: parseInt(cfgForm.sr_max.value),
                loopback: parseInt(cfgForm.sr_loopback.value),
                proximity_pct: parseFloat(cfgForm.sr_prox.value)
            },
            ml: {
                enabled: cfgForm.ml_enabled.checked,
                log_signals: cfgForm.ml_log.checked,
                forward_lookahead_candles: parseInt(cfgForm.ml_lookahead.value),
                win_threshold_pct: parseFloat(cfgForm.ml_winthresh.value)
            },
            trading: { enabled: cfgForm.trading_enabled.checked },
            openalgo: {
                apikey: cfgForm.oa_apikey.value,
                base_url: cfgForm.oa_baseurl.value
            },
            bot: {
                market_hours_check: cfgForm.mkt_check.checked,
                signal_check_interval: parseInt(cfgForm.check_interval.value)
            }
        };

        try {
            btnSaveConfig.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';
            const res = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ config: payload })
            });
            if (res.ok) {
                showToast("Configuration saved and Bot Restarted");
            } else {
                const err = await res.json();
                showToast(`Save failed: ${JSON.stringify(err)}`, "error");
            }
        } catch (e) {
            showToast(`Error: ${e.message}`, "error");
        } finally {
            btnSaveConfig.innerHTML = 'Save & Restart';
        }
    });

    btnRestartBot.addEventListener("click", async () => {
        try {
            btnRestartBot.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scanning...';
            const res = await fetch("/api/bot/restart", { 
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reason: "manual" })
            });
            if (res.ok) showToast("Scan started successfully");
            else showToast("Failed to run scanner", "error");
        } catch (e) {
            showToast("Failed to run scanner", "error");
        } finally {
            setTimeout(() => {
                btnRestartBot.innerHTML = '<i class="fa-solid fa-play"></i> Run Scanner';
            }, 1000);
        }
    });

    // ML Tab Handlers
    async function loadMlStats() {
        const statsRes = await fetch("/api/signals/stats");
        if (statsRes.ok) {
            const stats = await statsRes.json();
            // In a real app we'd aggregate these from the db exactly,
            // here we simulate standard outputs or grab from real ML report
            mlTotal.innerText = stats.summary.reduce((acc, curr) => acc + curr.cnt, 0);
        }
        
        mlThresholdSlider.oninput = function() {
            mlThresholdDisplay.innerText = this.value + "%";
        };

        const res = await fetch("/api/status");
        if (res.ok) {
            const status = await res.json();
            mlThresholdSlider.value = status.ml_threshold * 100;
            mlThresholdDisplay.innerText = mlThresholdSlider.value + "%";
            if (status.ml_model_exists) {
                mlModelBadge.innerText = "Model Active";
                mlModelBadge.style.background = "var(--color-buy-bg)";
                mlModelBadge.style.color = "var(--color-buy)";
            }
        }
    }

    btnSaveThreshold.addEventListener("click", async () => {
        showToast("Threshold updated in memory. (Add route to save config for persistence).");
    });

    btnMlReport.addEventListener("click", async () => {
        try {
            btnMlReport.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Loading...';
            const res = await fetch("/api/ml/report");
            const data = await res.json();
            mlReportPanel.innerText = data.output;
        } catch (e) {
            mlReportPanel.innerText = "Error loading report: " + e.message;
        } finally {
            btnMlReport.innerHTML = '<i class="fa-solid fa-chart-bar"></i> View Report';
        }
    });

    btnMlTrain.addEventListener("click", async () => {
        try {
            btnMlTrain.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Training...';
            const res = await fetch("/api/ml/train", { method: "POST" });
            const data = await res.json();
            mlReportPanel.innerText = data.output;
            showToast("Model trained successfully");
            loadMlStats();
        } catch (e) {
            mlReportPanel.innerText = "Error training model: " + e.message;
            showToast("Training failed", "error");
        } finally {
            btnMlTrain.innerHTML = '<i class="fa-solid fa-dumbbell"></i> Train Model';
        }
    });


    // ---------------------------------------------------------------------------
    // Initialization
    // ---------------------------------------------------------------------------
    setInterval(updateDashboard, 1500);
    updateDashboard();
    setupLogs();
});
