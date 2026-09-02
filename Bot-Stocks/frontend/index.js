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
    let lastScanTimestamp = null;  // epoch ms of the last completed scan
    let orderMode = "manual";      // "manual" | "auto" — synced with config & toggle
    let autoOrderSessionCount = 0; // Track total auto-orders placed this session
    // Suppresses the header TF dropdown change listener when values are set
    // programmatically (e.g. during loadConfig) to prevent duplicate scans.
    let _suppressTfChange = false;
    let positionsRefreshInterval = null;

    // Elements cache
    const tabButtons = document.querySelectorAll(".nav-item");
    const tabPanels = document.querySelectorAll(".tab-panel");
    const btnRunScan = document.getElementById("btn-run-scan");
    const btnToggleAutoRefresh = document.getElementById("btn-toggle-auto-refresh");
    const autoRefreshState = document.getElementById("auto-refresh-state");
    const connectionStatus = document.getElementById("connection-status");
    const activeScanInfo = document.getElementById("active-scan-info");
    const btnModeManual = document.getElementById("btn-mode-manual");
    const btnModeAuto = document.getElementById("btn-mode-auto");
    const btnSidebarToggle = document.getElementById("btn-sidebar-toggle");
    const sidebar = document.querySelector(".sidebar");

    // Stats
    const statScannedCount = document.getElementById("stat-scanned-count");
    const statScannedDetails = document.getElementById("stat-scanned-details");
    const statBuyCount = document.getElementById("stat-buy-count");
    const statSellCount = document.getElementById("stat-sell-count");
    // Sprint 2.5: regime + risk status card (replaces Last Scan Cycle)
    const statRegimeValue = document.getElementById("stat-regime-value");
    const statRegimeSub   = document.getElementById("stat-regime-sub");
    const statRegimeCard  = document.getElementById("stat-regime-card");

    // Tables & Search
    const buySignalsTable = document.getElementById("buy-signals-table").querySelector("tbody");
    const sellSignalsTable = document.getElementById("sell-signals-table").querySelector("tbody");
    const buySearch = document.getElementById("buy-search");
    const sellSearch = document.getElementById("sell-search");

    // Stale-data badge elements
    const buyLastUpdated  = document.getElementById("buy-last-updated");
    const sellLastUpdated = document.getElementById("sell-last-updated");

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
    // Helper: Attach engine master toggle event listener
    // ---------------------------------------------------------------------------
    function attachEngineToggleListener(engine, engineCard, toggleId, hasComponents) {
        const engineCheckbox = engineCard.querySelector(`#${toggleId}`);
        engineCheckbox.addEventListener("change", async () => {
            try {
                const updatePayload = {};
                updatePayload[engine.config_section] = {};
                updatePayload[engine.config_section][engine.enabled_key] = engineCheckbox.checked;
                
                const updateResp = await fetch(`${API_BASE}/api/config`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(updatePayload)
                });
                
                if (!updateResp.ok) throw new Error("Failed to update engine state");
                
                showNotification(`${engine.label} ${engineCheckbox.checked ? 'enabled' : 'disabled'}`, "success");
                
                // Auto-expand when enabled, auto-collapse when disabled
                if (hasComponents) {
                    if (engineCheckbox.checked) {
                        engineCard.classList.add("expanded");
                    } else {
                        engineCard.classList.remove("expanded");
                    }
                }
                
                await runScan();
            } catch (error) {
                console.error(`Error toggling ${engine.label}:`, error);
                showNotification(`Failed to toggle ${engine.label}`, "error");
                engineCheckbox.checked = !engineCheckbox.checked;
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Helper: Attach component toggle event listener
    // ---------------------------------------------------------------------------
    function attachComponentToggleListener(engine, comp, compItem, compToggleId) {
        const compCheckbox = compItem.querySelector(`#${compToggleId}`);
        compCheckbox.addEventListener("change", async () => {
            try {
                const updatePayload = {};
                updatePayload[engine.config_section] = {};
                updatePayload[engine.config_section][comp.config_key] = compCheckbox.checked;
                
                const updateResp = await fetch(`${API_BASE}/api/config`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(updatePayload)
                });
                
                if (!updateResp.ok) throw new Error("Failed to update component state");
                
                showNotification(`${comp.label} ${compCheckbox.checked ? 'enabled' : 'disabled'}`, "success");
                await runScan();
            } catch (error) {
                console.error(`Error toggling ${comp.label}:`, error);
                showNotification(`Failed to toggle ${comp.label}`, "error");
                compCheckbox.checked = !compCheckbox.checked;
            }
        });
    }

    // ---------------------------------------------------------------------------
    // Helper: Create components section for engine
    // ---------------------------------------------------------------------------
    function createEngineComponents(engine, engineCard) {
        const componentsContainer = document.createElement("div");
        componentsContainer.className = "engine-components";
        
        engine.components.forEach(comp => {
            const compToggleId = `dash-comp-${engine.key}-${comp.key}`;
            
            const compItem = document.createElement("div");
            compItem.className = "component-toggle-item";
            compItem.setAttribute("data-component-toggle", `${engine.key}.${comp.key}`);
            
            compItem.innerHTML = `
                <div class="component-toggle-info">
                    <span class="component-toggle-name">${comp.label}</span>
                    <span class="component-toggle-desc">${comp.display_label}</span>
                </div>
                <label class="switch" onclick="event.stopPropagation();">
                    <input type="checkbox" id="${compToggleId}" ${comp.enabled ? 'checked' : ''}>
                    <span class="slider round"></span>
                </label>
            `;
            
            componentsContainer.appendChild(compItem);
            attachComponentToggleListener(engine, comp, compItem, compToggleId);
        });
        
        engineCard.appendChild(componentsContainer);
        
        // Add expand/collapse click handler to header
        const header = engineCard.querySelector(".engine-toggle-header");
        header.addEventListener("click", (e) => {
            if (e.target.closest(".switch")) return;
            engineCard.classList.toggle("expanded");
        });
    }

    // ---------------------------------------------------------------------------
    // Helper: Create expandable engine toggle card with components
    // ---------------------------------------------------------------------------
    function createEngineToggle(engine, container) {
        const hasComponents = engine.components && engine.components.length > 0;
        
        // Create engine card container
        const engineCard = document.createElement("div");
        engineCard.className = "engine-toggle-card";
        engineCard.setAttribute("data-engine-toggle", engine.key);
        
        const toggleId = `dash-engine-${engine.key}`;
        
        // Build header HTML
        let headerHTML = `
            <div class="engine-toggle-header">
                <div class="engine-toggle-left">
        `;
        
        // Add expand icon only if engine has components
        if (hasComponents) {
            headerHTML += `<i class="fa-solid fa-chevron-right engine-expand-icon"></i>`;
        }
        
        headerHTML += `
                    <div class="engine-toggle-info">
                        <span class="engine-toggle-name">${engine.label}</span>
                        <span class="engine-toggle-desc">Enable ${engine.label} signal engine</span>
                    </div>
                </div>
                <label class="switch" onclick="event.stopPropagation();">
                    <input type="checkbox" id="${toggleId}" ${engine.enabled ? 'checked' : ''}>
                    <span class="slider round"></span>
                </label>
            </div>
        `;
        
        engineCard.innerHTML = headerHTML;
        
        // Add components section if engine has them
        if (hasComponents) {
            createEngineComponents(engine, engineCard);
        }
        
        // Insert at the beginning of the container (before filter toggles)
        const firstChild = container.firstChild;
        container.insertBefore(engineCard, firstChild);
        
        // Always start collapsed - users can manually expand by clicking header
        // (Removed auto-expand logic to keep UI clean by default)
        
        // Attach engine master toggle listener
        attachEngineToggleListener(engine, engineCard, toggleId, hasComponents);
    }

    // ---------------------------------------------------------------------------
    // Dynamic Engine Registry (Sprint 4) - Expandable/Collapsible Engines
    // ---------------------------------------------------------------------------
    async function loadEngineToggles() {
        try {
            const resp = await fetch(`${API_BASE}/api/engines`);
            if (!resp.ok) throw new Error("Failed to fetch engines");
            const data = await resp.json();
            const engines = data.engines || [];

            // Target container for engine toggles
            const container = document.querySelector(".control-toggles-list");
            if (!container) {
                console.warn("Engine toggles container not found");
                return;
            }

            // Clear existing hardcoded engine toggles (UT Bot, S/R)
            const existingEngineToggles = container.querySelectorAll("[data-engine-toggle]");
            existingEngineToggles.forEach(el => el.remove());

            // Dynamically create toggle for each engine
            engines.forEach((engine, idx) => {
                createEngineToggle(engine, container);
            });

            console.log(`✅ Dynamically loaded ${engines.length} engine toggles`);
        } catch (error) {
            console.error("Failed to load engine toggles:", error);
            showNotification("Failed to load engine controls", "error");
        }
    }

    // ---------------------------------------------------------------------------
    // Sidebar Collapse Logic
    // ---------------------------------------------------------------------------
    if (btnSidebarToggle && sidebar) {
        // Read saved sidebar state from localStorage on load
        const isCollapsed = localStorage.getItem("sidebar-collapsed") === "true";
        if (isCollapsed) {
            sidebar.classList.add("collapsed");
        }

        btnSidebarToggle.addEventListener("click", () => {
            sidebar.classList.toggle("collapsed");
            const collapsed = sidebar.classList.contains("collapsed");
            localStorage.setItem("sidebar-collapsed", collapsed ? "true" : "false");
        });
    }

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
            } else if (targetTab === "positions") {
                loadPositions();
                loadClosedPositions();
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
            const activeTf = cfg.candle_timeframe || cfg.scan_timeframe || "5m";
            activeScanInfo.textContent = `Profile: ${cfg.data_source.toUpperCase()} | TF: ${activeTf} | Segments: ${seg}`;

            // Set header dropdown values — suppress the change event so loadConfig
            // does not accidentally trigger a second scan when updating values programmatically.
            _suppressTfChange = true;
            const headerSelectLtf = document.getElementById("header-select-ltf");
            const headerSelectHtf = document.getElementById("header-select-htf");
            if (headerSelectLtf) headerSelectLtf.value = activeTf;
            if (headerSelectHtf) headerSelectHtf.value = cfg.filters.mtf_timeframe;
            _suppressTfChange = false;
            
            // Populate Config Form
            document.getElementById("cfg-data-source").value = cfg.data_source;
            document.getElementById("cfg-exchange").value = cfg.exchange;
            document.getElementById("cfg-timeframe").value = activeTf;
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
                document.getElementById("cfg-filters-score").value = cfg.filters.min_alert_score !== undefined ? cfg.filters.min_alert_score : 70;
                
                const mtfFilter = !!cfg.filters.mtf_filter_enabled;
                const mtfFilterEl = document.getElementById("cfg-filters-mtf-filter");
                if (mtfFilterEl) mtfFilterEl.checked = mtfFilter;
                document.getElementById("cfg-filters-mtf-tf").value = cfg.filters.mtf_timeframe || "15m";
                document.getElementById("cfg-filters-mtf-neutral").value = cfg.filters.mtf_neutral_pct !== undefined ? cfg.filters.mtf_neutral_pct : 0.3;
                document.getElementById("cfg-filters-mtf-atr-period").value = cfg.filters.mtf_atr_period !== undefined ? cfg.filters.mtf_atr_period : 10;

                document.getElementById("cfg-filters-rs-enabled").checked = !!cfg.filters.rs_enabled;
                document.getElementById("cfg-filters-rs-index").value = cfg.filters.rs_index || "NIFTY50";
                document.getElementById("cfg-filters-rs-period").value = cfg.filters.rs_period !== undefined ? cfg.filters.rs_period : 20;
                document.getElementById("cfg-filters-rs-buy").value = cfg.filters.rs_buy_threshold !== undefined ? cfg.filters.rs_buy_threshold : 1.05;
                document.getElementById("cfg-filters-rs-sell").value = cfg.filters.rs_sell_threshold !== undefined ? cfg.filters.rs_sell_threshold : 0.95;

                document.getElementById("cfg-filters-rr-enabled").checked = !!cfg.filters.risk_reward_enabled;
                document.getElementById("cfg-filters-rr-mult").value = cfg.filters.rr_atr_multiplier !== undefined ? cfg.filters.rr_atr_multiplier : 0.5;
                document.getElementById("cfg-filters-rr-ratio").value = cfg.filters.rr_default_ratio !== undefined ? cfg.filters.rr_default_ratio : 2.0;
                document.getElementById("cfg-filters-candle").checked = !!cfg.filters.candle_patterns_enabled;
                document.getElementById("cfg-filters-history").checked = !!cfg.filters.signal_history_enabled;
                document.getElementById("cfg-filters-history-hours").value = cfg.filters.outcome_check_hours !== undefined ? cfg.filters.outcome_check_hours : 4;
            }

            // OpenAlgo Order Settings
            if (cfg.openalgo) {
                const cfgMode = cfg.openalgo.order_mode || "manual";
                document.getElementById("cfg-oa-order-mode").value = cfgMode;
                const selectActions = document.getElementById("cfg-oa-allowed-actions");
                if (selectActions) selectActions.value = cfg.openalgo.allowed_actions || "BUY_ONLY";
                document.getElementById("cfg-oa-product").value = cfg.openalgo.order_product || "MIS";
                document.getElementById("cfg-oa-quantity").value = cfg.openalgo.order_quantity || 1;
                // Sync the dashboard header toggle with saved config
                _applyOrderMode(cfgMode);
            }

            // Alerts
            const tgEnabled = cfg.telegram.enabled !== undefined ? cfg.telegram.enabled : true;
            document.getElementById("cfg-tg-enabled").checked = !!tgEnabled;
            document.getElementById("cfg-tg-mode").value = cfg.telegram.mode;
            document.getElementById("cfg-tg-token").value = cfg.telegram.bot_token;
            document.getElementById("cfg-tg-chat").value = cfg.telegram.chat_id;

            // Segments & Custom List
            document.getElementById("cfg-segments").value = Array.isArray(cfg.segment) ? cfg.segment.join(", ") : cfg.segment;
            document.getElementById("cfg-use-symbols").checked = cfg.use_symbols;

            // Sync Dashboard Sidebar Toggles
            document.getElementById("dash-ut-enabled").checked = !!cfg.strategy.ut_enabled;
            document.getElementById("dash-sr-enabled").checked = !!cfg.sr_channels.enabled;
            if (cfg.filters) {
                const mtfFilterDash = document.getElementById("dash-filters-mtf-filter");
                if (mtfFilterDash) mtfFilterDash.checked = !!cfg.filters.mtf_filter_enabled;
                const mtfDesc = document.getElementById("dash-desc-mtf");
                if (mtfDesc) mtfDesc.textContent = `Filter signals by HTF trend (${cfg.filters.mtf_timeframe || '15m'})`;

                const rsFilterDash = document.getElementById("dash-filters-rs-enabled");
                if (rsFilterDash) {
                    rsFilterDash.checked = !!cfg.filters.rs_enabled;
                    console.log(`[Dashboard] RS Filter toggle synced: ${rsFilterDash.checked ? 'ON' : 'OFF'} (rs_enabled: ${cfg.filters.rs_enabled})`);
                }
                const rsDesc = document.getElementById("dash-desc-rs");
                if (rsDesc) {
                    const indexName = cfg.filters.rs_index || 'NIFTY50';
                    rsDesc.textContent = `Trade stocks outperforming ${indexName}`;
                }

                document.getElementById("dash-filters-rr-enabled").checked = !!cfg.filters.risk_reward_enabled;
                document.getElementById("dash-filters-candle").checked = !!cfg.filters.candle_patterns_enabled;
            }

            // Sync dashboard default auto-refresh checkbox
            if (cfg.bot) {
                document.getElementById("cfg-bot-auto-refresh").checked = !!cfg.bot.auto_refresh_enabled;
            }

            // Sync header scan-interval dropdown
            const headerIntervalSel = document.getElementById("header-scan-interval");
            if (headerIntervalSel && cfg.scan_interval_seconds) {
                const secs = String(cfg.scan_interval_seconds);
                const opts = Array.from(headerIntervalSel.options).map(o => parseInt(o.value));
                let best = opts[0];
                for (const v of opts) { if (v <= parseInt(secs)) best = v; }
                headerIntervalSel.value = String(best);
            }
            
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
            candle_timeframe: document.getElementById("cfg-timeframe").value,
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
                min_alert_score: parseInt(document.getElementById("cfg-filters-score").value || 70),
                candle_patterns_enabled: document.getElementById("cfg-filters-candle").checked,
                mtf_filter_enabled: document.getElementById("cfg-filters-mtf-filter").checked,
                mtf_timeframe: document.getElementById("cfg-filters-mtf-tf").value,
                mtf_neutral_pct: parseFloat(document.getElementById("cfg-filters-mtf-neutral").value || 0.3),
                mtf_atr_period: parseInt(document.getElementById("cfg-filters-mtf-atr-period").value || 10),
                rs_enabled: document.getElementById("cfg-filters-rs-enabled").checked,
                rs_index: document.getElementById("cfg-filters-rs-index").value,
                rs_period: parseInt(document.getElementById("cfg-filters-rs-period").value || 20),
                rs_buy_threshold: parseFloat(document.getElementById("cfg-filters-rs-buy").value || 1.05),
                rs_sell_threshold: parseFloat(document.getElementById("cfg-filters-rs-sell").value || 0.95),
                risk_reward_enabled: document.getElementById("cfg-filters-rr-enabled").checked,
                rr_atr_multiplier: parseFloat(document.getElementById("cfg-filters-rr-mult").value || 0.5),
                rr_default_ratio: parseFloat(document.getElementById("cfg-filters-rr-ratio").value || 2.0),
                signal_history_enabled: document.getElementById("cfg-filters-history").checked,
                outcome_check_hours: parseInt(document.getElementById("cfg-filters-history-hours").value || 4),
                win_rate_backtest_enabled: false
            },
            telegram: {
                enabled: document.getElementById("cfg-tg-enabled").checked,
                mode: document.getElementById("cfg-tg-mode").value,
                bot_token: document.getElementById("cfg-tg-token").value,
                chat_id: document.getElementById("cfg-tg-chat").value
            },
            openalgo: {
                ...activeConfig.openalgo,
                order_mode: document.getElementById("cfg-oa-order-mode").value,
                allowed_actions: document.getElementById("cfg-oa-allowed-actions")?.value || "BUY_ONLY",
                order_product: document.getElementById("cfg-oa-product").value,
                order_quantity: parseInt(document.getElementById("cfg-oa-quantity").value || 1),
            },
            data: activeConfig.data,
            bot: {
                log_level: activeConfig.bot.log_level,
                market_hours_check: activeConfig.bot.market_hours_check,
                market_open: activeConfig.bot.market_open,
                market_close: activeConfig.bot.market_close,
                auto_refresh_enabled: document.getElementById("cfg-bot-auto-refresh").checked
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
            showToast("✅ Configuration saved successfully!", "success");
            await loadConfig();
            // Re-initialise auto-refresh with the new interval from saved config
            // (only if auto-refresh is currently running)
            if (autoRefreshInterval !== null) {
                _startAutoRefresh();
            }
        } catch (err) {
            showToast(`❌ Error saving config: ${err.message}`, "error");
        }
    });

    btnResetConfig.addEventListener("click", () => {
        if (confirm("Reset form values to last saved state?")) {
            loadConfig();
        }
    });

     // ---------------------------------------------------------------------------
     // Sprint 3: Grading Control Handlers
     // ---------------------------------------------------------------------------
     const btnApplyGrading = document.getElementById("btn-apply-grading-settings");
     const cfgGradeMultiplier = document.getElementById("cfg-grade-multiplier");
     const cfgMinGrade = document.getElementById("cfg-min-grade");
     const gradingStatusText = document.getElementById("grading-status-text");

     // Load grading state from /api/risk/status when config tab is opened
     async function loadGradingState() {
         try {
             const resp = await fetch(`${API_BASE}/api/risk/status`);
             if (!resp.ok) throw new Error("Failed to load grading state.");
             const status = await resp.json();
             
             // Update controls
             if (cfgGradeMultiplier) {
                 cfgGradeMultiplier.checked = status.grade_multiplier_enabled === true;
             }
             if (cfgMinGrade) {
                 cfgMinGrade.value = status.min_grade_to_trade || "D";
             }
             
             // Update status text
             if (gradingStatusText) {
                 const enabled = status.grading_enabled === true ? "✓ Enabled" : "✗ Disabled";
                 const mult = status.grade_multiplier_enabled === true
                     ? "A×1.5, B×1.25, C×1.0, D×0.75"
                     : "Uniform risk (×1.0)";
                 gradingStatusText.innerHTML = `<strong>${enabled}</strong> · Min Grade: <strong>${status.min_grade_to_trade}</strong> · Multiplier: ${mult}`;
             }
         } catch (err) {
             console.error("Failed to load grading state:", err);
             if (gradingStatusText) gradingStatusText.textContent = "Error loading status";
         }
     }

     // Apply grading settings via POST /api/config/grading
     if (btnApplyGrading) {
         btnApplyGrading.addEventListener("click", async () => {
             try {
                 const enabled = cfgGradeMultiplier ? cfgGradeMultiplier.checked : false;
                 const minGrade = cfgMinGrade ? cfgMinGrade.value : "D";
                 
                 const params = new URLSearchParams();
                 params.append("grade_multiplier_enabled", enabled);
                 params.append("min_grade_to_trade", minGrade);
                 
                 const resp = await fetch(`${API_BASE}/api/config/grading`, {
                     method: "POST",
                     body: params,
                     headers: { "Content-Type": "application/x-www-form-urlencoded" }
                 });
                 
                 if (!resp.ok) throw new Error("API request failed");
                 const result = await resp.json();
                 
                 if (result.status === "success") {
                     alert("✓ Grading settings applied! Changes take effect on next scan.");
                     loadGradingState();  // Refresh UI
                 } else {
                     alert(`✗ Error: ${result.message}`);
                 }
             } catch (err) {
                 alert(`Failed to apply settings: ${err.message}`);
             }
         });
     }

     // Load grading state on initial load
     setTimeout(loadGradingState, 500);


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
    // Dashboard Quick Filter Controls
    // ---------------------------------------------------------------------------
    const dashToggles = [
        { id: "dash-ut-enabled", configPath: ["strategy", "ut_enabled"], companionId: "cfg-ut-enabled" },
        { id: "dash-sr-enabled", configPath: ["sr_channels", "enabled"], companionId: "cfg-sr-enabled" },
        { id: "dash-filters-mtf-filter", configPath: ["filters", "mtf_filter_enabled"], companionId: "cfg-filters-mtf-filter" },
        { id: "dash-filters-rs-enabled", configPath: ["filters", "rs_enabled"], companionId: "cfg-filters-rs-enabled" },
        { id: "dash-filters-rr-enabled", configPath: ["filters", "risk_reward_enabled"], companionId: "cfg-filters-rr-enabled" },
        { id: "dash-filters-candle", configPath: ["filters", "candle_patterns_enabled"], companionId: "cfg-filters-candle" }
    ];

    dashToggles.forEach(toggleInfo => {
        const toggleEl = document.getElementById(toggleInfo.id);
        if (toggleEl) {
            toggleEl.addEventListener("change", async () => {
                if (!activeConfig) return;
                
                const isChecked = toggleEl.checked;
                
                // Update local config structure
                const parentKey = toggleInfo.configPath[0];
                const childKey = toggleInfo.configPath[1];
                if (activeConfig[parentKey]) {
                    activeConfig[parentKey][childKey] = isChecked;
                }
                
                // Keep the Settings tab form checkbox in sync
                const companionEl = document.getElementById(toggleInfo.companionId);
                if (companionEl) {
                    companionEl.checked = isChecked;
                }
                
                // Disable all toggles temporarily while saving and scanning
                setTogglesDisabledState(true);
                
                try {
                    const resp = await fetch(`${API_BASE}/api/config`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(activeConfig)
                    });
                    if (!resp.ok) throw new Error("Failed to save configuration.");
                    
                    const name = toggleEl.closest(".control-toggle-item")?.querySelector(".toggle-name")?.textContent || "Filter";
                    showToast(`✅ ${name} ${isChecked ? 'enabled' : 'disabled'}!`, "success");
                    
                    // Reload active configuration
                    await loadConfig();
                    
                    // Trigger a scan immediately with the updated filters
                    await executeScan();
                } catch (err) {
                    showToast(`❌ Error: ${err.message}`, "error");
                    // Revert UI state on error
                    toggleEl.checked = !isChecked;
                    if (companionEl) companionEl.checked = !isChecked;
                } finally {
                    setTogglesDisabledState(false);
                }
            });
        }
    });

    function setTogglesDisabledState(disabled) {
        dashToggles.forEach(toggleInfo => {
            const el = document.getElementById(toggleInfo.id);
            if (el) el.disabled = disabled;
        });
    }

    // ---------------------------------------------------------------------------
    // Index Live Status Update
    // ---------------------------------------------------------------------------
    async function updateIndexStatus() {
        try {
            const resp = await fetch(`${API_BASE}/api/index-status`);
            if (!resp.ok) throw new Error("Failed to fetch index status.");
            const res = await resp.json();
            if (res.status === "success" && res.data) {
                const data = res.data;
                const mapping = {
                    "NIFTY 50": { val: "index-val-nifty", chg: "index-chg-nifty" },
                    "BANKNIFTY": { val: "index-val-banknifty", chg: "index-chg-banknifty" },
                    "NIFTY IT": { val: "index-val-niftyit", chg: "index-chg-niftyit" }
                };
                for (const [key, ids] of Object.entries(mapping)) {
                    const item = data[key];
                    const valEl = document.getElementById(ids.val);
                    const chgEl = document.getElementById(ids.chg);
                    if (item && valEl && chgEl) {
                        valEl.textContent = item.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                        const sign = item.change >= 0 ? "+" : "";
                        chgEl.textContent = `${sign}${item.change.toFixed(2)} (${sign}${item.pct.toFixed(2)}%)`;
                        if (item.change >= 0) {
                            chgEl.style.color = "var(--color-buy)";
                        } else {
                            chgEl.style.color = "var(--color-sell)";
                        }
                    }
                }
            }
        } catch (err) {
            console.error("Index status fetch error:", err);
        }
    }

    // ---------------------------------------------------------------------------
    // Running Scans
    // ---------------------------------------------------------------------------
    async function executeScan() {
        if (isScanning) return;
        isScanning = true;
        btnRunScan.disabled = true;
        btnRunScan.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Scanning...`;

        // Show pulsing "Refreshing…" state on table badges while scan runs
        [buyLastUpdated, sellLastUpdated].forEach(el => {
            if (!el) return;
            el.textContent = "⟳ Refreshing…";
            el.className = "last-updated-badge badge-scanning";
        });

        try {
            updateIndexStatus();
            const resp = await fetch(`${API_BASE}/api/scan`, { method: "POST" });
            if (!resp.ok) throw new Error("Trigger scan failed on backend server.");
            const data = await resp.json();
            renderScanData(data);
        } catch (err) {
            // On error clear the scanning state from badges
            [buyLastUpdated, sellLastUpdated].forEach(el => {
                if (!el) return;
                el.textContent = "Error";
                el.className = "last-updated-badge badge-stale";
            });
            alert(`❌ Scan Execution Error: ${err.message}`);
        } finally {
            isScanning = false;
            btnRunScan.disabled = false;
            btnRunScan.innerHTML = `<i class="fa-solid fa-play"></i> Run Scanner`;
            loadLogs(); // Refresh logs to capture scan output
        }
    }

    btnRunScan.addEventListener("click", executeScan);

    // Sync header timeframe dropdowns to config, save on backend, and re-scan
    async function handleHeaderTimeframeChange() {
        // Bail out if the value was set programmatically (e.g. by loadConfig) —
        // only act on genuine user-driven selection changes.
        if (_suppressTfChange) return;
        if (!activeConfig) return;
        const ltfSelect = document.getElementById("header-select-ltf");
        const htfSelect = document.getElementById("header-select-htf");
        if (!ltfSelect || !htfSelect) return;
        
        const ltf = ltfSelect.value;
        const htf = htfSelect.value;
        
        // Update local activeConfig state
        activeConfig.candle_timeframe = ltf;
        activeConfig.scan_timeframe = ltf;
        activeConfig.filters.mtf_timeframe = htf;
        
        // Update corresponding form elements in Config tab
        const formLtf = document.getElementById("cfg-timeframe");
        const formHtf = document.getElementById("cfg-filters-mtf-tf");
        if (formLtf) formLtf.value = ltf;
        if (formHtf) formHtf.value = htf;
        
        try {
            const resp = await fetch(`${API_BASE}/api/config`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(activeConfig)
            });
            if (!resp.ok) throw new Error("Failed to save timeframe config changes.");
            showToast("Timeframe saved and updated!", "success");
            await loadConfig();
            
            // Re-initialize auto-refresh if currently active
            if (autoRefreshInterval !== null) {
                _startAutoRefresh();
            }
            
            // Trigger scanner execution immediately
            executeScan();
        } catch (err) {
            showToast(`❌ Error saving timeframe: ${err.message}`, "error");
        }
    }

    const headerLtf = document.getElementById("header-select-ltf");
    const headerHtf = document.getElementById("header-select-htf");
    if (headerLtf) headerLtf.addEventListener("change", handleHeaderTimeframeChange);
    if (headerHtf) headerHtf.addEventListener("change", handleHeaderTimeframeChange);

    // ---------------------------------------------------------------------------
    // Order Placement
    // ---------------------------------------------------------------------------

    /** Apply order mode state to the toggle buttons and module variable. */
    function _applyOrderMode(mode) {
        orderMode = mode;
        [btnModeManual, btnModeAuto].forEach(btn => {
            btn.classList.toggle("active", btn.dataset.mode === mode);
        });
        // Update badge when mode changes
        updateAutoOrderBadge();
    }

    /** Update the auto-order badge in the dashboard header */
    function updateAutoOrderBadge() {
        const badge = document.getElementById("auto-order-badge");
        if (!badge) return;

        if (orderMode === "auto") {
            badge.style.display = "inline-block";
            if (autoOrderSessionCount > 0) {
                badge.textContent = `${autoOrderSessionCount} placed`;
                badge.className = "auto-badge auto-badge-active";
            } else {
                badge.textContent = "Waiting...";
                badge.className = "auto-badge auto-badge-waiting";
            }
        } else {
            badge.style.display = "none";
        }
    }

    // Header toggle buttons
    if (btnModeManual) {
        btnModeManual.addEventListener("click", async () => {
            _applyOrderMode("manual");
            try {
                const resp = await fetch(`${API_BASE}/api/config`);
                const cfg = await resp.json();
                cfg.openalgo = cfg.openalgo || {};
                cfg.openalgo.order_mode = "manual";
                activeConfig = cfg;
                const selectEl = document.getElementById("cfg-oa-order-mode");
                if (selectEl) selectEl.value = "manual";
                await fetch(`${API_BASE}/api/config`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(cfg)
                });
            } catch (e) {
                console.error("Failed to persist order_mode:", e);
            }
        });
    }
    if (btnModeAuto) {
        btnModeAuto.addEventListener("click", async () => {
            if (!confirm("Enable Auto mode? Orders will be placed automatically for every new scan signal without manual confirmation.")) return;
            _applyOrderMode("auto");
            try {
                const resp = await fetch(`${API_BASE}/api/config`);
                const cfg = await resp.json();
                cfg.openalgo = cfg.openalgo || {};
                cfg.openalgo.order_mode = "auto";
                activeConfig = cfg;
                const selectEl = document.getElementById("cfg-oa-order-mode");
                if (selectEl) selectEl.value = "auto";
                await fetch(`${API_BASE}/api/config`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(cfg)
                });
            } catch (e) {
                console.error("Failed to persist order_mode:", e);
            }
        });
    }

    /**
     * Place a single order via the backend /api/order endpoint.
     * @param {string} symbol 
     * @param {string} action "BUY" or "SELL"
     * @param {HTMLButtonElement|null} btnEl  Optional button element to show feedback on.
     * @param {number|null} qtyOverride  Quantity from the per-row input (overrides config default).
     * @param {number|null} priceOverride  The limit price to use if order_type is LIMIT.
     */
    async function placeOrder(symbol, action, btnEl = null, qtyOverride = null, priceOverride = null) {
        // Resolve order settings from the active trading_api_source broker block.
        // Falls back to the openalgo block for backward compatibility.
        const source     = (activeConfig?.trading_api_source || "openalgo").toLowerCase();
        const brokerCfg  = activeConfig?.[source] || activeConfig?.openalgo || {};
        const product    = brokerCfg.order_product || "MIS";
        const quantity   = qtyOverride ?? brokerCfg.order_quantity ?? 1;
        const exchange   = activeConfig?.exchange || "NSE";
        const price_type = brokerCfg.order_type || "MARKET";

        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i>`;
        }

        // For LIMIT orders fetch the live LTP from OpenAlgo at click time instead
        // of using the stale candle close price from when the scan ran.
        let price = 0.0;
        if (price_type === "LIMIT") {
            try {
                const ltpResp = await fetch(`${API_BASE}/api/ltp/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`);
                const ltpData = await ltpResp.json();
                if (!ltpResp.ok) throw new Error(ltpData.detail || "LTP fetch failed");
                price = ltpData.ltp;
                console.info(`[LTP] ${symbol} live price: ₹${price} (will place LIMIT @ ₹${price})`);
            } catch (ltpErr) {
                // LTP fetch failed — fall back to the signal's close price with a warning
                price = priceOverride || 0.0;
                console.warn(`[LTP] Live price fetch failed for ${symbol}: ${ltpErr.message}. Falling back to signal close price ₹${price}.`);
                showToast(`⚠️ ${symbol} — Using signal price ₹${price} (live LTP unavailable)`, "warning");
            }
        }

        try {
            const resp = await fetch(`${API_BASE}/api/order`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ symbol, action, exchange, product, quantity, price_type, price })
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.detail || "Order failed");

            const orderId = result.order?.orderid || result.order?.order_id || "placed";
            const priceNote = price_type === "LIMIT" ? ` @ ₹${price}` : "";
            showToast(`✅ ${action} ${symbol}${priceNote} — Order ${orderId}`, "success");
            if (btnEl) {
                const origLabel = btnEl.dataset.action === "BUY" ? "Buy" : "Sell";
                btnEl.innerHTML = `<i class="fa-solid fa-check"></i> Done`;
                btnEl.classList.add("btn-order-done");
                setTimeout(() => {
                    btnEl.innerHTML = `<i class="fa-solid fa-cart-shopping"></i> ${origLabel}`;
                    btnEl.classList.remove("btn-order-done");
                    btnEl.disabled = false;
                }, 4000);
            }
        } catch (err) {
            showToast(`❌ Order failed: ${symbol} — ${err.message}`, "error");
            if (btnEl) {
                btnEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Retry`;
                btnEl.disabled = false;
            }
        }
    }

    /** Toast notification helper */
    function showToast(message, type = "info") {
        let container = document.getElementById("toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            document.body.appendChild(container);
        }
        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        // Trigger animation
        requestAnimationFrame(() => toast.classList.add("toast-visible"));
        setTimeout(() => {
            toast.classList.remove("toast-visible");
            setTimeout(() => toast.remove(), 400);
        }, 4000);
    }

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
            let scoreTier = "C";
            if (score >= 85)      { scoreClass = "score-premium"; scoreTier = "A+"; }
            else if (score >= 70) { scoreClass = "score-high";    scoreTier = "A";  }
            else if (score >= 50) { scoreClass = "score-medium";  scoreTier = "B";  }
            else if (score >= 30) { scoreClass = "score-low";     scoreTier = "C";  }
            else                  { scoreClass = "score-weak";     scoreTier = "D";  }

            const reasonsList = item.score_reasons || [];
            const reasonsHtml = reasonsList.length > 0
                ? `<div class="score-tooltip">
                    <strong>Score Breakdown:</strong>
                    <ul>${reasonsList.map(r => `<li>${r}</li>`).join("")}</ul>
                   </div>`
                : "";

            const winRateStr = item.hist_win_rate !== undefined && item.hist_win_rate !== null ? `${item.hist_win_rate}%` : "—";

            const actionClass = type === "BUY" ? "btn-order-buy" : "btn-order-sell";

            // Sprint 2.5: engine tag + regime-gate + sizing badges rendered
            // inline with the symbol so no new columns are needed. Fields are
            // optional — pre-Sprint-2 rows simply render without them.
            let sprintTagsHtml = "";
            const eng = item.engine;
            if (eng) {
                const engMap = { utbot: "UT", sr: "SR", "utbot+sr": "UT+SR" };
                const engLabel = engMap[String(eng).toLowerCase()] || eng;
                sprintTagsHtml += `<span class="engine-tag" style="display:inline-block; margin-left:6px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:700; background:var(--surface-2,#1e2130); color:var(--text-secondary,#94a3b8); border:1px solid var(--border,#2d3748); vertical-align:middle;" title="Signal engine">${engLabel}</span>`;
            }
            // Sprint 3: grade badge — A green, B blue, C amber, D red. Tooltip
            // carries the composite score; the full per-factor breakdown lives
            // in item.grade_breakdown if you want to extend the tooltip later.
            const gr = item.grade;
            if (gr) {
                const gradeColours = {
                    A: { bg: "rgba(16,185,129,0.15)",  fg: "var(--success,#10b981)", bd: "rgba(16,185,129,0.35)" },
                    B: { bg: "rgba(59,130,246,0.15)",  fg: "var(--accent-blue,#3b82f6)", bd: "rgba(59,130,246,0.35)" },
                    C: { bg: "rgba(245,158,11,0.15)",  fg: "var(--warning,#f59e0b)", bd: "rgba(245,158,11,0.35)" },
                    D: { bg: "rgba(239,68,68,0.15)",   fg: "var(--danger,#ef4444)",  bd: "rgba(239,68,68,0.35)" },
                };
                const c = gradeColours[String(gr).toUpperCase()] || gradeColours.C;
                const gScore = item.grade_score !== undefined && item.grade_score !== null
                    ? ` · score ${Number(item.grade_score).toFixed(0)}` : "";
                sprintTagsHtml += `<span style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:800; background:${c.bg}; color:${c.fg}; border:1px solid ${c.bd}; vertical-align:middle;" title="Signal grade ${String(gr).toUpperCase()}${gScore} (market-context quality — see signal_grading config)">${String(gr).toUpperCase()}</span>`;
            }
            if (item.regime_gate_ok === false) {
                const gr2 = item.regime_gate_reason || "gate blocked";
                sprintTagsHtml += `<span style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:700; background:rgba(239,68,68,0.15); color:var(--danger,#ef4444); border:1px solid rgba(239,68,68,0.35); vertical-align:middle;" title="${gr2.replace(/"/g,"&quot;")}">GATE</span>`;
            }
            if (item.grade_gate_ok === false) {
                const gg = item.grade_gate_reason || "grade gate blocked";
                sprintTagsHtml += `<span style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:700; background:rgba(239,68,68,0.15); color:var(--danger,#ef4444); border:1px solid rgba(239,68,68,0.35); vertical-align:middle;" title="${gg.replace(/"/g,"&quot;")}">GRADE</span>`;
            }
            if (item.exposure_gate_ok === false) {
                const ex = item.exposure_gate_reason || "exposure cap hit";
                sprintTagsHtml += `<span style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:700; background:rgba(239,68,68,0.15); color:var(--danger,#ef4444); border:1px solid rgba(239,68,68,0.35); vertical-align:middle;" title="${ex.replace(/"/g,"&quot;")}">EXPOSURE</span>`;
            }
            const ps = item.position_sizing;
            let psTooltip = "";
            if (ps && typeof ps === "object" && ps.quantity !== undefined) {
                // NOTE: the scanner emits ``quantity`` (not ``qty``) in the
                // sizing dict — this was the reason the badge never rendered.
                const psMode  = ps.mode || "";
                const psQty   = ps.quantity;
                const psRisk  = ps.risk_amount !== undefined ? `₹${Number(ps.risk_amount).toFixed(0)}` : "?";
                const psMult  = ps.grade_multiplier !== undefined && ps.grade_multiplier !== 1.0
                    ? ` · grade ×${ps.grade_multiplier}` : "";
                psTooltip = `Sizing: ${psMode} · qty ${psQty} · risk ${psRisk}${psMult}`;
                sprintTagsHtml += `<span style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:700; background:rgba(16,185,129,0.15); color:var(--success,#10b981); border:1px solid rgba(16,185,129,0.35); vertical-align:middle;" title="${psTooltip.replace(/"/g,'&quot;')}">Qty ${psQty}</span>`;
            }

            // AI / LLM Recommendation Tag
            if (item.ai_recommendation && item.ai_recommendation !== "N/A") {
                const aiRec = String(item.ai_recommendation).toUpperCase();
                const isBull = aiRec.includes("BUY");
                const isAvoid = aiRec.includes("AVOID") || aiRec.includes("SELL");
                
                let aiBg = "rgba(16,185,129,0.15)";
                let aiFg = "var(--success,#10b981)";
                let aiBd = "rgba(16,185,129,0.35)";
                
                if (isAvoid) {
                    aiBg = "rgba(239,68,68,0.15)";
                    aiFg = "var(--danger,#ef4444)";
                    aiBd = "rgba(239,68,68,0.35)";
                } else if (!isBull) {
                    aiBg = "rgba(245,158,11,0.15)";
                    aiFg = "var(--warning,#f59e0b)";
                    aiBd = "rgba(245,158,11,0.35)";
                }
                
                const badgeText = item.ai_badge || `🤖 AI: ${aiRec}`;
                const scoreStr = item.ai_score !== undefined && item.ai_score !== null ? ` (${Math.round(item.ai_score)}/100)` : "";
                const reasonStr = item.ai_reasoning ? ` — ${item.ai_reasoning}` : "";
                const titleText = `AI Recommendation: ${aiRec}${scoreStr}${reasonStr}`;
                
                sprintTagsHtml += `<span class="ai-recommendation-tag" style="display:inline-block; margin-left:4px; padding:1px 6px; border-radius:4px; font-size:0.62rem; font-weight:800; background:${aiBg}; color:${aiFg}; border:1px solid ${aiBd}; vertical-align:middle; cursor:help;" title="${titleText.replace(/"/g, '&quot;')}">${badgeText}</span>`;
            }

            tr.innerHTML = `
                <td><strong>${item.symbol}</strong>${sprintTagsHtml}</td>
                <td>${item.close.toFixed(2)}</td>
                <td><span class="win-rate-val">${winRateStr}</span></td>
                <td>
                    <div class="score-container">
                        <div style="display: grid; grid-template-columns: auto auto; grid-template-rows: auto auto; justify-content: center; align-items: center; column-gap: 10px; row-gap: 3px;">
                            <span class="score-badge ${scoreClass}" style="grid-column: 1; grid-row: 1; font-size: 0.85rem; padding: 2px 8px; min-width: 32px;">${scoreTier}</span>
                            <button class="btn-analyze" data-symbol="${item.symbol}" style="grid-column: 2; grid-row: 1; background: none; border: none; color: var(--color-accent); cursor: pointer; padding: 0; font-size: 0.95rem; font-weight: 800; -webkit-text-stroke: 0.5px currentColor; display: inline-flex; align-items: center;" title="View Chart">
                                <i class="fa-solid fa-chart-line" style="font-weight: 800;"></i>
                            </button>
                            <span style="grid-column: 1; grid-row: 2; font-size: 0.7rem; color: #888;">${score.toFixed(1)}</span>
                        </div>
                        ${reasonsHtml}
                    </div>
                </td>
                <td>
                    <div class="action-cell">
                        <div class="order-qty-wrap">
                            <span class="order-qty-label">Qty</span>
                            <input type="number" class="order-qty-input" value="${activeConfig?.openalgo?.order_quantity ?? 2}" min="1" step="1" title="Number of shares to buy/sell">
                        </div>
                        <button class="btn btn-place-order ${actionClass}" data-symbol="${item.symbol}" data-action="${type}" data-price="${item.close}">
                            <i class="fa-solid fa-cart-shopping"></i> ${type === "BUY" ? "Buy" : "Sell"}
                        </button>
                    </div>
                </td>
                <td>${sl}</td>
                <td>${target}</td>
                <td><span class="rr-val" style="font-weight: 600; color: ${item.risk_reward >= 2.0 ? 'var(--success)' : 'inherit'}">${rr}</span></td>
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

        // Setup Place Order buttons (Manual mode)
        document.querySelectorAll(".btn-place-order").forEach(btn => {
            btn.addEventListener("click", () => {
                const sym = btn.getAttribute("data-symbol");
                const action = btn.getAttribute("data-action");
                const priceStr = btn.getAttribute("data-price");
                const price = priceStr ? parseFloat(priceStr) : null;
                // Read quantity from the sibling input in the same action-cell
                const qtyInput = btn.closest(".action-cell")?.querySelector(".order-qty-wrap .order-qty-input");
                const qty = qtyInput ? (parseInt(qtyInput.value) || 1) : 1;
                if (confirm(`Place ${action} order for ${qty} × ${sym}?`)) {
                    placeOrder(sym, action, btn, qty, price);
                }
            });
        });

        // Auto mode: fire orders for new signals — respects allowed_actions setting
        if (orderMode === "auto") {
            const oa = activeConfig?.openalgo || {};
            const allowedActions = (oa.allowed_actions || "BOTH").toUpperCase();

            let autoSignals = [];
            if (allowedActions === "BUY_ONLY") {
                // Only place BUY signals — skip SELL signals entirely
                autoSignals = (data.buy_signals || []).map(s => ({ ...s, action: "BUY" }));
            } else if (allowedActions === "SELL_ONLY") {
                // Only place SELL signals — skip BUY signals entirely
                autoSignals = (data.sell_signals || []).map(s => ({ ...s, action: "SELL" }));
            } else {
                // BOTH — place all signals
                autoSignals = [
                    ...(data.buy_signals || []).map(s => ({ ...s, action: "BUY" })),
                    ...(data.sell_signals || []).map(s => ({ ...s, action: "SELL" })),
                ];
            }

            console.info(`[AUTO-MODE] Scan complete. Found ${autoSignals.length} eligible signal(s) for ${allowedActions} mode.`);

            if (autoSignals.length > 0) {
                autoSignals.forEach(sig => {
                    placeOrder(sig.symbol, sig.action, null, oa.order_quantity ?? 2, sig.close);
                    autoOrderSessionCount++;
                });
                showToast(`🤖 Auto-mode: Placed ${autoSignals.length} order(s) (Session: ${autoOrderSessionCount})`, "success");
            } else {
                showToast(`🤖 Auto-mode active — No ${allowedActions} signals to place this scan`, "info");
            }

            // Update auto-order counter badge in dashboard
            updateAutoOrderBadge();
        }

        // Update stats
        const total = (data.buy_signals?.length || 0) + (data.sell_signals?.length || 0);
        statBuyCount.textContent = data.buy_signals?.length || 0;
        statSellCount.textContent = data.sell_signals?.length || 0;

        // Sprint 2.5: populate the Market Regime card from top-level scan payload.
        // The regime is computed from NIFTY on every scan (see Sprint 1.5).
        // Gate state + today's P&L come from a separate /api/risk/status call
        // fired here (piggyback — no independent polling loop).
        if (statRegimeValue) {
            const regime = (data.current_regime || "unknown").replace(/_/g, " ");
            statRegimeValue.textContent = regime;
            // Colour-code: trending → green, chop → orange, high_vol → red, else neutral
            const r = (data.current_regime || "").toLowerCase();
            let colour = "var(--text-primary)";
            if (r.startsWith("trending"))   colour = "var(--success, #10b981)";
            else if (r === "chop")          colour = "var(--warning, #f59e0b)";
            else if (r.includes("high_vol"))colour = "var(--danger,  #ef4444)";
            statRegimeValue.style.color = colour;
        }
        // Fire-and-forget risk status refresh (never blocks scan render).
        fetch(`${API_BASE}/api/risk/status`)
            .then(r => r.ok ? r.json() : null)
            .then(rs => {
                if (!rs || !statRegimeSub) return;
                const gateTxt = rs.regime_gate_enabled ? "<b style='color:var(--success)'>ON</b>"
                                                       : "<b>off</b>";
                const pnl = Number(rs.realized_pnl_today_rupees ?? 0);
                const pnlColour = pnl > 0 ? "var(--success, #10b981)"
                                : pnl < 0 ? "var(--danger,  #ef4444)"
                                :           "var(--text-primary)";
                const pnlStr = (pnl >= 0 ? "+₹" : "−₹") + Math.abs(pnl).toLocaleString("en-IN", {maximumFractionDigits: 0});
                const modeTxt = rs.sizing_mode && rs.sizing_mode !== "legacy"
                    ? ` · <span title='Sizing mode'>${rs.sizing_mode}</span>`
                    : "";
                statRegimeSub.innerHTML = `Gate: ${gateTxt} · P&L today: <b style="color:${pnlColour}">${pnlStr}</b>${modeTxt}`;
            })
            .catch(() => { /* silent — dashboard degrades gracefully */ });

        // Use the exact symbol count from the API response.
        // Falls back to the sum of buy+sell signals for older API versions.
        const totalCountScanned = data.total_scanned ?? total;
        statScannedCount.textContent = totalCountScanned;
        statScannedDetails.textContent = `Segments: ${data.segment_label}`;

        // Stamp the "last updated" badges on the signal table headers
        lastScanTimestamp = Date.now();
        const timeStr = new Date().toLocaleTimeString();
        [buyLastUpdated, sellLastUpdated].forEach(el => {
            if (!el) return;
            el.textContent = `Updated: ${timeStr}`;
            el.className = "last-updated-badge badge-fresh";
        });
    }   // ← end renderScanData()

    // ---------------------------------------------------------------------------
    // Auto-Refresh Manager
    // ---------------------------------------------------------------------------

    /** Start (or restart) the auto-refresh interval using the current config. */
    function _startAutoRefresh() {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
        const seconds = activeConfig?.scan_interval_seconds || 300;
        autoRefreshInterval = setInterval(executeScan, seconds * 1000);
        autoRefreshState.textContent = "ON";
        btnToggleAutoRefresh.classList.replace("btn-secondary", "btn-primary");
    }

    function initAutoRefresh() {
        const intervalSelect = document.getElementById("header-scan-interval");

        // Sync dropdown to the value loaded from config
        function _syncIntervalDropdown() {
            if (!intervalSelect || !activeConfig) return;
            const seconds = String(activeConfig.scan_interval_seconds || 60);
            // Pick the closest option (exact match first, else nearest lower)
            const options = Array.from(intervalSelect.options).map(o => parseInt(o.value));
            let best = options[0];
            for (const v of options) {
                if (v <= parseInt(seconds)) best = v;
            }
            intervalSelect.value = String(best);
        }
        _syncIntervalDropdown();

        // When user changes the dropdown: apply immediately + persist to backend
        if (intervalSelect) {
            intervalSelect.addEventListener("change", async () => {
                const newSeconds = parseInt(intervalSelect.value);
                if (!activeConfig) return;
                activeConfig.scan_interval_seconds = newSeconds;
                // Restart auto-refresh immediately with new interval (if running)
                if (autoRefreshInterval !== null) {
                    _startAutoRefresh();
                }
                // Persist to config.yml
                try {
                    await fetch(`${API_BASE}/api/config`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(activeConfig)
                    });
                    const label = intervalSelect.options[intervalSelect.selectedIndex].text;
                    showToast(`✅ Auto-refresh interval set to ${label}`, "success");
                } catch (err) {
                    showToast(`❌ Failed to save interval: ${err.message}`, "error");
                }
            });
        }

        // Toggle action
        btnToggleAutoRefresh.addEventListener("click", () => {
            if (autoRefreshInterval) {
                // Disable
                clearInterval(autoRefreshInterval);
                autoRefreshInterval = null;
                autoRefreshState.textContent = "OFF";
                btnToggleAutoRefresh.classList.replace("btn-primary", "btn-secondary");
            } else {
                // Enable — reads fresh interval from activeConfig
                _startAutoRefresh();
            }
        });

        // Start on page load only if enabled in config
        if (activeConfig?.bot?.auto_refresh_enabled) {
            _startAutoRefresh();
        } else {
            autoRefreshState.textContent = "OFF";
            btnToggleAutoRefresh.classList.replace("btn-primary", "btn-secondary");
        }
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
                if (score >= 85)      scoreClass = "score-premium";
                else if (score >= 70) scoreClass = "score-high";
                else if (score >= 50) scoreClass = "score-medium";
                else if (score >= 30) scoreClass = "score-low";
                else                  scoreClass = "score-weak";
                
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

                let aiTagHtml = "";
                if (r.ai_recommendation && r.ai_recommendation !== "N/A") {
                    const aiRec = String(r.ai_recommendation).toUpperCase();
                    const isBull = aiRec.includes("BUY");
                    const isAvoid = aiRec.includes("AVOID") || aiRec.includes("SELL");
                    let aiBg = "rgba(16,185,129,0.15)", aiFg = "var(--success,#10b981)", aiBd = "rgba(16,185,129,0.35)";
                    if (isAvoid) {
                        aiBg = "rgba(239,68,68,0.15)"; aiFg = "var(--danger,#ef4444)"; aiBd = "rgba(239,68,68,0.35)";
                    } else if (!isBull) {
                        aiBg = "rgba(245,158,11,0.15)"; aiFg = "var(--warning,#f59e0b)"; aiBd = "rgba(245,158,11,0.35)";
                    }
                    const badgeText = r.ai_badge || `🤖 ${aiRec}`;
                    const scoreStr = r.ai_score !== undefined && r.ai_score !== null ? ` (${Math.round(r.ai_score)}/100)` : "";
                    const reasonStr = r.ai_reasoning ? ` — ${r.ai_reasoning}` : "";
                    const titleText = `AI Recommendation: ${aiRec}${scoreStr}${reasonStr}`;
                    aiTagHtml = `<br><span style="display:inline-block; margin-top:2px; padding:1px 5px; border-radius:4px; font-size:0.60rem; font-weight:800; background:${aiBg}; color:${aiFg}; border:1px solid ${aiBd}; cursor:help;" title="${titleText.replace(/"/g, '&quot;')}">${badgeText}</span>`;
                }

                tr.innerHTML = `
                    <td><small>${ts}</small></td>
                    <td><strong>${r.symbol}</strong>${aiTagHtml}</td>
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

    // Config Tab MTF Alignment visual rule is no longer needed since it has been simplified to a single Hard Confirmation filter.

    // ---------------------------------------------------------------------------
    // Positions & Trade Management Logic
    // ---------------------------------------------------------------------------
    async function loadPositions() {
        const tableBody = document.getElementById("positions-open-body");
        const summaryCard = document.getElementById("portfolio-summary");
        if (!tableBody) return;
        
        try {
            const resp = await fetch(`${API_BASE}/api/positions`);
            if (!resp.ok) throw new Error("Failed to load positions.");
            const data = await resp.json();
            const openPositions = data.positions || [];

            document.getElementById("positions-last-update-badge").textContent = `Updated: ${new Date().toLocaleTimeString()}`;

            if (openPositions.length === 0) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="13" class="empty-placeholder">No active positions currently registered for monitoring. Placed orders will show up here.</td>
                    </tr>
                `;
                // Hide summary card when no positions
                if (summaryCard) summaryCard.style.display = "none";
                return;
            }

            // Show and update portfolio summary
            if (summaryCard) summaryCard.style.display = "grid";
            
            let totalInvested = 0;
            let totalCurrentValue = 0;
            let html = "";
            
            openPositions.forEach(pos => {
                const ltp = pos.high_water_mark || pos.entry_price; // Current price (high_water_mark is live LTP)
                const entryPrice = pos.entry_price;
                const qty = pos.quantity;
                const isBuy = pos.direction === "BUY";
                const dirClass = isBuy ? "ut-badge-type" : "sr-badge-type";
                
                // Calculate financial metrics
                const invested = entryPrice * qty;
                const currentValue = ltp * qty;
                const pnlRupees = isBuy ? (currentValue - invested) : (invested - currentValue);
                const pnlPct = ((pnlRupees / invested) * 100);
                
                // Accumulate totals
                totalInvested += invested;
                totalCurrentValue += currentValue;
                
                // Live P&L color logic
                let pnlClass = "pnl-neutral";
                let formattedPnlPct = pnlPct.toFixed(2) + "%";
                let formattedPnlRs = "₹" + pnlRupees.toFixed(2);
                if (pnlPct > 0) {
                    pnlClass = "pnl-profit";
                    formattedPnlPct = "+" + formattedPnlPct;
                    formattedPnlRs = "+" + formattedPnlRs;
                } else if (pnlPct < 0) {
                    pnlClass = "pnl-loss";
                }

                // Status Badge logic
                let statusClass = "badge-status-monitoring";
                let statusLabel = "Monitoring";
                if (pos.trailing_active) {
                    statusClass = "badge-status-trailing";
                    statusLabel = `Trailing ${isBuy ? '↑' : '↓'}`;
                } else if (pos.profit_locked) {
                    statusClass = "badge-status-locked";
                    statusLabel = "Locked 🔒";
                }

                html += `
                    <tr>
                        <td><strong>${pos.symbol}</strong></td>
                        <td><span class="condition-badge ${dirClass}">${pos.direction}</span></td>
                        <td>${qty}</td>
                        <td>₹${entryPrice.toFixed(2)}</td>
                        <td class="live-ltp" style="font-weight: 600;">₹${ltp.toFixed(2)}</td>
                        <td style="color: rgba(255,255,255,0.8);">₹${invested.toFixed(2)}</td>
                        <td style="color: rgba(255,255,255,0.8);">₹${currentValue.toFixed(2)}</td>
                        <td><span class="pnl-badge ${pnlClass}">${formattedPnlRs}</span></td>
                        <td><span class="pnl-badge ${pnlClass}">${formattedPnlPct}</span></td>
                        <td>₹${pos.current_sl.toFixed(2)}</td>
                        <td>₹${pos.target_price.toFixed(2)}</td>
                        <td><span class="badge-status ${statusClass}">${statusLabel}</span></td>
                        <td>
                            <button class="btn btn-danger btn-close-pos" data-id="${pos.id}" style="padding: 3px 8px; font-size: 0.75rem;">
                                <i class="fa-solid fa-rectangle-xmark"></i> Exit
                            </button>
                        </td>
                    </tr>
                `;
            });
            tableBody.innerHTML = html;

            // Update portfolio summary
            const totalPnlRs = totalCurrentValue - totalInvested;
            const totalPnlPct = totalInvested > 0 ? ((totalPnlRs / totalInvested) * 100) : 0;
            const totalPnlClass = totalPnlRs >= 0 ? "var(--color-buy)" : "var(--color-sell)";
            const totalPnlSign = totalPnlRs >= 0 ? "+" : "";
            
            document.getElementById("summary-total-positions").textContent = openPositions.length;
            document.getElementById("summary-total-invested").textContent = `₹${totalInvested.toFixed(2)}`;
            document.getElementById("summary-current-value").textContent = `₹${totalCurrentValue.toFixed(2)}`;
            
            const pnlElement = document.getElementById("summary-total-pnl");
            pnlElement.textContent = `${totalPnlSign}₹${totalPnlRs.toFixed(2)} (${totalPnlSign}${totalPnlPct.toFixed(2)}%)`;
            pnlElement.style.color = totalPnlClass;

            // Bind exit buttons
            tableBody.querySelectorAll(".btn-close-pos").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const posId = btn.getAttribute("data-id");
                    if (confirm(`Are you sure you want to manually exit and close position ${posId}?`)) {
                        await closePosition(posId);
                    }
                });
            });

        } catch (err) {
            log.error("Error loading positions: %s", err);
        }
    }

    const btnCloseAllPos = document.getElementById("btn-close-all-positions");
    if (btnCloseAllPos) {
        btnCloseAllPos.addEventListener("click", async () => {
            if (!confirm("⚠️ Are you sure you want to CLOSE ALL active monitored positions immediately?")) return;
            btnCloseAllPos.disabled = true;
            btnCloseAllPos.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Closing All...';
            try {
                const resp = await fetch(`${API_BASE}/api/positions/close-all`, { method: "POST" });
                const data = await resp.json();
                if (!resp.ok) {
                    throw new Error(data.detail || data.message || "Failed to close all positions.");
                }
                alert(data.message || "Successfully closed positions.");
                await loadPositions();
                await loadClosedPositions();
            } catch (err) {
                alert("Failed to close all positions: " + err.message);
            } finally {
                btnCloseAllPos.disabled = false;
                btnCloseAllPos.innerHTML = '<i class="fa-solid fa-square-xmark"></i> Close All Positions';
            }
        });
    }

    // Sprint 4: Emergency Exit Button Handler
    const btnEmergencyExit = document.getElementById("btn-emergency-exit");
    if (btnEmergencyExit) {
        btnEmergencyExit.addEventListener("click", async () => {
            const confirmed = confirm(
                "🚨 EMERGENCY EXIT 🚨\n\n" +
                "This will IMMEDIATELY close ALL open positions at market price.\n\n" +
                "⚠️ This action:\n" +
                "• Bypasses normal position management\n" +
                "• Sends critical Telegram alerts\n" +
                "• Cannot be undone\n\n" +
                "Only use in PANIC situations!\n\n" +
                "Are you ABSOLUTELY SURE?"
            );
            
            if (!confirmed) return;
            
            // Double confirmation
            const doubleCheck = confirm("FINAL WARNING: Proceed with emergency exit?");
            if (!doubleCheck) return;
            
            btnEmergencyExit.disabled = true;
            btnEmergencyExit.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> EMERGENCY EXIT IN PROGRESS...';
            btnEmergencyExit.style.animation = "none";
            
            try {
                const resp = await fetch(`${API_BASE}/api/emergency-exit`, { method: "POST" });
                const data = await resp.json();
                
                if (!resp.ok) {
                    throw new Error(data.detail || data.message || "Emergency exit failed");
                }
                
                let message = `Emergency Exit Complete\n\n`;
                message += `Closed: ${data.closed}/${data.total} positions\n`;
                
                if (data.errors && data.errors.length > 0) {
                    message += `\n⚠️ Errors:\n${data.errors.slice(0, 3).join('\n')}`;
                    if (data.errors.length > 3) {
                        message += `\n... and ${data.errors.length - 3} more`;
                    }
                }
                
                alert(message);
                await loadPositions();
                await loadClosedPositions();
                
            } catch (err) {
                alert("🚨 EMERGENCY EXIT FAILED:\n\n" + err.message + "\n\nPlease check logs and close positions manually!");
            } finally {
                btnEmergencyExit.disabled = false;
                btnEmergencyExit.innerHTML = '🚨 EMERGENCY EXIT';
                btnEmergencyExit.style.animation = "pulse-red 2s infinite";
            }
        });
    }

    // Helper: shared reset-databases logic used by both buttons
    async function resetAllDatabases(btn) {
        if (!confirm("⚠️ Are you SURE you want to clear ALL trade database history, active positions, and signal history?\n\nThis will wipe all tracking logs to start completely fresh!")) return;
        const origHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Clearing DBs...';
        try {
            const resp = await fetch(`${API_BASE}/api/reset-data`, { method: "POST" });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || "Failed to reset databases.");
            alert(data.message || "All databases cleared successfully! Starting fresh.");
            await loadPositions();
            await loadClosedPositions();
            await loadHistory();
            await loadStats();
        } catch (err) {
            alert("❌ Clear Databases Error: " + err.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }

    // History tab: Clear All History & DBs button
    const btnResetDb = document.getElementById("btn-reset-databases");
    if (btnResetDb) {
        btnResetDb.addEventListener("click", () => resetAllDatabases(btnResetDb));
    }

    // Positions tab: Clear All History & DBs button (same action)
    const btnResetDbPos = document.getElementById("btn-reset-databases-pos");
    if (btnResetDbPos) {
        btnResetDbPos.addEventListener("click", () => resetAllDatabases(btnResetDbPos));
    }

    async function loadClosedPositions() {
        const tableBody = document.getElementById("positions-closed-body");
        if (!tableBody) return;

        try {
            const resp = await fetch(`${API_BASE}/api/positions/closed`);
            if (!resp.ok) throw new Error("Failed to load closed positions.");
            const data = await resp.json();
            const closedPositions = data.positions || [];

            if (closedPositions.length === 0) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="7" class="empty-placeholder">No closed trades history found.</td>
                    </tr>
                `;
                return;
            }

            let html = "";
            closedPositions.forEach(pos => {
                const isBuy = pos.direction === "BUY";
                const dirClass = isBuy ? "ut-badge-type" : "sr-badge-type";
                const pnl = pos.pnl_pct !== null ? pos.pnl_pct : 0;
                
                let pnlClass = "pnl-neutral";
                let formattedPnl = pnl.toFixed(2) + "%";
                if (pnl > 0) {
                    pnlClass = "pnl-profit";
                    formattedPnl = "+" + formattedPnl;
                } else if (pnl < 0) {
                    pnlClass = "pnl-loss";
                }

                html += `
                    <tr>
                        <td><strong>${pos.symbol}</strong></td>
                        <td><span class="condition-badge ${dirClass}">${pos.direction}</span></td>
                        <td>₹${pos.entry_price.toFixed(2)}</td>
                        <td>₹${(pos.close_price || 0).toFixed(2)}</td>
                        <td>${pos.close_time || "—"}</td>
                        <td><span class="badge-reason">${pos.close_reason || "—"}</span></td>
                        <td><span class="pnl-badge ${pnlClass}">${formattedPnl}</span></td>
                    </tr>
                `;
            });
            tableBody.innerHTML = html;
        } catch (err) {
            log.error("Error loading closed positions: %s", err);
        }
    }

    async function closePosition(posId) {
        try {
            const resp = await fetch(`${API_BASE}/api/positions/${posId}/close`, { method: "POST" });
            if (!resp.ok) throw new Error("Failed to close position.");
            showToast(`Manual exit order placed for position ${posId}!`, "success");
            loadPositions();
            loadClosedPositions();
        } catch (err) {
            showToast(`❌ Error exiting position: ${err.message}`, "error");
        }
    }

    const btnRefreshClosedPos = document.getElementById("btn-refresh-closed-positions");
    if (btnRefreshClosedPos) {
        btnRefreshClosedPos.addEventListener("click", () => {
            loadClosedPositions();
            showToast("Closed positions updated!", "success");
        });
    }

    // Initialize System
    async function init() {
        // Load dynamic engine toggles first (Sprint 4)
        await loadEngineToggles();
        await loadConfig();
        initAutoRefresh();
        // Trigger first scan immediately to show active triggers
        executeScan();

        // Separate interval refresh for positions (polled every 5 seconds)
        positionsRefreshInterval = setInterval(() => {
            const activeTab = document.querySelector(".nav-item.active");
            if (activeTab && activeTab.getAttribute("data-tab") === "positions") {
                loadPositions();
            }
        }, 5000);

        // Staleness ticker — checks every 30 s whether the last scan is overdue.
        // Turns the table badges amber when elapsed > 2× the configured interval.
        setInterval(() => {
            if (!lastScanTimestamp || isScanning) return;
            const elapsed = (Date.now() - lastScanTimestamp) / 1000;
            const threshold = 2 * (activeConfig?.scan_interval_seconds || 300);
            if (elapsed > threshold) {
                [buyLastUpdated, sellLastUpdated].forEach(el => {
                    if (el && !el.classList.contains("badge-stale")) {
                        el.classList.replace("badge-fresh", "badge-stale");
                    }
                });
            }
        }, 30_000);
    }

    // ---------------------------------------------------------------------------
    // Accordion Settings Logic
    // ---------------------------------------------------------------------------
    function initAccordionSettings() {
        const accordionSections = document.querySelectorAll('.accordion-section');
        
        accordionSections.forEach(section => {
            const header = section.querySelector('.accordion-header');
            if (!header) return;
            
            header.addEventListener('click', () => {
                // Toggle active class
                const wasActive = section.classList.contains('active');
                
                // Optional: Close other sections (comment out for multi-open mode)
                // accordionSections.forEach(s => s.classList.remove('active'));
                
                // Toggle this section
                if (wasActive) {
                    section.classList.remove('active');
                } else {
                    section.classList.add('active');
                }
            });
        });
        
        // Open first section by default
        if (accordionSections.length > 0) {
            accordionSections[0].classList.add('active');
        }
    }
    
    // Initialize accordion after DOM is ready
    setTimeout(initAccordionSettings, 100);

    init();
});
