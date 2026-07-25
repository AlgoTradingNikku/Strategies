/* ==========================================================================
   Logical Controller & Charting — UTBot + SR Channels Dashboard
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // State management
    let activeConfig = null;
    let autoRefreshInterval = null;
    let isScanning = false;
    let historyPage = 0;
    const historyLimit = 15;

    // Elements cache
    const tabButtons = document.querySelectorAll(".nav-item");
    const tabPanels = document.querySelectorAll(".tab-panel");
    const btnRunScan = document.getElementById("btn-run-scan");
    const btnToggleAutoRefresh = document.getElementById("btn-toggle-auto-refresh");
    const autoRefreshState = document.getElementById("auto-refresh-state");
    const connectionStatus = document.getElementById("connection-status");
    const activeScanInfo = document.getElementById("active-scan-info");

    // Stats
    const statScannedCount = document.getElementById("stat-scanned-count");
    const statScannedDetails = document.getElementById("stat-scanned-details");
    const statBuyCount = document.getElementById("stat-buy-count");
    const statSellCount = document.getElementById("stat-sell-count");
    const statLastScanTime = document.getElementById("stat-last-scan-time");

    // Tables & Search
    const buySignalsTable = document.getElementById("buy-signals-table").querySelector("tbody");
    const sellSignalsTable = document.getElementById("sell-signals-table").querySelector("tbody");
    const buySearch = document.getElementById("buy-search");
    const sellSearch = document.getElementById("sell-search");

    // Config form
    const configForm = document.getElementById("config-form");
    const btnResetConfig = document.getElementById("btn-reset-config");


    // Logs
    const btnClearTerminal = document.getElementById("btn-clear-terminal");
    const btnRefreshLogs = document.getElementById("btn-refresh-logs");
    const logsTerminal = document.getElementById("logs-terminal-output");

    // History Panel Cache
    const btnPrevPage = document.getElementById("btn-prev-page");
    const btnNextPage = document.getElementById("btn-next-page");
    const btnRefreshHistory = document.getElementById("btn-refresh-history");
    const historySignalsTable = document.getElementById("history-signals-table")?.querySelector("tbody");
    const historyPageInfo = document.getElementById("history-page-info");

    // Stats Panel Cache
    const statsTotalSignals = document.getElementById("stats-total-signals");
    const statsCheckedSignals = document.getElementById("stats-checked-signals");
    const statsAvgRrWinners = document.getElementById("stats-avg-rr-winners");
    const statsAvgRrLosers = document.getElementById("stats-avg-rr-losers");
    const statsScoreTiersBody = document.getElementById("stats-score-tiers-body");
    const statsDirectionsTimeframesBody = document.getElementById("stats-directions-timeframes-body");

    // API Base URL
    const API_BASE = window.location.origin;

    // ---------------------------------------------------------------------------
    // Tab Switching Logic
    // ---------------------------------------------------------------------------
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.getAttribute("data-tab");
            
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanels.forEach(p => p.classList.remove("active"));
            
            btn.classList.add("active");
            document.getElementById(`panel-${targetTab}`).classList.add("active");
            
            // Auto refresh logs when loading log panel
            if (targetTab === "logs") {
                loadLogs();
            } else if (targetTab === "history") {
                loadHistory();
            } else if (targetTab === "stats") {
                loadStats();
            }
        });
    });

    // ---------------------------------------------------------------------------
    // Loading Config Data
    // ---------------------------------------------------------------------------
    async function loadConfig() {
        try {
            const resp = await fetch(`${API_BASE}/api/config`);
            if (!resp.ok) throw new Error("Failed to load config file.");
            const cfg = await resp.json();
            activeConfig = cfg;
            
            // Update Active Config display text
            const seg = Array.isArray(cfg.segment) ? cfg.segment.join("+") : (cfg.segment || "CUSTOM");
            activeScanInfo.textContent = `Profile: ${cfg.data_source.toUpperCase()} | TF: ${cfg.scan_timeframe} | Segments: ${seg}`;
            
            // Populate Config Form
            document.getElementById("cfg-data-source").value = cfg.data_source;
            document.getElementById("cfg-exchange").value = cfg.exchange;
            document.getElementById("cfg-timeframe").value = cfg.scan_timeframe;
            document.getElementById("cfg-interval").value = cfg.scan_interval_seconds;
            document.getElementById("cfg-lookback").value = cfg.signal_lookback_candles;

            // Strategy
            document.getElementById("cfg-ut-enabled").checked = !!cfg.strategy.ut_enabled;
            document.getElementById("cfg-ut-key").value = cfg.strategy.key_value;
            document.getElementById("cfg-ut-atr").value = cfg.strategy.atr_period;
            document.getElementById("cfg-ut-ha").checked = cfg.strategy.use_heikin_ashi;

            // SR Channels
            document.getElementById("cfg-sr-enabled").checked = !!cfg.sr_channels.enabled;
            document.getElementById("cfg-sr-pivot").value = cfg.sr_channels.pivot_period;
            document.getElementById("cfg-sr-source").value = cfg.sr_channels.source;
            document.getElementById("cfg-sr-width").value = cfg.sr_channels.channel_width_pct;
            document.getElementById("cfg-sr-strength").value = cfg.sr_channels.min_strength;
            document.getElementById("cfg-sr-max").value = cfg.sr_channels.max_num_sr;
            document.getElementById("cfg-sr-loopback").value = cfg.sr_channels.loopback;
            document.getElementById("cfg-sr-prox").value = cfg.sr_channels.proximity_pct;

            // Setup Filters
            if (cfg.filters) {
                document.getElementById("cfg-filters-ema").checked = !!cfg.filters.ema_filter_enabled;
                document.getElementById("cfg-filters-ema-period").value = cfg.filters.ema_period !== undefined ? cfg.filters.ema_period : 200;
                document.getElementById("cfg-filters-volume").checked = !!cfg.filters.volume_filter_enabled;
                document.getElementById("cfg-filters-vol-sma").value = cfg.filters.volume_sma_period !== undefined ? cfg.filters.volume_sma_period : 20;
                document.getElementById("cfg-filters-vol-pct").value = cfg.filters.volume_min_pct !== undefined ? cfg.filters.volume_min_pct : 80;
                document.getElementById("cfg-filters-score").value = cfg.filters.min_alert_score !== undefined ? cfg.filters.min_alert_score : 70;
                
                document.getElementById("cfg-filters-mtf").checked = !!cfg.filters.mtf_enabled;
                document.getElementById("cfg-filters-mtf-tf").value = cfg.filters.mtf_timeframe || "1h";
                document.getElementById("cfg-filters-mtf-align").checked = !!cfg.filters.require_mtf_alignment;
                document.getElementById("cfg-filters-mtf-neutral").value = cfg.filters.mtf_neutral_pct !== undefined ? cfg.filters.mtf_neutral_pct : 0.3;

                document.getElementById("cfg-filters-adx-filt").checked = !!cfg.filters.adx_filter_enabled;
                document.getElementById("cfg-filters-adx-val").value = cfg.filters.adx_min_threshold !== undefined ? cfg.filters.adx_min_threshold : 20;
                document.getElementById("cfg-filters-adx-strong").value = cfg.filters.adx_strong_threshold !== undefined ? cfg.filters.adx_strong_threshold : 25;
                document.getElementById("cfg-filters-adx-moderate").value = cfg.filters.adx_moderate_threshold !== undefined ? cfg.filters.adx_moderate_threshold : 20;

                document.getElementById("cfg-filters-rsi-filter").checked = !!cfg.filters.rsi_filter_enabled;
                document.getElementById("cfg-filters-rsi-period").value = cfg.filters.rsi_period !== undefined ? cfg.filters.rsi_period : 14;
                document.getElementById("cfg-filters-rsi-buy-min").value = cfg.filters.rsi_buy_min !== undefined ? cfg.filters.rsi_buy_min : 40;
                document.getElementById("cfg-filters-rsi-buy-max").value = cfg.filters.rsi_buy_max !== undefined ? cfg.filters.rsi_buy_max : 65;
                document.getElementById("cfg-filters-rsi-sell-min").value = cfg.filters.rsi_sell_min !== undefined ? cfg.filters.rsi_sell_min : 35;
                document.getElementById("cfg-filters-rsi-sell-max").value = cfg.filters.rsi_sell_max !== undefined ? cfg.filters.rsi_sell_max : 60;

                document.getElementById("cfg-filters-rs-period").value = cfg.filters.rs_period !== undefined ? cfg.filters.rs_period : 20;
                document.getElementById("cfg-filters-rs-buy").value = cfg.filters.rs_buy_threshold !== undefined ? cfg.filters.rs_buy_threshold : 1.1;
                document.getElementById("cfg-filters-rs-sell").value = cfg.filters.rs_sell_threshold !== undefined ? cfg.filters.rs_sell_threshold : 0.9;

                document.getElementById("cfg-filters-rr-enabled").checked = !!cfg.filters.risk_reward_enabled;
                document.getElementById("cfg-filters-rr-mult").value = cfg.filters.rr_atr_multiplier !== undefined ? cfg.filters.rr_atr_multiplier : 0.5;
                document.getElementById("cfg-filters-rr-ratio").value = cfg.filters.rr_default_ratio !== undefined ? cfg.filters.rr_default_ratio : 2.0;
                document.getElementById("cfg-filters-candle").checked = !!cfg.filters.candle_patterns_enabled;
                document.getElementById("cfg-filters-history").checked = !!cfg.filters.signal_history_enabled;
                document.getElementById("cfg-filters-history-hours").value = cfg.filters.outcome_check_hours !== undefined ? cfg.filters.outcome_check_hours : 4;
            }

            // Alerts
            document.getElementById("cfg-tg-mode").value = cfg.telegram.mode;
            document.getElementById("cfg-tg-token").value = cfg.telegram.bot_token;
            document.getElementById("cfg-tg-chat").value = cfg.telegram.chat_id;

            // Segments & Custom List
            document.getElementById("cfg-segments").value = Array.isArray(cfg.segment) ? cfg.segment.join(", ") : cfg.segment;
            document.getElementById("cfg-use-symbols").checked = cfg.use_symbols;
            
            // Update Connection banner
            connectionStatus.textContent = "Scanner System Online";
            document.querySelector(".status-indicator").className = "status-indicator online";
        } catch (err) {
            console.error(err);
            connectionStatus.textContent = "Offline / Connection Error";
            document.querySelector(".status-indicator").className = "status-indicator offline";
        }
    }

    // ---------------------------------------------------------------------------
    // Saving Config Data
    // ---------------------------------------------------------------------------
    configForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!activeConfig) return;

        // Build Payload keeping data block intact
        const payload = {
            data_source: document.getElementById("cfg-data-source").value,
            exchange: document.getElementById("cfg-exchange").value,
            scan_timeframe: document.getElementById("cfg-timeframe").value,
            scan_interval_seconds: parseInt(document.getElementById("cfg-interval").value),
            signal_lookback_candles: parseInt(document.getElementById("cfg-lookback").value),
            strategy: {
                ut_enabled: document.getElementById("cfg-ut-enabled").checked,
                key_value: parseFloat(document.getElementById("cfg-ut-key").value),
                atr_period: parseInt(document.getElementById("cfg-ut-atr").value),
                use_heikin_ashi: document.getElementById("cfg-ut-ha").checked
            },
            sr_channels: {
                enabled: document.getElementById("cfg-sr-enabled").checked,
                pivot_period: parseInt(document.getElementById("cfg-sr-pivot").value),
                source: document.getElementById("cfg-sr-source").value,
                channel_width_pct: parseFloat(document.getElementById("cfg-sr-width").value),
                min_strength: parseInt(document.getElementById("cfg-sr-strength").value),
                max_num_sr: parseInt(document.getElementById("cfg-sr-max").value),
                loopback: parseInt(document.getElementById("cfg-sr-loopback").value),
                proximity_pct: parseFloat(document.getElementById("cfg-sr-prox").value)
            },
            filters: {
                ema_filter_enabled: document.getElementById("cfg-filters-ema").checked,
                ema_period: parseInt(document.getElementById("cfg-filters-ema-period").value || 200),
                volume_filter_enabled: document.getElementById("cfg-filters-volume").checked,
                volume_sma_period: parseInt(document.getElementById("cfg-filters-vol-sma").value || 20),
                volume_min_pct: parseInt(document.getElementById("cfg-filters-vol-pct").value || 80),
                min_alert_score: parseInt(document.getElementById("cfg-filters-score").value || 70),
                mtf_enabled: document.getElementById("cfg-filters-mtf").checked,
                mtf_timeframe: document.getElementById("cfg-filters-mtf-tf").value,
                require_mtf_alignment: document.getElementById("cfg-filters-mtf-align").checked,
                mtf_neutral_pct: parseFloat(document.getElementById("cfg-filters-mtf-neutral").value || 0.3),
                adx_filter_enabled: document.getElementById("cfg-filters-adx-filt").checked,
                adx_min_threshold: parseFloat(document.getElementById("cfg-filters-adx-val").value || 20),
                adx_strong_threshold: parseFloat(document.getElementById("cfg-filters-adx-strong").value || 25),
                adx_moderate_threshold: parseFloat(document.getElementById("cfg-filters-adx-moderate").value || 20),
                rsi_filter_enabled: document.getElementById("cfg-filters-rsi-filter").checked,
                rsi_period: parseInt(document.getElementById("cfg-filters-rsi-period").value || 14),
                rsi_buy_min: parseFloat(document.getElementById("cfg-filters-rsi-buy-min").value || 40),
                rsi_buy_max: parseFloat(document.getElementById("cfg-filters-rsi-buy-max").value || 65),
                rsi_sell_min: parseFloat(document.getElementById("cfg-filters-rsi-sell-min").value || 35),
                rsi_sell_max: parseFloat(document.getElementById("cfg-filters-rsi-sell-max").value || 60),
                rs_period: parseInt(document.getElementById("cfg-filters-rs-period").value || 20),
                rs_buy_threshold: parseFloat(document.getElementById("cfg-filters-rs-buy").value || 1.1),
                rs_sell_threshold: parseFloat(document.getElementById("cfg-filters-rs-sell").value || 0.9),
                risk_reward_enabled: document.getElementById("cfg-filters-rr-enabled").checked,
                rr_atr_multiplier: parseFloat(document.getElementById("cfg-filters-rr-mult").value || 0.5),
                rr_default_ratio: parseFloat(document.getElementById("cfg-filters-rr-ratio").value || 2.0),
                candle_patterns_enabled: document.getElementById("cfg-filters-candle").checked,
                signal_history_enabled: document.getElementById("cfg-filters-history").checked,
                outcome_check_hours: parseInt(document.getElementById("cfg-filters-history-hours").value || 4)
            },
            telegram: {
                mode: document.getElementById("cfg-tg-mode").value,
                bot_token: document.getElementById("cfg-tg-token").value,
                chat_id: document.getElementById("cfg-tg-chat").value
            },
            openalgo: activeConfig.openalgo,
            data: activeConfig.data,
            bot: {
                log_level: activeConfig.bot.log_level,
                market_hours_check: activeConfig.bot.market_hours_check,
                market_open: activeConfig.bot.market_open,
                market_close: activeConfig.bot.market_close
            },
            symbols: activeConfig.symbols,
            use_symbols: document.getElementById("cfg-use-symbols").checked
        };

        // Handle Segment formatting
        const rawSeg = document.getElementById("cfg-segments").value.trim();
        if (rawSeg === "") {
            payload.segment = "";
        } else if (rawSeg.includes(",")) {
            payload.segment = rawSeg.split(",").map(s => s.trim().toUpperCase()).filter(s => s);
        } else {
            payload.segment = rawSeg.toUpperCase();
        }

        try {
            const resp = await fetch(`${API_BASE}/api/config`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error("Failed to save configuration.");
            alert("✅ Configuration saved and loaded successfully!");
            loadConfig();
        } catch (err) {
            alert(`❌ Error saving config: ${err.message}`);
        }
    });

    btnResetConfig.addEventListener("click", () => {
        if (confirm("Reset form values to last saved state?")) {
            loadConfig();
        }
    });

    // ---------------------------------------------------------------------------
    // Logging Terminal Logic
    // ---------------------------------------------------------------------------
    async function loadLogs() {
        try {
            const resp = await fetch(`${API_BASE}/api/logs`);
            if (!resp.ok) throw new Error("Failed to load logs.");
            const data = await resp.json();
            logsTerminal.textContent = data.logs;
            // Scroll to bottom
            logsTerminal.scrollTop = logsTerminal.scrollHeight;
        } catch (err) {
            logsTerminal.textContent = `Error loading system logs: ${err.message}`;
        }
    }

    btnClearTerminal.addEventListener("click", () => {
        logsTerminal.textContent = "";
    });

    btnRefreshLogs.addEventListener("click", loadLogs);

    // ---------------------------------------------------------------------------
    // Running Scans
    // ---------------------------------------------------------------------------
    async function executeScan() {
        if (isScanning) return;
        isScanning = true;
        btnRunScan.disabled = true;
        btnRunScan.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Scanning...`;
        
        try {
            const resp = await fetch(`${API_BASE}/api/scan`, { method: "POST" });
            if (!resp.ok) throw new Error("Trigger scan failed on backend server.");
            const data = await resp.json();
            renderScanData(data);
        } catch (err) {
            alert(`❌ Scan Execution Error: ${err.message}`);
        } finally {
            isScanning = false;
            btnRunScan.disabled = false;
            btnRunScan.innerHTML = `<i class="fa-solid fa-play"></i> Run Scanner`;
            loadLogs(); // Refresh logs to capture scan output
        }
    }

    btnRunScan.addEventListener("click", executeScan);

    function renderScanData(data) {
        // Clear tables
        buySignalsTable.innerHTML = "";
        sellSignalsTable.innerHTML = "";

        // Collect all symbols scanned
        const allSymbols = new Set();
        
        // Renders rows
        const createRow = (item, type) => {
            allSymbols.add(item.symbol);
            const tr = document.createElement("tr");

            const sl = item.stop_loss !== null && item.stop_loss !== undefined ? item.stop_loss.toFixed(2) : "—";
            const target = item.target !== null && item.target !== undefined ? item.target.toFixed(2) : "—";
            const rr = item.risk_reward !== null && item.risk_reward !== undefined ? item.risk_reward.toFixed(1) : "—";

            const score = item.setup_score !== undefined ? item.setup_score : 0;
            let scoreClass = "score-low";
            if (score >= 70) scoreClass = "score-high";
            else if (score >= 40) scoreClass = "score-medium";

            const reasonsList = item.score_reasons || [];
            const reasonsHtml = reasonsList.length > 0
                ? `<div class="score-tooltip">
                    <strong>Score Breakdown:</strong>
                    <ul>${reasonsList.map(r => `<li>${r}</li>`).join("")}</ul>
                   </div>`
                : "";

            tr.innerHTML = `
                <td><strong>${item.symbol}</strong></td>
                <td>${item.close.toFixed(2)}</td>
                <td>${sl}</td>
                <td>${target}</td>
                <td><span class="rr-val" style="font-weight: 600; color: ${item.risk_reward >= 2.0 ? 'var(--success)' : 'inherit'}">${rr}</span></td>
                <td>
                    <div class="score-container">
                        <span class="score-badge ${scoreClass}">${score.toFixed(1)}</span>
                        ${reasonsHtml}
                    </div>
                </td>
                <td>
                    <div class="badge-group">
                        ${item.triggered.map(cond => {
                            const styleClass = cond.includes("UT") ? "ut-badge-type" : "sr-badge-type";
                            return `<span class="condition-badge ${styleClass}">${cond}</span>`;
                        }).join("")}
                    </div>
                </td>
                <td>
                    <button class="btn btn-secondary btn-analyze" data-symbol="${item.symbol}" style="padding: 4px 10px; font-size: 0.75rem;">
                        <i class="fa-solid fa-chart-line"></i> Chart
                    </button>
                </td>
            `;
            return tr;
        };

        // Render BUY Signals
        if (data.buy_signals && data.buy_signals.length > 0) {
            data.buy_signals.forEach(item => {
                buySignalsTable.appendChild(createRow(item, "BUY"));
            });
        } else {
            buySignalsTable.innerHTML = `<tr><td colspan="8" class="empty-placeholder">No BUY signals found for this scan interval.</td></tr>`;
        }

        // Render SELL Signals
        if (data.sell_signals && data.sell_signals.length > 0) {
            data.sell_signals.forEach(item => {
                sellSignalsTable.appendChild(createRow(item, "SELL"));
            });
        } else {
            sellSignalsTable.innerHTML = `<tr><td colspan="8" class="empty-placeholder">No SELL signals found for this scan interval.</td></tr>`;
        }

        // Setup analyze buttons — open TradingView chart in a new tab
        document.querySelectorAll(".btn-analyze").forEach(btn => {
            btn.addEventListener("click", () => {
                const sym = btn.getAttribute("data-symbol");
                const url = `https://www.tradingview.com/chart/?symbol=NSE%3A${encodeURIComponent(sym)}`;
                window.open(url, "_blank", "noopener,noreferrer");
            });
        });

        // Update stats
        const total = (data.buy_signals?.length || 0) + (data.sell_signals?.length || 0);
        statBuyCount.textContent = data.buy_signals?.length || 0;
        statSellCount.textContent = data.sell_signals?.length || 0;
        // Show only the time portion of the timestamp (e.g. "20:59:15")
        const rawTs = data.timestamp || "";
        const timeOnly = rawTs.includes(" ") ? rawTs.split(" ")[1] : (rawTs || new Date().toLocaleTimeString());
        statLastScanTime.textContent = timeOnly;

        // Calculate dynamically the scanned count
        // Default to a fallback if we don't know the exact count
        let totalCountScanned = 50; // default Nifty50 constituents
        if (data.segment_label.includes("BANKNIFTY") && !data.segment_label.includes("NIFTY50")) {
            totalCountScanned = 14;
        } else if (data.segment_label.includes("BANKNIFTY") && data.segment_label.includes("NIFTY50")) {
            totalCountScanned = 59; // deduplicated count
        }
        statScannedCount.textContent = totalCountScanned;
        statScannedDetails.textContent = `Segments: ${data.segment_label}`;
    }   // ← end renderScanData()

    // ---------------------------------------------------------------------------
    // Auto-Refresh Manager
    // ---------------------------------------------------------------------------
    function initAutoRefresh() {
        // Toggle action
        btnToggleAutoRefresh.addEventListener("click", () => {
            if (autoRefreshInterval) {
                // Disable
                clearInterval(autoRefreshInterval);
                autoRefreshInterval = null;
                autoRefreshState.textContent = "OFF";
                btnToggleAutoRefresh.classList.replace("btn-primary", "btn-secondary");
            } else {
                // Enable
                const seconds = activeConfig?.scan_interval_seconds || 300;
                autoRefreshInterval = setInterval(executeScan, seconds * 1000);
                autoRefreshState.textContent = "ON";
                btnToggleAutoRefresh.classList.replace("btn-secondary", "btn-primary");
            }
        });

        // Set default to ON based on config interval
        setTimeout(() => {
            const seconds = activeConfig?.scan_interval_seconds || 300;
            autoRefreshInterval = setInterval(executeScan, seconds * 1000);
        }, 1000);
    }


    // ---------------------------------------------------------------------------
    // Signal History & Outcome Display
    // ---------------------------------------------------------------------------
    async function loadHistory() {
        if (!historySignalsTable) return;
        historySignalsTable.innerHTML = `<tr><td colspan="12" class="empty-placeholder"><i class="fa-solid fa-spinner fa-spin"></i> Loading historical logs...</td></tr>`;
        try {
            const offset = historyPage * historyLimit;
            const resp = await fetch(`${API_BASE}/api/signal-history?limit=${historyLimit}&offset=${offset}`);
            if (!resp.ok) throw new Error("Failed to load history.");
            const data = await resp.json();
            const list = data.history || [];
            
            historySignalsTable.innerHTML = "";
            if (list.length === 0) {
                historySignalsTable.innerHTML = `<tr><td colspan="12" class="empty-placeholder">No signals logged for page ${historyPage + 1}.</td></tr>`;
                return;
            }
            
            list.forEach(r => {
                const tr = document.createElement("tr");
                const ts = r.timestamp || "";
                
                const sl = r.stop_loss !== null && r.stop_loss !== undefined ? r.stop_loss.toFixed(2) : "—";
                const tgt = r.target !== null && r.target !== undefined ? r.target.toFixed(2) : "—";
                const rr = r.risk_reward !== null && r.risk_reward !== undefined ? r.risk_reward.toFixed(1) : "—";
                
                const score = r.setup_score !== undefined ? r.setup_score : 0;
                let scoreClass = "score-low";
                if (score >= 70) scoreClass = "score-high";
                else if (score >= 40) scoreClass = "score-medium";
                
                const reasonsList = r.score_reasons || [];
                const reasonsHtml = reasonsList.length > 0
                    ? `<div class="score-tooltip">
                        <strong>Score Breakdown:</strong>
                        <ul>${reasonsList.map(item => `<li>${item}</li>`).join("")}</ul>
                       </div>`
                    : "";

                let outcomeHtml = "—";
                let pnlClass = "";
                if (r.outcome_checked) {
                    const pnlVal = r.outcome_pnl_pct !== null ? r.outcome_pnl_pct.toFixed(2) + "%" : "0.00%";
                    if (r.outcome_hit_target) {
                        outcomeHtml = `<span class="badge badge-success" style="background-color: var(--success); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;">TARGET HIT</span>`;
                        pnlClass = "text-buy";
                    } else if (r.outcome_hit_stop) {
                        outcomeHtml = `<span class="badge badge-danger" style="background-color: var(--danger); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;">STOP LOSS</span>`;
                        pnlClass = "text-sell";
                    } else {
                        outcomeHtml = `<span class="badge" style="background: #6c757d; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;">COMPLETED</span>`;
                        pnlClass = r.outcome_pnl_pct >= 0 ? "text-buy" : "text-sell";
                    }
                    outcomeHtml = `<div style="display: flex; flex-direction: column; gap: 2px; align-items: center;">
                        ${outcomeHtml}
                        <strong class="${pnlClass}">${pnlVal}</strong>
                    </div>`;
                } else {
                    outcomeHtml = `<span class="badge" style="background: #ffc107; color: #212529; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;">PENDING</span>`;
                }

                const adxVal = r.adx !== null && r.adx !== undefined ? r.adx.toFixed(1) : "—";
                const rsVal = r.rs_ratio !== null && r.rs_ratio !== undefined ? r.rs_ratio.toFixed(2) : "—";
                const mtfVal = r.mtf_trend ? r.mtf_trend.toUpperCase() : "—";
                let mtfClass = mtfVal === "BULLISH" ? "text-buy" : mtfVal === "BEARISH" ? "text-sell" : "";

                tr.innerHTML = `
                    <td><small>${ts}</small></td>
                    <td><strong>${r.symbol}</strong></td>
                    <td><span class="condition-badge" style="background: #2a3b5c; color: white;">${r.timeframe || "N/A"}</span></td>
                    <td><span class="condition-badge ${r.signal_type === "BUY" ? "ut-badge-type" : "sr-badge-type"}">${r.signal_type}</span></td>
                    <td>${r.close_price.toFixed(2)}</td>
                    <td>${sl}</td>
                    <td>${tgt}</td>
                    <td><strong>${rr}</strong></td>
                    <td>
                        <div class="score-container">
                            <span class="score-badge ${scoreClass}">${score.toFixed(1)}</span>
                            ${reasonsHtml}
                        </div>
                    </td>
                    <td><small>ADX: ${adxVal}<br>RS: ${rsVal}</small></td>
                    <td><strong class="${mtfClass}">${mtfVal}</strong></td>
                    <td>${outcomeHtml}</td>
                `;
                historySignalsTable.appendChild(tr);
            });
            historyPageInfo.textContent = `Page ${historyPage + 1}`;
        } catch (err) {
            historySignalsTable.innerHTML = `<tr><td colspan="12" class="empty-placeholder text-sell">Error loading history: ${err.message}</td></tr>`;
        }
    }

    if (btnPrevPage) {
        btnPrevPage.addEventListener("click", () => {
            if (historyPage > 0) {
                historyPage--;
                loadHistory();
            }
        });
    }
    if (btnNextPage) {
        btnNextPage.addEventListener("click", () => {
            historyPage++;
            loadHistory();
        });
    }
    if (btnRefreshHistory) {
        btnRefreshHistory.addEventListener("click", loadHistory);
    }

    // ---------------------------------------------------------------------------
    // Performance Statistics Display
    // ---------------------------------------------------------------------------
    async function loadStats() {
        if (!statsTotalSignals) return;
        try {
            const resp = await fetch(`${API_BASE}/api/statistics?days=30`);
            if (!resp.ok) throw new Error("Failed to load statistics.");
            const data = await resp.json();
            const stats = data.statistics || {};

            statsTotalSignals.textContent = stats.total_signals !== undefined ? stats.total_signals : "-";
            statsCheckedSignals.textContent = `Checked: ${stats.checked_signals !== undefined ? stats.checked_signals : "-"}`;
            statsAvgRrWinners.textContent = stats.avg_rr_winners !== undefined ? stats.avg_rr_winners.toFixed(2) : "-";
            statsAvgRrLosers.textContent = stats.avg_rr_losers !== undefined ? stats.avg_rr_losers.toFixed(2) : "-";

            // Populate setup score tiers
            statsScoreTiersBody.innerHTML = "";
            const scoreTiers = stats.by_score_tier || {};
            const tierNames = ["70-100", "40-69", "0-39"];
            
            let hasScoreData = false;
            tierNames.forEach(tier => {
                if (scoreTiers[tier]) {
                    hasScoreData = true;
                    const val = scoreTiers[tier];
                    const tr = document.createElement("tr");
                    tr.innerHTML = `
                        <td><strong>Score ${tier}</strong></td>
                        <td>${val.total}</td>
                        <td>${val.wins}</td>
                        <td><strong class="text-buy">${val.win_rate}%</strong></td>
                    `;
                    statsScoreTiersBody.appendChild(tr);
                }
            });
            if (!hasScoreData) {
                statsScoreTiersBody.innerHTML = `<tr><td colspan="4" class="empty-placeholder">No checked signals found to compile performance.</td></tr>`;
            }

            // Populate directions & timeframes
            statsDirectionsTimeframesBody.innerHTML = "";
            const byType = stats.by_signal_type || {};
            const byTf = stats.by_timeframe || {};
            let hasDirData = false;

            Object.keys(byType).forEach(type => {
                hasDirData = true;
                const val = byType[type];
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><span class="condition-badge ${type === "BUY" ? "ut-badge-type" : "sr-badge-type"}">${type} Signals</span></td>
                    <td>${val.total}</td>
                    <td>${val.wins}</td>
                    <td><strong class="text-buy">${val.win_rate}%</strong></td>
                `;
                statsDirectionsTimeframesBody.appendChild(tr);
            });

            Object.keys(byTf).forEach(tf => {
                hasDirData = true;
                const val = byTf[tf];
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td><strong>Timeframe ${tf}</strong></td>
                    <td>${val.total}</td>
                    <td>${val.wins}</td>
                    <td><strong class="text-buy">${val.win_rate}%</strong></td>
                `;
                statsDirectionsTimeframesBody.appendChild(tr);
            });

            if (!hasDirData) {
                statsDirectionsTimeframesBody.innerHTML = `<tr><td colspan="4" class="empty-placeholder">No checked signals found.</td></tr>`;
            }

        } catch (err) {
            console.error("Error loading stats:", err);
        }
    }

    // ---------------------------------------------------------------------------
    // Filterable Lists
    // ---------------------------------------------------------------------------
    function makeFilterable(inputEl, tableBody) {
        inputEl.addEventListener("input", () => {
            const query = inputEl.value.toLowerCase().trim();
            const rows = tableBody.querySelectorAll("tr");
            
            rows.forEach(row => {
                if (row.querySelector(".empty-placeholder")) return;
                const sym = row.querySelector("td").textContent.toLowerCase();
                if (sym.includes(query)) {
                    row.style.display = "";
                } else {
                    row.style.display = "none";
                }
            });
        });
    }

    makeFilterable(buySearch, buySignalsTable);
    makeFilterable(sellSearch, sellSignalsTable);

    // Initialize System
    async function init() {
        await loadConfig();
        initAutoRefresh();
        // Trigger first scan immediately to show active triggers
        executeScan();
    }

    init();
});
