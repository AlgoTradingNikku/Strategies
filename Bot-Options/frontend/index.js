/* =============================================================================
  Bot-Options / frontend / index.js
  UI Control Logic for the Options Trading Terminal dashboard.
============================================================================= */

document.addEventListener("DOMContentLoaded", () => {
    let currentUnderlying = "NIFTY";
    let activeTab = "tab-signals";
    let configData = {};
    let autoScanIntervalId = null;
    let mainUpdateIntervalId = null;

    // UI Elements Cache
    const el = {
        tabs: document.querySelectorAll(".tab-btn"),
        panes: document.querySelectorAll(".tab-pane"),
        underlyingBtns: document.querySelectorAll("#underlying-select .toggle-btn"),
        expirySelect: document.getElementById("config-expiry"),
        strikeMethodSelect: document.getElementById("config-strike-method"),
        premiumMinInput: document.getElementById("config-premium-min"),
        premiumMaxInput: document.getElementById("config-premium-max"),
        premiumRangeSec: document.getElementById("premium-range-inputs"),
        stage3Enabled: document.getElementById("config-stage3-enabled"),
        filterMtf: document.getElementById("config-filter-mtf"),
        filterEma: document.getElementById("config-filter-ema"),
        filterIv: document.getElementById("config-filter-iv"),
        filterOi: document.getElementById("config-filter-oi"),
        filterDecay: document.getElementById("config-filter-decay"),
        autoScanToggle: document.getElementById("config-auto-scan"),
        btnScanNow: document.getElementById("btn-scan-now"),
        signalsList: document.getElementById("signals-list"),
        signalsCount: document.getElementById("signals-count"),
        chainBody: document.getElementById("chain-body"),
        chainSpotPrice: document.getElementById("chain-spot-price"),
        chainExpiryDate: document.getElementById("chain-expiry-date"),
        chainMaxpain: document.getElementById("chain-maxpain"),
        chainTradePanel: document.getElementById("chain-trade-panel"),
        tradeOptType: document.getElementById("trade-opt-type"),
        tradeOptSymbol: document.getElementById("trade-opt-symbol"),
        tradeOptLtp: document.getElementById("trade-opt-ltp"),
        tradeOptMargin: document.getElementById("trade-opt-margin"),
        btnChainBuy: document.getElementById("btn-chain-buy"),
        btnChainSell: document.getElementById("btn-chain-sell"),
        btnChainClose: document.getElementById("btn-chain-close"),
        positionsList: document.getElementById("positions-list"),
        logsConsole: document.getElementById("logs-console"),
        clockTime: document.getElementById("clock-time"),
        tickerNifty: document.getElementById("ticker-nifty"),
        tickerBanknifty: document.getElementById("ticker-banknifty"),
        expiryCountdown: document.getElementById("expiry-countdown"),
        capitalAvailable: document.getElementById("capital-available"),
        capitalUsed: document.getElementById("capital-used"),
        capitalProgressFill: document.getElementById("capital-progress-fill"),
        riskDailyLoss: document.getElementById("risk-daily-loss"),
        riskLossFill: document.getElementById("risk-loss-fill"),
        riskLegsCount: document.getElementById("risk-legs-count"),
        riskTradesCount: document.getElementById("risk-trades-count"),
        riskConsecLosses: document.getElementById("risk-consec-losses"),
        statWinRate: document.getElementById("stat-win-rate"),
        statTotalSignals: document.getElementById("stat-total-signals"),
        statExecuted: document.getElementById("stat-executed"),
        statAvgPnl: document.getElementById("stat-avg-pnl"),
        portfolioDelta: document.getElementById("greek-delta"),
        portfolioTheta: document.getElementById("greek-theta"),
        portfolioVega: document.getElementById("greek-vega"),
        portfolioGamma: document.getElementById("greek-gamma")
    };

    // ---------------------------------------------------------------------------
    // Clock Initialization
    // ---------------------------------------------------------------------------
    function updateClock() {
        const now = new Date();
        el.clockTime.textContent = now.toLocaleTimeString() + " IST";
    }
    setInterval(updateClock, 1000);
    updateClock();

    // ---------------------------------------------------------------------------
    // Tab Controller
    // ---------------------------------------------------------------------------
    el.tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            el.tabs.forEach(t => t.classList.remove("active"));
            el.panes.forEach(p => p.classList.remove("active"));
            
            tab.classList.add("active");
            activeTab = tab.dataset.tab;
            document.getElementById(activeTab).classList.add("active");
            
            if (activeTab === "tab-signals") {
                stopLogsRefresh();
                loadSignals();
            } else if (activeTab === "tab-chain") {
                stopLogsRefresh();
                loadOptionChain();
            } else if (activeTab === "tab-logs") {
                startLogsRefresh();
            } else if (activeTab === "tab-analytics") {
                stopLogsRefresh();
                loadStats();
            }
        });
    });

    // ---------------------------------------------------------------------------
    // Underlying Selector
    // ---------------------------------------------------------------------------
    el.underlyingBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            el.underlyingBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentUnderlying = btn.dataset.val;
            
            if (activeTab === "tab-chain") {
                loadOptionChain();
            }
            triggerExpiryCountdown();
        });
    });

    // ---------------------------------------------------------------------------
    // Handle form show/hide premium inputs
    // ---------------------------------------------------------------------------
    el.strikeMethodSelect.addEventListener("change", () => {
        if (el.strikeMethodSelect.value === "PREMIUM") {
            el.premiumRangeSec.style.display = "block";
        } else {
            el.premiumRangeSec.style.display = "none";
        }
    });

    // ---------------------------------------------------------------------------
    // Fetch and Populate Config
    // ---------------------------------------------------------------------------
    async function loadConfig() {
        try {
            const res = await fetch("/api/config");
            configData = await res.json();
            
            // Populate basic form controls
            el.expirySelect.value = configData.strike_selection.expiry_preference;
            el.strikeMethodSelect.value = configData.strike_selection.method;
            el.premiumMinInput.value = configData.strike_selection.premium_min;
            el.premiumMaxInput.value = configData.strike_selection.premium_max;
            el.stage3Enabled.checked = configData.option_chart_confirmation.enabled;
            
            // Populate filters
            el.filterMtf.checked = configData.filters.mtf_filter_enabled;
            el.filterEma.checked = configData.filters.ema_filter_enabled;
            el.filterIv.checked = configData.filters.iv_score_enabled;
            el.filterOi.checked = configData.filters.oi_momentum_score_enabled;
            el.filterDecay.checked = configData.filters.time_decay_penalty_enabled;
            
            // Premium input toggle
            if (configData.strike_selection.method === "PREMIUM") {
                el.premiumRangeSec.style.display = "block";
            }

            // Expiry countdown trigger
            triggerExpiryCountdown();
        } catch (e) {
            console.error("Failed to load config: ", e);
        }
    }

    async function saveConfig() {
        if (!configData.strike_selection) return;

        configData.strike_selection.expiry_preference = el.expirySelect.value;
        configData.strike_selection.method = el.strikeMethodSelect.value;
        configData.strike_selection.premium_min = parseFloat(el.premiumMinInput.value) || 50;
        configData.strike_selection.premium_max = parseFloat(el.premiumMaxInput.value) || 500;
        configData.option_chart_confirmation.enabled = el.stage3Enabled.checked;
        
        configData.filters.mtf_filter_enabled = el.filterMtf.checked;
        configData.filters.ema_filter_enabled = el.filterEma.checked;
        configData.filters.iv_score_enabled = el.filterIv.checked;
        configData.filters.oi_momentum_score_enabled = el.filterOi.checked;
        configData.filters.time_decay_penalty_enabled = el.filterDecay.checked;

        try {
            await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(configData)
            });
        } catch (e) {
            console.error("Failed to save config: ", e);
        }
    }

    // Attach save listeners to inputs
    const saveTriggers = [
        el.expirySelect, el.strikeMethodSelect, el.premiumMinInput, el.premiumMaxInput,
        el.stage3Enabled, el.filterMtf, el.filterEma, el.filterIv, el.filterOi, el.filterDecay
    ];
    saveTriggers.forEach(input => input.addEventListener("change", saveConfig));

    // ---------------------------------------------------------------------------
    // Expiry Countdown Helper
    // ---------------------------------------------------------------------------
    function triggerExpiryCountdown() {
        // Mock estimate of expiry date to show countdown badge
        const today = new Date();
        const daysToThursday = (4 - today.getDay() + 7) % 7 || 7;
        el.expiryCountdown.textContent = `${daysToThursday} days remaining`;
    }

    // ---------------------------------------------------------------------------
    // Option Chain Tab Load
    // ---------------------------------------------------------------------------
    async function loadOptionChain() {
        try {
            el.chainBody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 20px;">Fetching option chain...</td></tr>`;
            
            const res = await fetch(`/api/option-chain?underlying=${currentUnderlying}&strike_count=10`);
            const payload = await res.json();
            
            if (payload.status !== "success") {
                el.chainBody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 20px; color: var(--danger-red);">Error: ${payload.detail || "Failed to fetch chain"}</td></tr>`;
                return;
            }

            const data = payload.data;
            el.chainSpotPrice.textContent = `Spot LTP: ₹${data.underlying_ltp.toFixed(2)}`;
            el.chainExpiryDate.textContent = `Expiry: ${data.expiry_date}`;
            el.chainMaxpain.textContent = `Max Pain: ₹${data.atm_strike}`;

            const chainRows = data.chain || [];
            if (chainRows.length === 0) {
                el.chainBody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 20px;">No contracts returned in option chain.</td></tr>`;
                return;
            }

            el.chainBody.innerHTML = "";
            chainRows.forEach(row => {
                const strike = row.strike;
                const ce = row.ce || {};
                const pe = row.pe || {};
                
                const isATM = strike === data.atm_strike;
                const isITM = strike < data.underlying_ltp; 
                
                const tr = document.createElement("tr");
                if (isATM) {
                    tr.classList.add("atm-row");
                } else {
                    tr.classList.add(isITM ? "itm-row" : "otm-row");
                }

                // Add call side, strike cell, put side
                tr.innerHTML = `
                    <td class="ce-col text-muted">${ce.oi ? formatNumber(ce.oi) : "-"}</td>
                    <td class="ce-col text-muted">${ce.volume ? formatNumber(ce.volume) : "-"}</td>
                    <td class="ce-col" style="color: var(--warning-amber);">${ce.iv ? ce.iv.toFixed(1) + "%" : "-"}</td>
                    <td class="ce-col ce-trade-trigger font-bold text-success cursor-pointer" data-symbol="${ce.symbol}" data-ltp="${ce.ltp}" data-type="CE">₹${ce.ltp ? ce.ltp.toFixed(2) : "-"}</td>
                    
                    <td class="strike-cell text-primary font-bold strike-header">${strike}</td>
                    
                    <td class="pe-col pe-trade-trigger font-bold text-danger cursor-pointer" data-symbol="${pe.symbol}" data-ltp="${pe.ltp}" data-type="PE">₹${pe.ltp ? pe.ltp.toFixed(2) : "-"}</td>
                    <td class="pe-col" style="color: var(--warning-amber);">${pe.iv ? pe.iv.toFixed(1) + "%" : "-"}</td>
                    <td class="pe-col text-muted">${pe.volume ? formatNumber(pe.volume) : "-"}</td>
                    <td class="pe-col text-muted">${pe.oi ? formatNumber(pe.oi) : "-"}</td>
                `;
                
                el.chainBody.appendChild(tr);
            });

            // Re-attach triggers to columns for quick trading
            document.querySelectorAll(".ce-trade-trigger, .pe-trade-trigger").forEach(cell => {
                cell.addEventListener("click", () => {
                    const symbol = cell.dataset.symbol;
                    const ltp = parseFloat(cell.dataset.ltp);
                    const type = cell.dataset.type;

                    if (!symbol || isNaN(ltp)) return;

                    el.tradeOptType.textContent = type;
                    el.tradeOptType.className = `badge ${type === "CE" ? "badge-underlying" : "badge-underlying BANKNIFTY"}`;
                    el.tradeOptSymbol.textContent = symbol;
                    el.tradeOptLtp.textContent = ltp.toFixed(2);
                    el.tradeOptMargin.textContent = (ltp * (type === "CE" ? 75 : 30)).toLocaleString();
                    el.chainTradePanel.style.display = "flex";
                });
            });

        } catch (e) {
            console.error("Chain fetch error: ", e);
            el.chainBody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 20px; color: var(--danger-red);">Connection failed</td></tr>`;
        }
    }

    // Quick trade button triggers
    el.btnChainClose.addEventListener("click", () => {
        el.chainTradePanel.style.display = "none";
    });

    async function placeManualTrade(action) {
        const symbol = el.tradeOptSymbol.textContent;
        const type = el.tradeOptType.textContent;
        const qty = type === "CE" ? 75 : 30; // standard lot
        
        try {
            const resp = await fetch("/api/order", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol: symbol,
                    action: action,
                    quantity: qty
                })
            });
            const result = await resp.json();
            if (result.status === "success") {
                alert(`Order placed successfully: ${symbol}`);
                el.chainTradePanel.style.display = "none";
                loadPositions();
            } else {
                alert(`Order failed: ${result.detail || "Unresolved error"}`);
            }
        } catch (e) {
            alert(`Network error placing order: ${e}`);
        }
    }
    el.btnChainBuy.addEventListener("click", () => placeManualTrade("BUY"));
    el.btnChainSell.addEventListener("click", () => placeManualTrade("SELL"));

    // ---------------------------------------------------------------------------
    // Signals Scan
    // ---------------------------------------------------------------------------
    // ---------------------------------------------------------------------------
    // Load Signals from DB  (called on page load AND after every scan)
    // ---------------------------------------------------------------------------
    async function loadSignals() {
        try {
            const res = await fetch("/api/signals?limit=50");
            const data = await res.json();
            if (data.status === "success") {
                renderSignals(data.signals || []);
            }
        } catch (e) {
            console.error("Signals load error: ", e);
        }
    }

    async function triggerScan() {
        el.btnScanNow.disabled = true;
        el.btnScanNow.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> SCANNING...`;

        try {
            const res = await fetch("/api/scan", { method: "POST" });
            const payload = await res.json();

            if (payload.status !== "success") {
                console.error("Scan failed: ", payload.detail);
                // Still reload signals — may have existing ones in DB
                await loadSignals();
                return;
            }

            // Always reload from DB after scan so the list reflects the
            // full history (not just signals from this single scan cycle).
            await loadSignals();

        } catch (e) {
            console.error("Scan dispatch error: ", e);
        } finally {
            el.btnScanNow.disabled = false;
            el.btnScanNow.innerHTML = `<i class="fa-solid fa-play"></i> SCAN NOW`;
        }
    }
    el.btnScanNow.addEventListener("click", triggerScan);

    function renderSignals(signals) {
        el.signalsCount.textContent = signals.length;
        if (signals.length === 0) {
            el.signalsList.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-rss-square"></i>
                    <p>No option signals yet. Trigger a scan or turn on Auto-Scan.</p>
                </div>`;
            return;
        }

        el.signalsList.innerHTML = "";
        signals.forEach(sig => {
            // score_reasons and filter_status arrive as JSON strings from the DB endpoint
            // — parse them defensively so a bad value never crashes the whole render loop.
            const reasons = Array.isArray(sig.score_reasons)
                ? sig.score_reasons
                : (() => { try { return JSON.parse(sig.score_reasons || "[]"); } catch(e) { return []; } })();

            const filterStatus = (sig.filter_status && typeof sig.filter_status === "object")
                ? sig.filter_status
                : (() => { try { return JSON.parse(sig.filter_status || "{}"); } catch(e) { return {}; } })();

            const stage3Status = filterStatus.stage3 || "unknown";
            const stage3Color  = stage3Status === "confirmed" ? "var(--success-green)" : "var(--danger-red)";

            const reasonsHtml = reasons.length
                ? reasons.map(r => `<span>• ${r}</span>`).join("")
                : `<span class="text-muted">No scoring details available.</span>`;

            const card = document.createElement("div");
            card.className = `signal-card ${sig.direction.toLowerCase()}`;

            card.innerHTML = `
                <div class="card-top">
                    <div class="sym-info">
                        <span class="badge-underlying ${sig.underlying}">${sig.underlying}</span>
                        <span class="sym-text">${sig.symbol}</span>
                        <span class="expiry-text">Exp: ${sig.expiry}</span>
                    </div>
                    <span class="badge-direction ${sig.direction.toLowerCase()}">${sig.direction}</span>
                </div>
                <div class="card-middle">
                    <span class="strat-tag">${sig.strategy_name || "UTBot+SR"} · Stage 3: <b style="color:${stage3Color}">${stage3Status}</b></span>
                    <div class="score-arc-widget">
                        <span class="score-text">${parseFloat(sig.confidence_score || 0).toFixed(0)}/100</span>
                    </div>
                </div>
                <div class="card-data-row">
                    <div class="data-cell">
                        <span class="lbl">Premium</span>
                        <span class="val">₹${parseFloat(sig.entry_premium || 0).toFixed(2)}</span>
                    </div>
                    <div class="data-cell">
                        <span class="lbl">Spot</span>
                        <span class="val">₹${parseFloat(sig.underlying_price || 0).toFixed(2)}</span>
                    </div>
                    <div class="data-cell">
                        <span class="lbl">IV</span>
                        <span class="val">${sig.iv_proxy ? parseFloat(sig.iv_proxy).toFixed(1) + "%" : "-"}</span>
                    </div>
                    <div class="data-cell">
                        <span class="lbl">OI</span>
                        <span class="val">${sig.oi_at_signal ? formatNumber(sig.oi_at_signal) : "-"}</span>
                    </div>
                    <div class="data-cell">
                        <span class="lbl">Status</span>
                        <span class="val">${sig.status || "SIGNAL"}</span>
                    </div>
                </div>
                <div class="card-reasons">
                    ${reasonsHtml}
                </div>
                <div class="card-bottom">
                    <span class="time-lbl">Triggered at: ${sig.timestamp}</span>
                    <div class="card-actions">
                        <button class="btn btn-primary execute-sig-btn" data-symbol="${sig.symbol}" data-premium="${sig.entry_premium}">EXECUTE</button>
                    </div>
                </div>
            `;
            
            el.signalsList.appendChild(card);
        });

        // Add execution click listeners
        document.querySelectorAll(".execute-sig-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const sym = btn.dataset.symbol;
                btn.disabled = true;
                btn.textContent = "PLACING...";
                
                try {
                    const resp = await fetch("/api/order", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            symbol: sym,
                            action: "BUY",
                            quantity: 75 // default
                        })
                    });
                    const res = await resp.json();
                    if (res.status === "success") {
                        alert("Trade executed successfully!");
                        loadPositions();
                    } else {
                        alert("Execution failed: " + res.detail);
                    }
                } catch (e) {
                    console.error("Order submit failed: ", e);
                } finally {
                    btn.disabled = false;
                    btn.textContent = "EXECUTE";
                }
            });
        });
    }

    // ---------------------------------------------------------------------------
    // Fetch Positions & Risk Metrics
    // ---------------------------------------------------------------------------
    async function loadPositions() {
        try {
            const res = await fetch("/api/positions");
            const payload = await res.json();
            
            if (payload.status !== "success") return;
            
            const positions = payload.positions || [];
            
            if (positions.length === 0) {
                el.positionsList.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-folder-open"></i>
                        <p>No active positions found.</p>
                    </div>`;
                el.riskLegsCount.textContent = `0 / 5`;
                updatePortfolioGreeks([]);
                return;
            }

            el.positionsList.innerHTML = "";
            el.riskLegsCount.textContent = `${positions.length} / 5`;
            
            let totalDelta = 0.0;
            let totalTheta = 0.0;

            positions.forEach(pos => {
                const currentPremium = parseFloat(pos.current_premium || pos.entry_premium);
                const entryPremium = parseFloat(pos.entry_premium);
                const pnl = currentPremium - entryPremium;
                const pnlPct = (pnl / entryPremium) * 100.0;
                const quantity = intVal(pos.quantity);
                const pnlAmount = pnl * quantity;

                // Greek accumulation estimates
                totalDelta += 0.49 * (pos.option_type === "CE" ? 1 : -1) * (quantity / 75.0);
                totalTheta += -4.2 * (quantity / 75.0);

                const card = document.createElement("div");
                card.className = `pos-card ${pnl >= 0 ? 'up' : 'down'}`;
                
                card.innerHTML = `
                    <div class="pos-top">
                        <span class="pos-sym">${pos.symbol}</span>
                        <span class="pos-pnl ${pnl >= 0 ? 'profit' : 'loss'}">
                            ₹${pnlAmount.toFixed(2)} (${pnlPct.toFixed(1)}%)
                        </span>
                    </div>
                    <div class="pos-details">
                        <span>LTP: ₹${currentPremium.toFixed(2)}</span>
                        <span>Entry: ₹${entryPremium.toFixed(2)}</span>
                        <span>Qty: ${quantity}</span>
                    </div>
                    <div class="pos-action-row">
                        <span class="badge-status">SL: ₹${pos.current_sl_premium.toFixed(2)}</span>
                        <button class="pos-close-btn" data-id="${pos.id}">CLOSE</button>
                    </div>
                `;
                
                el.positionsList.appendChild(card);
            });

            // Update greeks
            el.portfolioDelta.textContent = (totalDelta >= 0 ? "+" : "") + totalDelta.toFixed(2);
            el.portfolioTheta.textContent = `-₹${Math.abs(totalTheta).toFixed(2)}/d`;

            // Attach close triggers
            document.querySelectorAll(".pos-close-btn").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const posId = btn.dataset.id;
                    btn.disabled = true;
                    btn.textContent = "CLOSING...";
                    
                    try {
                        const resp = await fetch(`/api/positions/${posId}/close`, { method: "POST" });
                        const res = await resp.json();
                        if (res.status === "success") {
                            loadPositions();
                        } else {
                            alert("Failed to close position: " + res.detail);
                        }
                    } catch (e) {
                        console.error(e);
                    }
                });
            });

        } catch (e) {
            console.error("Positions load error: ", e);
        }
    }

    function updatePortfolioGreeks(positions) {
        el.portfolioDelta.textContent = "+0.00";
        el.portfolioTheta.textContent = "-₹0.00/d";
        el.portfolioVega.textContent = "+0.00";
        el.portfolioGamma.textContent = "+0.0000";
    }

    // ---------------------------------------------------------------------------
    // Fetch Index Ticker Levels
    // ---------------------------------------------------------------------------
    async function loadIndexTicks() {
        try {
            // In a production codebase, this would call index ticks API. 
            // We simulate it dynamically for visual feedback or fetch from existing endpoint.
            const niftyLtp = 23456.75 + (Math.random() - 0.5) * 5;
            const bankLtp = 51234.50 + (Math.random() - 0.5) * 15;
            
            el.tickerNifty.querySelector(".ticker-price").textContent = niftyLtp.toFixed(2);
            el.tickerNifty.querySelector(".ticker-change").textContent = "+1.23% (+286 pts)";
            el.tickerNifty.querySelector(".ticker-change").className = "ticker-change up";

            el.tickerBanknifty.querySelector(".ticker-price").textContent = bankLtp.toFixed(2);
            el.tickerBanknifty.querySelector(".ticker-change").textContent = "-0.45% (-231 pts)";
            el.tickerBanknifty.querySelector(".ticker-change").className = "ticker-change down";
        } catch (e) {
            console.error("Ticker fetch error: ", e);
        }
    }

    // ---------------------------------------------------------------------------
    // Logs Tab — live tail with auto-refresh and controls
    // ---------------------------------------------------------------------------
    let logsRefreshIntervalId = null;

    async function loadLogs() {
        try {
            const res = await fetch("/api/logs?lines=200");
            const data = await res.json();
            el.logsConsole.textContent = data.logs || "No logs available.";
            const autoScroll = document.getElementById("log-autoscroll");
            if (!autoScroll || autoScroll.checked) {
                el.logsConsole.scrollTop = el.logsConsole.scrollHeight;
            }
        } catch (e) {
            el.logsConsole.textContent = "Failed to load log feed.";
        }
    }

    function startLogsRefresh() {
        if (logsRefreshIntervalId) return;
        loadLogs();
        logsRefreshIntervalId = setInterval(() => {
            const autoRefresh = document.getElementById("log-autorefresh");
            if (autoRefresh && autoRefresh.checked) loadLogs();
        }, 3000);
    }

    function stopLogsRefresh() {
        if (logsRefreshIntervalId) {
            clearInterval(logsRefreshIntervalId);
            logsRefreshIntervalId = null;
        }
    }

    // VIEW LOGS button in left panel — switch tab and start live feed
    const btnViewLogs = document.getElementById("btn-view-logs");
    if (btnViewLogs) {
        btnViewLogs.addEventListener("click", () => {
            el.tabs.forEach(t => t.classList.remove("active"));
            el.panes.forEach(p => p.classList.remove("active"));
            const logsTab = document.querySelector('[data-tab="tab-logs"]');
            if (logsTab) logsTab.classList.add("active");
            document.getElementById("tab-logs").classList.add("active");
            activeTab = "tab-logs";
            startLogsRefresh();
        });
    }

    // Manual Refresh button inside Logs tab
    const btnLogRefresh = document.getElementById("btn-log-refresh");
    if (btnLogRefresh) btnLogRefresh.addEventListener("click", loadLogs);

    // Clear View button — clears the visible pre without touching the file
    const btnLogClear = document.getElementById("btn-log-clear");
    if (btnLogClear) btnLogClear.addEventListener("click", () => {
        el.logsConsole.textContent = "— view cleared (file unchanged) —";
    });

    // ---------------------------------------------------------------------------
    // Stats Tab Content
    // ---------------------------------------------------------------------------
    async function loadStats() {
        try {
            const res = await fetch("/api/statistics");
            const data = await res.json();
            if (data.status === "success") {
                const stats = data.statistics;
                el.statWinRate.textContent = `${stats.win_rate}%`;
                el.statTotalSignals.textContent = stats.total_signals;
                el.statExecuted.textContent = stats.executed_signals;
                el.statAvgPnl.textContent = `${stats.avg_pnl_pct >= 0 ? "+" : ""}${stats.avg_pnl_pct}%`;

                // Update risk widget limits
                el.riskTradesCount.textContent = `${stats.executed_signals} / 10`;
            }
        } catch (e) {
            console.error("Stats load error: ", e);
        }
    }

    // ---------------------------------------------------------------------------
    // Auto Scan Loop Manager
    // ---------------------------------------------------------------------------
    let autoScanRunning = false;   // guard: never fire a new scan while one is in flight

    async function autoScanTick() {
        if (autoScanRunning) return;   // previous scan still running — skip this tick
        autoScanRunning = true;
        try {
            await triggerScan();
        } finally {
            autoScanRunning = false;
        }
    }

    el.autoScanToggle.addEventListener("change", () => {
        if (el.autoScanToggle.checked) {
            const scanIntervalSec = configData.scan_interval_seconds || 60;
            // Enforce a minimum of 30 seconds to avoid hammering broker APIs
            const intervalMs = Math.max(scanIntervalSec, 30) * 1000;
            autoScanIntervalId = setInterval(autoScanTick, intervalMs);
            console.info(`Auto scan loop enabled — interval: ${intervalMs / 1000}s`);
        } else {
            if (autoScanIntervalId) {
                clearInterval(autoScanIntervalId);
                autoScanIntervalId = null;
                console.info("Auto scan loop disabled.");
            }
        }
    });

    // Helper functions
    function formatNumber(num) {
        if (num >= 10000000) return (num / 10000000).toFixed(2) + " Cr";
        if (num >= 100000) return (num / 100000).toFixed(2) + " L";
        if (num >= 1000) return (num / 1000).toFixed(1) + " K";
        return num.toString();
    }

    function intVal(val) {
        return parseInt(val) || 0;
    }

    // Startup Initialization
    loadConfig();
    loadSignals();
    loadPositions();
    loadIndexTicks();
    
    // Main updates loop (every 5 seconds)
    mainUpdateIntervalId = setInterval(() => {
        loadPositions();
        loadIndexTicks();
        if (activeTab === "tab-chain") {
            loadOptionChain();
        } else if (activeTab === "tab-logs") {
            loadLogs();
        }
    }, 5000);
});
