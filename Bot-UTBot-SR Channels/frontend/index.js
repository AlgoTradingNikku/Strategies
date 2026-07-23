/* ==========================================================================
   Logical Controller & Charting — UTBot + SR Channels Dashboard
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // State management
    let activeConfig = null;
    let autoRefreshInterval = null;
    let currentChart = null;
    let candlestickSeries = null;
    let trailLineSeries = null;
    let activeSRLines = []; // Store references to draw S/R lines
    let activeMarkers = [];
    let isScanning = false;

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

    // Chart controls
    const chartSymbolSelect = document.getElementById("chart-symbol-select");
    const chartTitle = document.getElementById("chart-title");
    const chartTimeframeDisplay = document.getElementById("chart-timeframe-display");
    const chartTickerDetails = document.getElementById("chart-ticker-details");
    const toggleUtLine = document.getElementById("toggle-ut-line");
    const toggleSrZones = document.getElementById("toggle-sr-zones");
    const toggleMarkers = document.getElementById("toggle-markers");

    // Logs
    const btnClearTerminal = document.getElementById("btn-clear-terminal");
    const btnRefreshLogs = document.getElementById("btn-refresh-logs");
    const logsTerminal = document.getElementById("logs-terminal-output");

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
            
            // Adjust chart size if switching to charting tab
            if (targetTab === "charting" && currentChart) {
                setTimeout(() => {
                    const container = document.getElementById("chart-element-container");
                    currentChart.resize(container.clientWidth, container.clientHeight);
                }, 100);
            }
            
            // Auto refresh logs when loading log panel
            if (targetTab === "logs") {
                loadLogs();
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
            document.getElementById("cfg-signal-mode").value = cfg.signal_mode;
            document.getElementById("cfg-lookback").value = cfg.signal_lookback_candles;

            // Strategy
            document.getElementById("cfg-ut-key").value = cfg.strategy.key_value;
            document.getElementById("cfg-ut-atr").value = cfg.strategy.atr_period;
            document.getElementById("cfg-ut-ha").checked = cfg.strategy.use_heikin_ashi;

            // SR Channels
            document.getElementById("cfg-sr-pivot").value = cfg.sr_channels.pivot_period;
            document.getElementById("cfg-sr-source").value = cfg.sr_channels.source;
            document.getElementById("cfg-sr-width").value = cfg.sr_channels.channel_width_pct;
            document.getElementById("cfg-sr-strength").value = cfg.sr_channels.min_strength;
            document.getElementById("cfg-sr-max").value = cfg.sr_channels.max_num_sr;
            document.getElementById("cfg-sr-loopback").value = cfg.sr_channels.loopback;
            document.getElementById("cfg-sr-prox").value = cfg.sr_channels.proximity_pct;

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
            signal_mode: document.getElementById("cfg-signal-mode").value,
            signal_lookback_candles: parseInt(document.getElementById("cfg-lookback").value),
            strategy: {
                ut_enabled: activeConfig.strategy.ut_enabled,
                key_value: parseFloat(document.getElementById("cfg-ut-key").value),
                atr_period: parseInt(document.getElementById("cfg-ut-atr").value),
                use_heikin_ashi: document.getElementById("cfg-ut-ha").checked
            },
            sr_channels: {
                enabled: activeConfig.sr_channels.enabled,
                pivot_period: parseInt(document.getElementById("cfg-sr-pivot").value),
                source: document.getElementById("cfg-sr-source").value,
                channel_width_pct: parseFloat(document.getElementById("cfg-sr-width").value),
                min_strength: parseInt(document.getElementById("cfg-sr-strength").value),
                max_num_sr: parseInt(document.getElementById("cfg-sr-max").value),
                loopback: parseInt(document.getElementById("cfg-sr-loopback").value),
                proximity_pct: parseFloat(document.getElementById("cfg-sr-prox").value)
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
        
        // Renders rows helper
        const createRow = (item, type) => {
            allSymbols.add(item.symbol);
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${item.symbol}</strong></td>
                <td>${item.close.toFixed(2)}</td>
                <td>${item.details.ut_trail ? item.details.ut_trail.toFixed(2) : "N/A"}</td>
                <td>${item.details.sr_zones.map(z => `${z[0].toFixed(1)}–${z[1].toFixed(1)}`).join(", ") || "None"}</td>
                <td>
                    ${item.triggered.map(cond => {
                        const styleClass = cond.includes("UT") ? "ut-badge-type" : "sr-badge-type";
                        return `<span class="condition-badge ${styleClass}">${cond}</span>`;
                    }).join("")}
                </td>
                <td>
                    <button class="btn btn-secondary btn-analyze" data-symbol="${item.symbol}" style="padding: 4px 10px; font-size: 0.75rem;">
                        <i class="fa-solid fa-chart-line"></i> Analyze
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
            buySignalsTable.innerHTML = `<tr><td colspan="6" class="empty-placeholder">No BUY signals found for this scan interval.</td></tr>`;
        }

        // Render SELL Signals
        if (data.sell_signals && data.sell_signals.length > 0) {
            data.sell_signals.forEach(item => {
                sellSignalsTable.appendChild(createRow(item, "SELL"));
            });
        } else {
            sellSignalsTable.innerHTML = `<tr><td colspan="6" class="empty-placeholder">No SELL signals found for this scan interval.</td></tr>`;
        }

        // Setup analyze buttons
        document.querySelectorAll(".btn-analyze").forEach(btn => {
            btn.addEventListener("click", () => {
                const sym = btn.getAttribute("data-symbol");
                loadSymbolChart(sym);
                // Switch tab
                tabButtons.forEach(b => b.classList.remove("active"));
                tabPanels.forEach(p => p.classList.remove("active"));
                document.querySelector('[data-tab="charting"]').classList.add("active");
                document.getElementById("panel-charting").classList.add("active");
            });
        });

        // Update stats
        const total = (data.buy_signals?.length || 0) + (data.sell_signals?.length || 0);
        statBuyCount.textContent = data.buy_signals?.length || 0;
        statSellCount.textContent = data.sell_signals?.length || 0;
        statLastScanTime.textContent = data.timestamp || new Date().toLocaleTimeString();

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

        // Populate Ticker Chart Dropdown with symbols
        populateChartDropdown(allSymbols);
    }

    function populateChartDropdown(symbolsSet) {
        chartSymbolSelect.innerHTML = `<option value="" disabled selected>Choose a symbol...</option>`;
        
        // Add all active symbols first
        if (symbolsSet.size > 0) {
            const groupActive = document.createElement("optgroup");
            groupActive.label = "Active Signals";
            Array.from(symbolsSet).sort().forEach(sym => {
                const opt = document.createElement("option");
                opt.value = sym;
                opt.textContent = sym;
                groupActive.appendChild(opt);
            });
            chartSymbolSelect.appendChild(groupActive);
        }

        // Add backup list of standard symbols from config list
        if (activeConfig && activeConfig.symbols) {
            const groupAll = document.createElement("optgroup");
            groupAll.label = "All Configured Tickers";
            activeConfig.symbols.forEach(sym => {
                // Avoid duplication
                if (!symbolsSet.has(sym)) {
                    const opt = document.createElement("option");
                    opt.value = sym;
                    opt.textContent = sym;
                    groupAll.appendChild(opt);
                }
            });
            chartSymbolSelect.appendChild(groupAll);
        }
    }

    chartSymbolSelect.addEventListener("change", () => {
        const val = chartSymbolSelect.value;
        if (val) loadSymbolChart(val);
    });

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
    // Interactive TradingView Chart Setup
    // ---------------------------------------------------------------------------
    function initChartWidget() {
        const container = document.getElementById("chart-element-container");
        container.innerHTML = ""; // Clear placeholder

        // Create core chart instance
        currentChart = LightweightCharts.createChart(container, {
            layout: {
                backgroundColor: "#0c1220",
                textColor: "#94a3b8",
                fontSize: 12,
                fontFamily: "Outfit"
            },
            grid: {
                vertLines: { color: "rgba(255, 255, 255, 0.03)" },
                horzLines: { color: "rgba(255, 255, 255, 0.03)" }
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: "#64748b", labelBackgroundColor: "#1e293b" },
                horzLine: { color: "#64748b", labelBackgroundColor: "#1e293b" }
            },
            priceScale: {
                borderColor: "rgba(255, 255, 255, 0.08)"
            },
            timeScale: {
                borderColor: "rgba(255, 255, 255, 0.08)",
                timeVisible: true,
                secondsVisible: false
            }
        });

        // Add candlestick series
        candlestickSeries = currentChart.addCandlestickSeries({
            upColor: "#10b981",
            downColor: "#ef4444",
            borderUpColor: "#10b981",
            borderDownColor: "#ef4444",
            wickUpColor: "#10b981",
            wickDownColor: "#ef4444"
        });

        // Add UT Trail Line series
        trailLineSeries = currentChart.addLineSeries({
            color: "#3b82f6",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            title: "UT Trail Stop"
        });

        // Resize handler
        const resizeObserver = new ResizeObserver(entries => {
            if (entries.length === 0 || !currentChart) return;
            const { width, height } = entries[0].contentRect;
            currentChart.resize(width, height);
        });
        resizeObserver.observe(container);
    }

    async function loadSymbolChart(symbol) {
        if (!currentChart) {
            initChartWidget();
        }

        chartTitle.textContent = `${symbol} — Historical Candlesticks`;
        chartTimeframeDisplay.textContent = activeConfig?.scan_timeframe || "15m";
        chartTickerDetails.style.display = "block";

        try {
            const tf = activeConfig?.scan_timeframe || "15m";
            const resp = await fetch(`${API_BASE}/api/history/${symbol}?timeframe=${tf}`);
            if (!resp.ok) throw new Error(`Symbol ${symbol} history fetch failed.`);
            const data = await resp.json();

            // 1. Plot Candlesticks
            const candles = data.history.map(bar => ({
                time: bar.time,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close
            }));
            candlestickSeries.setData(candles);

            // 2. Plot UT Trail line
            const trails = data.history
                .filter(bar => bar.ut_trail !== null)
                .map(bar => ({
                    time: bar.time,
                    value: bar.ut_trail
                }));
            
            if (toggleUtLine.checked) {
                trailLineSeries.setData(trails);
            } else {
                trailLineSeries.setData([]);
            }

            // 3. Draw S/R Zone Dotted Lines
            // Remove previous lines
            activeSRLines.forEach(line => candlestickSeries.removePriceLine(line));
            activeSRLines = [];

            if (toggleSrZones.checked && data.sr_zones) {
                data.sr_zones.forEach((zone, index) => {
                    // Draw lines at the top and bottom of each zone
                    const hiLine = candlestickSeries.createPriceLine({
                        price: zone.high,
                        color: "rgba(16, 185, 129, 0.4)",
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `S/R Zone ${index+1} Top`
                    });
                    const loLine = candlestickSeries.createPriceLine({
                        price: zone.low,
                        color: "rgba(239, 68, 68, 0.4)",
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `S/R Zone ${index+1} Bottom`
                    });
                    activeSRLines.push(hiLine, loLine);
                });
            }

            // 4. Draw UT Bot Signal Markers
            if (toggleMarkers.checked) {
                const markers = [];
                data.history.forEach(bar => {
                    if (bar.buy) {
                        markers.push({
                            time: bar.time,
                            position: "belowBar",
                            color: "#10b981",
                            shape: "arrowUp",
                            text: "UT BUY",
                            size: 1.5
                        });
                    } else if (bar.sell) {
                        markers.push({
                            time: bar.time,
                            position: "aboveBar",
                            color: "#ef4444",
                            shape: "arrowDown",
                            text: "UT SELL",
                            size: 1.5
                        });
                    }
                });
                candlestickSeries.setMarkers(markers);
            } else {
                candlestickSeries.setMarkers([]);
            }

            // Fit content
            currentChart.timeScale().fitContent();

            // Populate Sidebar details
            const lastBar = data.history[data.history.length - 1];
            document.getElementById("detail-close").textContent = lastBar ? lastBar.close.toFixed(2) : "-";
            document.getElementById("detail-trail").textContent = (lastBar && lastBar.ut_trail) ? lastBar.ut_trail.toFixed(2) : "N/A";
            document.getElementById("detail-zones").textContent = data.sr_zones ? data.sr_zones.length : "0";

        } catch (err) {
            console.error(err);
            alert(`Error rendering chart data: ${err.message}`);
        }
    }

    // Chart Options Toggle Listeners
    toggleUtLine.addEventListener("change", () => {
        const val = chartSymbolSelect.value;
        if (val) loadSymbolChart(val);
    });
    toggleSrZones.addEventListener("change", () => {
        const val = chartSymbolSelect.value;
        if (val) loadSymbolChart(val);
    });
    toggleMarkers.addEventListener("change", () => {
        const val = chartSymbolSelect.value;
        if (val) loadSymbolChart(val);
    });

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
