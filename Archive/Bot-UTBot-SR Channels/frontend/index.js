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
    const statLastScanTime = document.getElementById("stat-last-scan-time");

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
                
                const mtfFilter = !!cfg.filters.mtf_filter_enabled;
                const mtfFilterEl = document.getElementById("cfg-filters-mtf-filter");
                if (mtfFilterEl) mtfFilterEl.checked = mtfFilter;
                document.getElementById("cfg-filters-mtf-tf").value = cfg.filters.mtf_timeframe || "15m";
                document.getElementById("cfg-filters-mtf-neutral").value = cfg.filters.mtf_neutral_pct !== undefined ? cfg.filters.mtf_neutral_pct : 0.3;
                document.getElementById("cfg-filters-mtf-atr-period").value = cfg.filters.mtf_atr_period !== undefined ? cfg.filters.mtf_atr_period : 10;

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

            // OpenAlgo Order Settings
            if (cfg.openalgo) {
                const cfgMode = cfg.openalgo.order_mode || "manual";
                document.getElementById("cfg-oa-order-mode").value = cfgMode;
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

            // Sync Active Engines Sidebar Badges
            const sideUt = document.getElementById("side-badge-ut");
            const sideSr = document.getElementById("side-badge-sr");
            if (sideUt) sideUt.style.display = cfg.strategy.ut_enabled ? "inline-block" : "none";
            if (sideSr) sideSr.style.display = cfg.sr_channels.enabled ? "inline-block" : "none";

            // Sync Dashboard Sidebar Toggles
            document.getElementById("dash-ut-enabled").checked = !!cfg.strategy.ut_enabled;
            document.getElementById("dash-sr-enabled").checked = !!cfg.sr_channels.enabled;
            if (cfg.filters) {
                document.getElementById("dash-filters-ema").checked = !!cfg.filters.ema_filter_enabled;
                const emaDesc = document.getElementById("dash-desc-ema");
                if (emaDesc) emaDesc.textContent = `Filter signals by major EMA trend (${cfg.filters.ema_period || 200})`;

                document.getElementById("dash-filters-volume").checked = !!cfg.filters.volume_filter_enabled;
                
                const mtfFilterDash = document.getElementById("dash-filters-mtf-filter");
                if (mtfFilterDash) mtfFilterDash.checked = !!cfg.filters.mtf_filter_enabled;
                const mtfDesc = document.getElementById("dash-desc-mtf");
                if (mtfDesc) mtfDesc.textContent = `Filter signals by HTF trend (${cfg.filters.mtf_timeframe || '15m'})`;
                document.getElementById("dash-filters-adx-filt").checked = !!cfg.filters.adx_filter_enabled;
                document.getElementById("dash-filters-rsi-filter").checked = !!cfg.filters.rsi_filter_enabled;
                
                const sqzDash = document.getElementById("dash-filters-squeeze");
                if (sqzDash) sqzDash.checked = !!cfg.filters.squeeze_filter_enabled;
                document.getElementById("dash-filters-rr-enabled").checked = !!cfg.filters.risk_reward_enabled;
                document.getElementById("dash-filters-candle").checked = !!cfg.filters.candle_patterns_enabled;
            }

            // Sync dashboard default auto-refresh checkbox
            if (cfg.bot) {
                document.getElementById("cfg-bot-auto-refresh").checked = !!cfg.bot.auto_refresh_enabled;
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
                mtf_filter_enabled: document.getElementById("cfg-filters-mtf-filter").checked,
                mtf_timeframe: document.getElementById("cfg-filters-mtf-tf").value,
                mtf_neutral_pct: parseFloat(document.getElementById("cfg-filters-mtf-neutral").value || 0.3),
                mtf_atr_period: parseInt(document.getElementById("cfg-filters-mtf-atr-period").value || 10),
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
                enabled: document.getElementById("cfg-tg-enabled").checked,
                mode: document.getElementById("cfg-tg-mode").value,
                bot_token: document.getElementById("cfg-tg-token").value,
                chat_id: document.getElementById("cfg-tg-chat").value
            },
            openalgo: {
                ...activeConfig.openalgo,
                order_mode: document.getElementById("cfg-oa-order-mode").value,
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
        { id: "dash-filters-ema", configPath: ["filters", "ema_filter_enabled"], companionId: "cfg-filters-ema" },
        { id: "dash-filters-volume", configPath: ["filters", "volume_filter_enabled"], companionId: "cfg-filters-volume" },
        { id: "dash-filters-mtf-filter", configPath: ["filters", "mtf_filter_enabled"], companionId: "cfg-filters-mtf-filter" },
        { id: "dash-filters-adx-filt", configPath: ["filters", "adx_filter_enabled"], companionId: "cfg-filters-adx-filt" },
        { id: "dash-filters-rsi-filter", configPath: ["filters", "rsi_filter_enabled"], companionId: "cfg-filters-rsi-filter" },
        { id: "dash-filters-squeeze", configPath: ["filters", "squeeze_filter_enabled"], companionId: null },
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

    // ---------------------------------------------------------------------------
    // Order Placement
    // ---------------------------------------------------------------------------

    /** Apply order mode state to the toggle buttons and module variable. */
    function _applyOrderMode(mode) {
        orderMode = mode;
        [btnModeManual, btnModeAuto].forEach(btn => {
            btn.classList.toggle("active", btn.dataset.mode === mode);
        });
    }

    // Header toggle buttons
    if (btnModeManual) {
        btnModeManual.addEventListener("click", () => {
            _applyOrderMode("manual");
            // Persist to config immediately without full form submit
            if (activeConfig?.openalgo) {
                activeConfig.openalgo.order_mode = "manual";
                document.getElementById("cfg-oa-order-mode").value = "manual";
            }
        });
    }
    if (btnModeAuto) {
        btnModeAuto.addEventListener("click", () => {
            if (!confirm("Enable Auto mode? Orders will be placed automatically for every new scan signal without manual confirmation.")) return;
            _applyOrderMode("auto");
            if (activeConfig?.openalgo) {
                activeConfig.openalgo.order_mode = "auto";
                document.getElementById("cfg-oa-order-mode").value = "auto";
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
        const oa = activeConfig?.openalgo || {};
        const product  = oa.order_product  || "MIS";
        const quantity = qtyOverride ?? oa.order_quantity ?? 2;
        const exchange = activeConfig?.exchange || "NSE";
        const price_type = oa.order_type || "MARKET";
        const price = (price_type === "LIMIT" && priceOverride) ? priceOverride : 0.0;

        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i>`;
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
            showToast(`✅ ${action} ${symbol} — Order ${orderId}`, "success");
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
            if (score >= 85) { scoreClass = "score-premium"; scoreTier = "A+"; }
            else if (score >= 70) { scoreClass = "score-high"; scoreTier = "A"; }
            else if (score >= 50) { scoreClass = "score-medium"; scoreTier = "B"; }

            const reasonsList = item.score_reasons || [];
            const reasonsHtml = reasonsList.length > 0
                ? `<div class="score-tooltip">
                    <strong>Score Breakdown:</strong>
                    <ul>${reasonsList.map(r => `<li>${r}</li>`).join("")}</ul>
                   </div>`
                : "";

            // Confluence Matrix — each icon reflects threshold pass/fail, not scoring.
            // Backend sends explicit _ok fields; text heuristics are fallbacks for
            // any cached results that predate these fields.
            const reasonsText = reasonsList.join(" ");

            // Trend icon: active when price is on the correct side of the EMA
            // (above for BUY, below for SELL) OR when MTF confirms the direction.
            const mtfConfirms = reasonsText.includes("MTF confirms");
            const emaOk = item.ema_above !== null && item.ema_above !== undefined
                ? (type === "BUY" ? item.ema_above === true : item.ema_above === false)
                : reasonsText.includes("EMA") || reasonsText.includes("MTF");
            const hasTrend = emaOk || mtfConfirms;

            // RSI icon: active when RSI is within the configured optimal range
            const hasMomentum = item.rsi_ok !== null && item.rsi_ok !== undefined
                ? item.rsi_ok
                : reasonsText.includes("RSI");

            // Vol icon: active when volume >= configured threshold %
            const hasVolume = item.vol_ok !== undefined ? item.vol_ok : reasonsText.includes("volume");

            // S/R icon: active when price is inside or near a zone (score-text driven, always accurate)
            const hasSR = reasonsText.includes("Support") || reasonsText.includes("Resistance") || reasonsText.includes("S/R");

            // Squeeze icon: active when a squeeze release occurred on this bar
            const hasSqueeze = item.sqz_ok !== null && item.sqz_ok !== undefined
                ? item.sqz_ok
                : reasonsText.includes("Squeeze");
            
            const activeColor = type === "BUY" ? "var(--success)" : "var(--danger)";
            const inactiveColor = "#444";
            
            const dot = (isActive, label, icon) => 
                `<div style="display:flex; flex-direction:column; align-items:center; gap:2px;" title="${label}">
                    <i class="fa-solid ${icon}" style="color: ${isActive ? activeColor : inactiveColor}; font-size: 14px;"></i>
                    <span style="font-size: 9px; color: ${isActive ? '#ccc' : '#666'}">${label.substring(0,3)}</span>
                 </div>`;

            const trendIcon = type === "BUY" ? "fa-arrow-trend-up" : "fa-arrow-trend-down";

            const confluenceMatrixHtml = `
                <div style="display:flex; gap: 8px; justify-content: center;">
                    ${dot(hasTrend, 'Trend', trendIcon)}
                    ${dot(hasMomentum, 'RSI', 'fa-bolt')}
                    ${dot(hasVolume, 'Vol', 'fa-chart-simple')}
                    ${dot(hasSR, 'S/R', 'fa-bars')}
                    ${dot(hasSqueeze, 'Sqz', 'fa-compress')}
                </div>
            `;
            
            const winRateStr = item.hist_win_rate !== undefined && item.hist_win_rate !== null ? `${item.hist_win_rate}%` : "—";

            const actionClass = type === "BUY" ? "btn-order-buy" : "btn-order-sell";
            tr.innerHTML = `
                <td><strong>${item.symbol}</strong></td>
                <td>${item.close.toFixed(2)}</td>
                <td>${sl}</td>
                <td>${target}</td>
                <td><span class="rr-val" style="font-weight: 600; color: ${item.risk_reward >= 2.0 ? 'var(--success)' : 'inherit'}">${rr}</span></td>
                <td><span class="win-rate-val">${winRateStr}</span></td>
                <td>
                    <div class="score-container">
                        <div style="display:flex; flex-direction:column; align-items:center;">
                            <span class="score-badge ${scoreClass}" style="font-size: 0.85rem; padding: 2px 8px; min-width: 32px;">${scoreTier}</span>
                            <span style="font-size: 0.7rem; color: #888; margin-top: 3px;">${score.toFixed(1)}</span>
                        </div>
                        ${reasonsHtml}
                    </div>
                </td>
                <td>
                    ${confluenceMatrixHtml}
                </td>
                <td>
                    <div class="action-cell">
                        <button class="btn btn-secondary btn-analyze" data-symbol="${item.symbol}">
                            <i class="fa-solid fa-chart-line"></i> Chart
                        </button>
                        <div class="order-qty-wrap">
                            <span class="order-qty-label">Qty</span>
                            <input type="number" class="order-qty-input" value="${activeConfig?.openalgo?.order_quantity ?? 2}" min="1" step="1" title="Number of shares to buy/sell">
                        </div>
                        <button class="btn btn-place-order ${actionClass}" data-symbol="${item.symbol}" data-action="${type}" data-price="${item.close}">
                            <i class="fa-solid fa-cart-shopping"></i> ${type === "BUY" ? "Buy" : "Sell"}
                        </button>
                    </div>
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

        // Auto mode: fire orders for all new signals without any button click
        if (orderMode === "auto") {
            const allSignals = [
                ...(data.buy_signals || []).map(s => ({ ...s, action: "BUY" })),
                ...(data.sell_signals || []).map(s => ({ ...s, action: "SELL" })),
            ];
            allSignals.forEach(sig => {
                const oa = activeConfig?.openalgo || {};
                placeOrder(sig.symbol, sig.action, null, oa.order_quantity ?? 2, sig.close);
            });
        }

        // Update stats
        const total = (data.buy_signals?.length || 0) + (data.sell_signals?.length || 0);
        statBuyCount.textContent = data.buy_signals?.length || 0;
        statSellCount.textContent = data.sell_signals?.length || 0;
        // Show only the time portion of the timestamp (e.g. "20:59:15")
        const rawTs = data.timestamp || "";
        const timeOnly = rawTs.includes(" ") ? rawTs.split(" ")[1] : (rawTs || new Date().toLocaleTimeString());
        statLastScanTime.textContent = timeOnly;

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

    // Config Tab MTF Alignment visual rule is no longer needed since it has been simplified to a single Hard Confirmation filter.

    // Initialize System
    async function init() {
        await loadConfig();
        initAutoRefresh();
        // Trigger first scan immediately to show active triggers
        executeScan();

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

    init();
});
