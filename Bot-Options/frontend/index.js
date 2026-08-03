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
        utEnabled: document.getElementById("config-ut-enabled"),
        srEnabled: document.getElementById("config-sr-enabled"),
        filterMtf: document.getElementById("config-filter-mtf"),
        filterEma: document.getElementById("config-filter-ema"),
        filterCandle: document.getElementById("config-filter-candle"),
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
                loadTradeHistory();
            }
        });
    });

    // ---------------------------------------------------------------------------
    // Underlying Selector + TradingView link updater
    // ---------------------------------------------------------------------------

    const tvUnderlyingLink = document.getElementById("tv-underlying-link");

    /** Update the TV icon href and tooltip to match the active underlying. */
    function updateTvUnderlyingLink(underlying) {
        if (!tvUnderlyingLink) return;
        const url = "https://www.tradingview.com/chart/H8qWRNcF/?symbol=" + encodeURIComponent(underlying);
        tvUnderlyingLink.href  = url;
        tvUnderlyingLink.title = "Open " + underlying + " chart on TradingView";
    }

    el.underlyingBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            el.underlyingBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentUnderlying = btn.dataset.val;
            updateTvUnderlyingLink(currentUnderlying);

            if (activeTab === "tab-chain") {
                loadOptionChain();
            }
            triggerExpiryCountdown();
        });
    });

    // Set correct URL on page load (default underlying is NIFTY)
    updateTvUnderlyingLink(currentUnderlying);

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
    // -------------------------------------------------------------------------
    // loadConfig — populates BOTH the left-panel quick controls AND the
    // full settings drawer from the single /api/config response.
    // -------------------------------------------------------------------------
    async function loadConfig() {
        try {
            const res = await fetch("/api/config");
            configData = await res.json();

            // ── Left panel quick controls ────────────────────────────────────
            el.expirySelect.value        = configData.strike_selection.expiry_preference;
            el.strikeMethodSelect.value  = configData.strike_selection.method;
            el.premiumMinInput.value     = configData.strike_selection.premium_min;
            el.premiumMaxInput.value     = configData.strike_selection.premium_max;
            el.stage3Enabled.checked     = configData.option_chart_confirmation.enabled;

            el.utEnabled.checked   = configData.strategy?.ut_enabled !== false;
            el.srEnabled.checked   = configData.sr_channels?.enabled !== false;
            el.filterMtf.checked   = configData.filters.mtf_filter_enabled;
            el.filterEma.checked   = configData.filters.ema_filter_enabled;
            el.filterCandle.checked= configData.filters.candle_patterns_enabled !== false;
            el.filterIv.checked    = configData.filters.iv_score_enabled;
            el.filterOi.checked    = configData.filters.oi_momentum_score_enabled;
            el.filterDecay.checked = configData.filters.time_decay_penalty_enabled;

            if (configData.strike_selection.method === "PREMIUM") {
                el.premiumRangeSec.style.display = "block";
            }

            // ── Drawer: Signal Engine ────────────────────────────────────────
            const strat = configData.strategy || {};
            const sr    = configData.sr_channels || {};
            setRangeAndLabel("cfg-ut-keyval",  "cfg-ut-keyval-lbl",  strat.key_value       ?? 1.0,  v => v);
            d("cfg-ut-atr").value        = strat.atr_period           ?? 2;
            d("cfg-ut-ha").checked       = !!strat.use_heikin_ashi;
            d("cfg-sr-pivot").value      = sr.pivot_period            ?? 10;
            d("cfg-sr-width").value      = sr.channel_width_pct       ?? 5.0;
            d("cfg-sr-proximity").value  = sr.proximity_pct           ?? 0.2;

            const gate1Val = configData.min_underlying_score ?? 60;
            const gate2Val = configData.filters?.min_alert_score ?? 60;
            setScoreSlider("cfg-gate1", "cfg-gate1-lbl", gate1Val);
            setScoreSlider("cfg-gate2", "cfg-gate2-lbl", gate2Val);

            d("cfg-lookback").value  = configData.signal_lookback_candles ?? 2;
            d("cfg-timeframe").value = configData.scan_timeframe          ?? "5m";

            // ── Drawer: Filters ──────────────────────────────────────────────
            const filt = configData.filters || {};
            d("cfg-mtf-tf").value     = filt.mtf_timeframe             ?? "15m";
            d("cfg-ema-period").value = filt.ema_period                ?? 200;
            d("cfg-adx-min").value    = filt.adx_min_threshold         ?? 20;
            d("cfg-decay-days").value = filt.time_decay_threshold_days ?? 3;
            d("cfg-dedup-mins").value = Math.round(
                (configData.scan_dedup_window_seconds ?? 900) / 60
            );
            d("cfg-candle-patterns").checked = filt.candle_patterns_enabled !== false;

            // ── Drawer: Execution ────────────────────────────────────────────
            const exec = configData.execution || {};
            setOrderMode(exec.order_mode ?? "manual");
            d("cfg-num-lots").value      = exec.num_lots          ?? 1;
            d("cfg-order-type").value    = exec.order_type        ?? "LIMIT";
            d("cfg-order-product").value = exec.order_product     ?? "MIS";
            d("cfg-fill-timeout").value  = exec.fill_timeout_seconds ?? 30;
            d("cfg-strategy-tag").value  = exec.strategy_tag      ?? "OptionsBot";

            // ── Drawer: Trade Management ─────────────────────────────────────
            const tm  = configData.trade_management   || {};
            const tsl = tm.trailing_sl   || {};
            const pe  = tm.partial_exit  || {};
            const exp = tm.expiry_management || {};

            setRangeAndLabel("cfg-sl-pct",  "cfg-sl-pct-lbl",  tm.stop_loss_pct ?? 30, v => v + "%");
            setRangeAndLabel("cfg-tgt-pct", "cfg-tgt-pct-lbl", tm.target_pct    ?? 50, v => v + "%");
            d("cfg-trail-act").value  = tsl.activation_pct    ?? 25;
            d("cfg-trail-dist").value = tsl.distance_pct      ?? 15;
            d("cfg-partial-tgt").value = pe.target1_pct       ?? 30;
            d("cfg-expiry-mins").value = exp.exit_minutes_before_close ?? 10;

            // ── Drawer: Risk Management ──────────────────────────────────────
            const risk = configData.risk_management || {};
            d("cfg-max-pos").value           = risk.max_simultaneous_positions ?? 5;
            d("cfg-max-trades").value        = risk.max_trades_per_day         ?? 10;
            d("cfg-max-loss-amt").value      = risk.max_daily_loss_amount      ?? 5000;
            d("cfg-consec-loss").value       = risk.consecutive_loss_limit     ?? 3;
            d("cfg-cooldown-mins").value     = risk.cooldown_minutes           ?? 30;
            d("cfg-capital").value           = risk.capital_allocation         ?? 100000;
            d("cfg-capital-per-trade").value = risk.max_capital_per_trade      ?? 50000;

            // ── Drawer: Scan Schedule ────────────────────────────────────────
            const bot = configData.bot || {};
            d("cfg-scan-interval").value  = configData.scan_interval_seconds ?? 60;
            d("cfg-mkt-hours").checked    = !!bot.market_hours_check;
            d("cfg-lookback-days").value  = configData.data?.lookback_days   ?? 30;
            d("cfg-log-level").value      = bot.log_level                    ?? "INFO";
    
            // ── Auto-scan persistent state ───────────────────────────────────
            // If config says auto_scan_enabled=true, check the toggle and start
            // the loop immediately so it resumes after a page reload.
            if (configData.auto_scan_enabled === true) {
                el.autoScanToggle.checked = true;
                const intervalMs = Math.max(configData.scan_interval_seconds || 60, 30) * 1000;
                if (!autoScanIntervalId) {
                    autoScanIntervalId = setInterval(autoScanTick, intervalMs);
                    console.info(`Auto-scan resumed from config — interval: ${intervalMs / 1000}s`);
                }
            } else {
                el.autoScanToggle.checked = false;
            }
    
            triggerExpiryCountdown();
        } catch (e) {
            console.error("Failed to load config:", e);
        }
    }

    // -------------------------------------------------------------------------
    // saveConfig — reads BOTH left-panel controls AND drawer fields back into
    // configData and posts the entire merged object to /api/config.
    // Called by the drawer SAVE button and also by left-panel quick-change events.
    // -------------------------------------------------------------------------
    async function saveConfig() {
        if (!configData || !configData.strike_selection) return;

        // ── Left panel quick controls ────────────────────────────────────────
        configData.strike_selection.expiry_preference = el.expirySelect.value;
        configData.strike_selection.method            = el.strikeMethodSelect.value;
        configData.strike_selection.premium_min       = parseFloat(el.premiumMinInput.value) || 50;
        configData.strike_selection.premium_max       = parseFloat(el.premiumMaxInput.value) || 500;
        configData.option_chart_confirmation.enabled  = el.stage3Enabled.checked;

        configData.strategy.ut_enabled                = el.utEnabled.checked;
        configData.sr_channels.enabled                = el.srEnabled.checked;
        configData.filters.mtf_filter_enabled         = el.filterMtf.checked;
        configData.filters.ema_filter_enabled         = el.filterEma.checked;
        configData.filters.candle_patterns_enabled    = el.filterCandle.checked;
        configData.filters.iv_score_enabled           = el.filterIv.checked;
        configData.filters.oi_momentum_score_enabled  = el.filterOi.checked;
        configData.filters.time_decay_penalty_enabled = el.filterDecay.checked;

        // ── Drawer: Signal Engine ────────────────────────────────────────────
        configData.strategy.key_value              = parseFloat(d("cfg-ut-keyval").value)  || 1.0;
        configData.strategy.atr_period             = parseInt(d("cfg-ut-atr").value)       || 2;
        configData.strategy.use_heikin_ashi        = d("cfg-ut-ha").checked;
        configData.sr_channels.pivot_period        = parseInt(d("cfg-sr-pivot").value)     || 10;
        configData.sr_channels.channel_width_pct   = parseFloat(d("cfg-sr-width").value)   || 5.0;
        configData.sr_channels.proximity_pct       = parseFloat(d("cfg-sr-proximity").value) || 0.2;
        configData.min_underlying_score            = parseInt(d("cfg-gate1").value)         || 60;
        configData.filters.min_alert_score         = parseInt(d("cfg-gate2").value)         || 60;
        configData.signal_lookback_candles         = parseInt(d("cfg-lookback").value)      || 2;
        configData.scan_timeframe                  = d("cfg-timeframe").value;

        // ── Drawer: Filters ──────────────────────────────────────────────────
        configData.filters.mtf_timeframe             = d("cfg-mtf-tf").value;
        configData.filters.ema_period                = parseInt(d("cfg-ema-period").value)  || 200;
        configData.filters.adx_min_threshold         = parseFloat(d("cfg-adx-min").value)   || 20;
        configData.filters.time_decay_threshold_days = parseInt(d("cfg-decay-days").value)  || 3;
        configData.scan_dedup_window_seconds         = (parseInt(d("cfg-dedup-mins").value) || 15) * 60;
        configData.filters.candle_patterns_enabled   = d("cfg-candle-patterns").checked;

        // ── Drawer: Execution ────────────────────────────────────────────────
        configData.execution.order_mode        = drawerOrderMode;
        configData.execution.num_lots          = parseInt(d("cfg-num-lots").value)      || 1;
        configData.execution.order_type        = d("cfg-order-type").value;
        configData.execution.order_product     = d("cfg-order-product").value;
        configData.execution.fill_timeout_seconds = parseInt(d("cfg-fill-timeout").value) || 30;
        configData.execution.strategy_tag     = d("cfg-strategy-tag").value.trim() || "OptionsBot";

        // ── Drawer: Trade Management ─────────────────────────────────────────
        configData.trade_management.stop_loss_pct          = parseFloat(d("cfg-sl-pct").value)       || 30;
        configData.trade_management.target_pct             = parseFloat(d("cfg-tgt-pct").value)      || 50;
        configData.trade_management.trailing_sl.activation_pct = parseFloat(d("cfg-trail-act").value) || 25;
        configData.trade_management.trailing_sl.distance_pct   = parseFloat(d("cfg-trail-dist").value) || 15;
        configData.trade_management.partial_exit.target1_pct   = parseFloat(d("cfg-partial-tgt").value) || 30;
        configData.trade_management.expiry_management.exit_minutes_before_close = parseInt(d("cfg-expiry-mins").value) || 10;

        // ── Drawer: Risk Management ──────────────────────────────────────────
        configData.risk_management.max_simultaneous_positions = parseInt(d("cfg-max-pos").value)       || 5;
        configData.risk_management.max_trades_per_day         = parseInt(d("cfg-max-trades").value)    || 10;
        configData.risk_management.max_daily_loss_amount      = parseFloat(d("cfg-max-loss-amt").value)|| 5000;
        configData.risk_management.consecutive_loss_limit     = parseInt(d("cfg-consec-loss").value)   || 3;
        configData.risk_management.cooldown_minutes           = parseInt(d("cfg-cooldown-mins").value) || 30;
        configData.risk_management.capital_allocation         = parseFloat(d("cfg-capital").value)     || 100000;
        configData.risk_management.max_capital_per_trade      = parseFloat(d("cfg-capital-per-trade").value) || 50000;

        // ── Drawer: Scan Schedule ────────────────────────────────────────────
        configData.scan_interval_seconds      = parseInt(d("cfg-scan-interval").value)  || 60;
        configData.bot.market_hours_check     = d("cfg-mkt-hours").checked;
        if (!configData.data) configData.data = {};
        configData.data.lookback_days         = parseInt(d("cfg-lookback-days").value)  || 30;
        configData.bot.log_level              = d("cfg-log-level").value;

        try {
            const resp = await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(configData)
            });
            return resp.ok;
        } catch (e) {
            console.error("Failed to save config:", e);
            return false;
        }
    }

    // Attach save-on-change to quick left-panel inputs (no status feedback needed)
    const saveTriggers = [
        el.expirySelect, el.strikeMethodSelect, el.premiumMinInput, el.premiumMaxInput,
        el.stage3Enabled,
        el.utEnabled, el.srEnabled,
        el.filterMtf, el.filterEma, el.filterCandle,
        el.filterIv, el.filterOi, el.filterDecay
    ];
    saveTriggers.forEach(input => input.addEventListener("change", saveConfig));

    // =========================================================================
    // SETTINGS DRAWER logic
    // =========================================================================

    const drawerEl       = document.getElementById("settings-drawer");
    const drawerOverlay  = document.getElementById("drawer-overlay");
    const btnOpen        = document.getElementById("btn-settings-open");
    const btnClose       = document.getElementById("btn-settings-close");
    const btnSave        = document.getElementById("btn-settings-save");
    const drawerStatus   = document.getElementById("drawer-save-status");

    // Track the selected order mode separately (MODE-BTN segmented control)
    let drawerOrderMode = "manual";

    // ── Helper: getElementById shorthand ────────────────────────────────────
    function d(id) { return document.getElementById(id); }

    // ── Helper: set a range input AND its readout label ──────────────────────
    function setRangeAndLabel(inputId, labelId, value, fmtFn) {
        const inp = d(inputId);
        const lbl = d(labelId);
        if (!inp || !lbl) return;
        inp.value = value;
        lbl.textContent = fmtFn ? fmtFn(value) : value;
    }

    // ── Helper: set score slider + colour-coded label ─────────────────────────
    function setScoreSlider(inputId, labelId, value) {
        const inp = d(inputId);
        const lbl = d(labelId);
        if (!inp || !lbl) return;
        inp.value = value;
        lbl.textContent = value;
        lbl.className = "score-lbl-val " + (value < 40 ? "low" : value < 65 ? "mid" : "high");
    }

    // ── Helper: set the order-mode segmented control ─────────────────────────
    function setOrderMode(mode) {
        drawerOrderMode = mode;
        document.querySelectorAll(".mode-btn").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.mode === mode);
        });
    }

    // ── Open / close the drawer ──────────────────────────────────────────────
    function openDrawer() {
        drawerEl.classList.add("open");
        drawerOverlay.classList.add("active");
        btnOpen.classList.add("active");
    }

    function closeDrawer() {
        drawerEl.classList.remove("open");
        drawerOverlay.classList.remove("active");
        btnOpen.classList.remove("active");
    }

    btnOpen.addEventListener("click", () => {
        drawerEl.classList.contains("open") ? closeDrawer() : openDrawer();
    });
    btnClose.addEventListener("click", closeDrawer);
    drawerOverlay.addEventListener("click", closeDrawer);

    // Esc key closes the drawer
    document.addEventListener("keydown", e => {
        if (e.key === "Escape" && drawerEl.classList.contains("open")) closeDrawer();
    });

    // ── Save button in drawer footer ─────────────────────────────────────────
    btnSave.addEventListener("click", async () => {
        btnSave.disabled = true;
        btnSave.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Saving...`;
        drawerStatus.textContent = "";
        drawerStatus.className = "drawer-save-status";

        const ok = await saveConfig();

        btnSave.disabled = false;
        btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SAVE SETTINGS`;

        if (ok) {
            drawerStatus.textContent = "✓ Saved";
            drawerStatus.className = "drawer-save-status ok";
        } else {
            drawerStatus.textContent = "✗ Error";
            drawerStatus.className = "drawer-save-status err";
        }
        // Fade out status after 3 seconds
        setTimeout(() => { drawerStatus.textContent = ""; drawerStatus.className = "drawer-save-status"; }, 3000);
    });

    // ── Accordion toggle ──────────────────────────────────────────────────────
    document.querySelectorAll(".acc-header").forEach(header => {
        header.addEventListener("click", () => {
            const targetId = header.dataset.target;
            const body     = d(targetId);
            const arrow    = header.querySelector(".acc-arrow");
            if (!body) return;

            const isOpen = body.classList.contains("acc-body--open");

            // Close all sections first
            document.querySelectorAll(".acc-body").forEach(b => b.classList.remove("acc-body--open"));
            document.querySelectorAll(".acc-header").forEach(h => {
                h.classList.remove("acc-header--open");
                h.querySelector(".acc-arrow")?.classList.remove("acc-arrow--open");
            });

            // Toggle the clicked one
            if (!isOpen) {
                body.classList.add("acc-body--open");
                header.classList.add("acc-header--open");
                arrow?.classList.add("acc-arrow--open");
            }
        });
    });

    // ── Candle patterns two-way sync (left panel ↔ drawer) ───────────────────
    // The same config field is controlled by two checkboxes:
    //   config-filter-candle  (left panel quick toggle)
    //   cfg-candle-patterns   (drawer Filters section)
    // Toggling either one silently mirrors the value to the other.
    el.filterCandle.addEventListener("change", function () {
        d("cfg-candle-patterns").checked = this.checked;
    });
    d("cfg-candle-patterns").addEventListener("change", function () {
        el.filterCandle.checked = this.checked;
    });

    // ── Range slider live readouts ────────────────────────────────────────────
    // UTBot key_value
    d("cfg-ut-keyval").addEventListener("input", function () {
        d("cfg-ut-keyval-lbl").textContent = parseFloat(this.value).toFixed(1);
    });
    // SL %
    d("cfg-sl-pct").addEventListener("input", function () {
        d("cfg-sl-pct-lbl").textContent = this.value + "%";
    });
    // Target %
    d("cfg-tgt-pct").addEventListener("input", function () {
        d("cfg-tgt-pct-lbl").textContent = this.value + "%";
    });
    // Gate 1 score
    d("cfg-gate1").addEventListener("input", function () {
        setScoreSlider("cfg-gate1", "cfg-gate1-lbl", parseInt(this.value));
    });
    // Gate 2 score
    d("cfg-gate2").addEventListener("input", function () {
        setScoreSlider("cfg-gate2", "cfg-gate2-lbl", parseInt(this.value));
    });

    // ── Order-mode segmented control ─────────────────────────────────────────
    document.querySelectorAll(".mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const mode = btn.dataset.mode;
            if (mode === "auto") {
                const ok = confirm(
                    "⚠️ Switch to AUTO mode?\n\n" +
                    "Orders will be placed INSTANTLY when a signal fires, " +
                    "without any manual confirmation.\n\n" +
                    "Make sure your risk parameters are set correctly."
                );
                if (!ok) return;
            }
            setOrderMode(mode);
        });
    });

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

    // Active filter/sort state for the signal toolbar
    let sigFilter = "all";   // "all" | "BUY" | "SELL" | "NIFTY" | "BANKNIFTY"
    let sigSort   = "newest"; // "newest" | "score" | "premium"
    let _allSignals = [];     // full unfiltered list from last API call

    // Wire toolbar buttons
    document.querySelectorAll(".sig-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".sig-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            sigFilter = btn.dataset.filter;
            applySignalFilterSort();
        });
    });

    const sigSortSelect = document.getElementById("sig-sort-select");
    if (sigSortSelect) {
        sigSortSelect.addEventListener("change", () => {
            sigSort = sigSortSelect.value;
            applySignalFilterSort();
        });
    }

    function applySignalFilterSort() {
        let list = _allSignals.slice();

        // Filter
        if (sigFilter !== "all") {
            list = list.filter(s =>
                s.direction   === sigFilter ||
                s.underlying  === sigFilter
            );
        }

        // Sort
        if (sigSort === "score") {
            list.sort((a, b) => parseFloat(b.confidence_score || 0) - parseFloat(a.confidence_score || 0));
        } else if (sigSort === "premium") {
            list.sort((a, b) => parseFloat(b.entry_premium || 0) - parseFloat(a.entry_premium || 0));
        }
        // "newest" keeps DB order (already DESC by id)

        renderSignals(list);
    }

    // ---------------------------------------------------------------------------
    // Load Signals from DB  (called on page load AND after every scan)
    // ---------------------------------------------------------------------------
    async function loadSignals() {
        // Reset badge immediately so it never shows a stale count while fetching
        el.signalsCount.textContent = "0";
        try {
            const res = await fetch("/api/signals?limit=50");
            const data = await res.json();
            if (data.status === "success") {
                _allSignals = data.signals || [];
                applySignalFilterSort();
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
                        ${tvLink(sig.underlying)}
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
                        <button class="btn btn-primary execute-sig-btn" data-symbol="${sig.symbol}" data-underlying="${sig.underlying}" data-direction="${sig.direction}" data-premium="${sig.entry_premium}">EXECUTE</button>
                    </div>
                </div>
            `;
            
            el.signalsList.appendChild(card);
        });

        // Add execution click listeners
        document.querySelectorAll(".execute-sig-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const sym        = btn.dataset.symbol;
                const underlying = btn.dataset.underlying || "NIFTY";
                const direction  = btn.dataset.direction  || "BUY";
                const premium    = parseFloat(btn.dataset.premium || 0);

                // Lot sizes per underlying (mirrors config.yml)
                const LOT_SIZES = { NIFTY: 75, BANKNIFTY: 30 };
                const lotSize   = LOT_SIZES[underlying] || 75;
                const numLots   = parseInt((configData.execution || {}).num_lots || 1);
                const qty       = lotSize * numLots;

                showConfirmModal({
                    title: `Execute ${direction}`,
                    lines: [
                        `Symbol: <b>${sym}</b>`,
                        `Underlying: ${underlying} | Lot size: ${lotSize} × ${numLots} lots = <b>${qty} qty</b>`,
                        `Premium: ₹${premium.toFixed(2)}  |  Est. outlay: ₹${(premium * qty).toFixed(0)}`,
                    ],
                    confirmLabel: "PLACE ORDER",
                    onConfirm: async () => {
                        btn.disabled = true;
                        btn.textContent = "PLACING...";
                        try {
                            const resp = await fetch("/api/order", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ symbol: sym, action: direction, quantity: qty })
                            });
                            const res = await resp.json();
                            if (res.status === "success") {
                                loadPositions();
                            } else {
                                alert("Execution failed: " + (res.detail || "Unknown error"));
                            }
                        } catch (e) {
                            console.error("Order submit failed: ", e);
                        } finally {
                            btn.disabled = false;
                            btn.textContent = "EXECUTE";
                        }
                    }
                });
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
                updatePortfolioGreeks();
                return;
            }

            el.positionsList.innerHTML = "";
            el.riskLegsCount.textContent = `${positions.length} / 5`;
            
            let totalDelta = 0.0;
            let totalTheta = 0.0;

            positions.forEach(pos => {
                const currentPremium = parseFloat(pos.current_premium || pos.entry_premium);
                const entryPremium   = parseFloat(pos.entry_premium);
                const targetPremium  = parseFloat(pos.target_premium  || (entryPremium * 1.5));
                const slPremium      = parseFloat(pos.current_sl_premium || (entryPremium * 0.7));
                const pnl       = currentPremium - entryPremium;
                const pnlPct    = entryPremium > 0 ? (pnl / entryPremium) * 100.0 : 0;
                const quantity  = intVal(pos.quantity);
                const pnlAmount = pnl * quantity;

                // Progress bar — percentage of the SL→Target corridor traversed
                // 0% = at SL, 100% = at target, clipped [0,100]
                const corridor   = targetPremium - slPremium;
                const barPct     = corridor > 0
                    ? Math.max(0, Math.min(100, (currentPremium - slPremium) / corridor * 100))
                    : 50;
                const barColor   = pnl >= 0 ? "var(--success-green)" : "var(--danger-red)";

                // Badges
                const trailingBadge = pos.trailing_active
                    ? `<span class="pos-badge trail">⟳ TRAIL</span>` : "";
                const lockLevel  = parseInt(pos.profit_locked || 0);
                const lockBadge  = lockLevel > 0
                    ? `<span class="pos-badge lock">🔒 L${lockLevel}</span>` : "";
                const partialBadge = pos.partial_exit_done
                    ? `<span class="pos-badge partial">½ PARTIAL</span>` : "";

                // Greek accumulation (used only for void suppression now)
                totalDelta += 0.49 * (pos.option_type === "CE" ? 1 : -1) * (quantity / 75.0);
                totalTheta += -4.2 * (quantity / 75.0);

                const card = document.createElement("div");
                card.className = `pos-card ${pnl >= 0 ? "up" : "down"}`;

                card.innerHTML = `
                    <div class="pos-top">
                        <span class="pos-sym">${pos.symbol}</span>
                        ${tvLink(pos.underlying)}
                        <div class="pos-badges">${trailingBadge}${lockBadge}${partialBadge}</div>
                        <span class="pos-pnl ${pnl >= 0 ? "profit" : "loss"}">
                            ${pnl >= 0 ? "+" : ""}₹${pnlAmount.toFixed(0)} (${pnlPct.toFixed(1)}%)
                        </span>
                    </div>
                    <div class="pos-details">
                        <span>LTP: <b>₹${currentPremium.toFixed(2)}</b></span>
                        <span>Entry: ₹${entryPremium.toFixed(2)}</span>
                        <span>Qty: ${quantity}</span>
                    </div>
                    <div class="pos-progress-row">
                        <span class="pos-prog-label">SL ₹${slPremium.toFixed(0)}</span>
                        <div class="pos-progress-bar">
                            <div class="pos-progress-fill" style="width:${barPct.toFixed(1)}%;background:${barColor}"></div>
                        </div>
                        <span class="pos-prog-label">TGT ₹${targetPremium.toFixed(0)}</span>
                    </div>
                    <div class="pos-action-row">
                        <span class="badge-status">SL: ₹${slPremium.toFixed(2)}</span>
                        <button class="pos-close-btn" data-id="${pos.id}" data-symbol="${pos.symbol}">CLOSE</button>
                    </div>
                `;
                
                el.positionsList.appendChild(card);
            });

            // Update greeks via real API call (removes the per-position fake math)
            updatePortfolioGreeks();
            // Legacy dummy vars no longer needed but retained to avoid ref errors
            void totalDelta; void totalTheta;

            // Attach close triggers
            document.querySelectorAll(".pos-close-btn").forEach(btn => {
                btn.addEventListener("click", () => {
                    const posId = btn.dataset.id;
                    const sym   = btn.dataset.symbol || posId;
                    showConfirmModal({
                        title: "Close Position",
                        lines: [`Close position <b>${sym}</b>? This will market-exit the trade.`],
                        confirmLabel: "CLOSE NOW",
                        confirmClass: "btn-danger",
                        onConfirm: async () => {
                            btn.disabled = true;
                            btn.textContent = "CLOSING...";
                            try {
                                const resp = await fetch(`/api/positions/${posId}/close`, { method: "POST" });
                                const res = await resp.json();
                                if (res.status === "success") {
                                    loadPositions();
                                } else {
                                    alert("Failed to close: " + (res.detail || "Unknown error"));
                                }
                            } catch (e) {
                                console.error(e);
                            } finally {
                                btn.disabled = false;
                                btn.textContent = "CLOSE";
                            }
                        }
                    });
                });
            });

        } catch (e) {
            console.error("Positions load error: ", e);
        }
    }

    async function updatePortfolioGreeks() {
        try {
            const res = await fetch("/api/greeks");
            const data = await res.json();
            if (data.status !== "success") return;
            const p = data.portfolio || {};
            const delta = parseFloat(p.net_delta || 0);
            const theta = parseFloat(p.net_theta_inr || 0);
            const vega  = parseFloat(p.net_vega  || 0);
            const gamma = parseFloat(p.net_gamma || 0);
            el.portfolioDelta.textContent = (delta >= 0 ? "+" : "") + delta.toFixed(2);
            el.portfolioTheta.textContent = (theta >= 0 ? "+" : "-") + "₹" + Math.abs(theta).toFixed(2) + "/d";
            el.portfolioVega.textContent  = (vega  >= 0 ? "+" : "") + vega.toFixed(2);
            el.portfolioGamma.textContent = (gamma >= 0 ? "+" : "") + gamma.toFixed(4);
        } catch (e) {
            console.error("Greeks fetch error:", e);
        }
    }

    // ---------------------------------------------------------------------------
    // Fetch Index Ticker Levels  (wired to /api/market-pulse — spot LTP only)
    // Fast path: only renders spot prices. VIX/PCR are served from server cache
    // so this stays sub-100 ms even with 2 underlyings.
    // ---------------------------------------------------------------------------
    async function loadIndexTicks() {
        try {
            const res  = await fetch("/api/market-pulse");
            const data = await res.json();
            if (data.status !== "success") return;

            (data.underlyings || []).forEach(u => {
                let tickerEl = null;
                if (u.symbol === "NIFTY")     tickerEl = el.tickerNifty;
                if (u.symbol === "BANKNIFTY") tickerEl = el.tickerBanknifty;
                if (!tickerEl) return;

                const ltp    = parseFloat(u.ltp || 0);
                const chg    = parseFloat(u.change_pct || 0);
                const chgDir = chg >= 0 ? "up" : "down";
                const sign   = chg >= 0 ? "+" : "";

                tickerEl.querySelector(".ticker-price").textContent = ltp.toFixed(2);
                const changeEl = tickerEl.querySelector(".ticker-change");
                changeEl.textContent = `${sign}${chg.toFixed(2)}%`;
                changeEl.className   = `ticker-change ${chgDir}`;
            });

            // VIX — from server cache, still update when present
            const vixEl = document.getElementById("ticker-vix");
            if (vixEl && data.vix != null) {
                vixEl.querySelector(".ticker-price").textContent = parseFloat(data.vix).toFixed(2);
            }

            // PCR badges
            Object.entries(data.pcr || {}).forEach(([sym, val]) => {
                const el2 = document.getElementById("pcr-" + sym.toLowerCase());
                if (el2) el2.textContent = "PCR " + parseFloat(val.pcr || 0).toFixed(2);
            });

        } catch (e) {
            console.error("Market pulse fetch error: ", e);
        }
    }

    // ---------------------------------------------------------------------------
    // Risk Status Panel
    // ---------------------------------------------------------------------------
    async function loadRiskStatus() {
        try {
            const res  = await fetch("/api/risk-status");
            const data = await res.json();
            if (data.status !== "success") return;

            // Legs / trades
            if (el.riskLegsCount)   el.riskLegsCount.textContent   = `${data.open_legs} / ${data.max_legs}`;
            if (el.riskTradesCount) el.riskTradesCount.textContent = `${data.trades_today} / ${data.max_trades_per_day}`;
            if (el.riskConsecLosses) el.riskConsecLosses.textContent = `${data.consecutive_losses} / ${data.consecutive_loss_limit}`;

            // Daily P&L
            const pnl = parseFloat(data.daily_pnl || 0);
            const maxLoss = parseFloat(data.max_daily_loss || 5000);
            const lossUsed = Math.min(100, Math.abs(Math.min(0, pnl)) / maxLoss * 100);
            if (el.riskDailyLoss) {
                el.riskDailyLoss.textContent = (pnl >= 0 ? "+₹" : "-₹") + Math.abs(pnl).toFixed(0);
                el.riskDailyLoss.className = pnl >= 0 ? "metric-value profit" : "metric-value loss";
            }
            if (el.riskLossFill) el.riskLossFill.style.width = lossUsed.toFixed(1) + "%";

            // Capital
            const capPct = parseFloat(data.capital_pct || 0);
            if (el.capitalUsed)     el.capitalUsed.textContent     = "₹" + parseFloat(data.capital_used || 0).toLocaleString("en-IN");
            if (el.capitalAvailable) el.capitalAvailable.textContent = "₹" + parseFloat(data.capital_total || 0).toLocaleString("en-IN");
            if (el.capitalProgressFill) el.capitalProgressFill.style.width = capPct.toFixed(1) + "%";

            // Cooldown warning banner
            const cooldownBanner = document.getElementById("cooldown-banner");
            if (cooldownBanner) {
                if (data.cooldown_active) {
                    cooldownBanner.textContent = `⚠ Risk Cooldown active — ${data.cooldown_remaining_min} min remaining`;
                    cooldownBanner.style.display = "block";
                } else {
                    cooldownBanner.style.display = "none";
                }
            }
        } catch (e) {
            console.error("Risk status fetch error:", e);
        }
    }

    // ---------------------------------------------------------------------------
    // Confirmation Modal  (replaces native alert/confirm for EXECUTE/CLOSE)
    // ---------------------------------------------------------------------------
    function showConfirmModal({ title, lines, confirmLabel, confirmClass, onConfirm }) {
        // Remove any stale modal
        const stale = document.getElementById("confirm-modal-overlay");
        if (stale) stale.remove();

        const overlay = document.createElement("div");
        overlay.id = "confirm-modal-overlay";
        overlay.style.cssText = [
            "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999",
            "display:flex;align-items:center;justify-content:center"
        ].join(";");

        const btnClass = confirmClass || "btn-primary";
        overlay.innerHTML = `
            <div style="background:#1c2333;border:1px solid #30363d;border-radius:8px;padding:24px 28px;min-width:340px;max-width:480px">
                <h3 style="margin:0 0 14px;font-size:15px;color:#e6edf3">${title}</h3>
                <div style="font-size:13px;color:#8b949e;line-height:1.7;margin-bottom:20px">
                    ${lines.map(l => `<div>${l}</div>`).join("")}
                </div>
                <div style="display:flex;gap:10px;justify-content:flex-end">
                    <button id="modal-cancel" class="btn btn-secondary" style="padding:6px 18px">Cancel</button>
                    <button id="modal-confirm" class="btn ${btnClass}" style="padding:6px 18px">${confirmLabel || "Confirm"}</button>
                </div>
            </div>`;

        document.body.appendChild(overlay);
        overlay.querySelector("#modal-cancel").addEventListener("click", () => overlay.remove());
        overlay.querySelector("#modal-confirm").addEventListener("click", () => {
            overlay.remove();
            onConfirm();
        });
        // Click outside to dismiss
        overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
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
                el.statWinRate.textContent      = `${stats.win_rate}%`;
                el.statTotalSignals.textContent = stats.total_signals;
                el.statExecuted.textContent     = stats.executed_signals;
                el.statAvgPnl.textContent = `${stats.avg_pnl_pct >= 0 ? "+" : ""}${stats.avg_pnl_pct}%`;
            }
        } catch (e) {
            console.error("Stats load error: ", e);
        }
        // Always refresh the closed trade history table together with stats
        await loadTradeHistory();
    }

    // ---------------------------------------------------------------------------
    // Closed Trade History (Analytics tab table)
    // ---------------------------------------------------------------------------
    async function loadTradeHistory() {
        const tbody = document.getElementById("history-tbody");
        if (!tbody) return;
        try {
            const res  = await fetch("/api/positions/closed?limit=100");
            const data = await res.json();
            if (data.status !== "success") return;

            const trades = data.positions || [];
            if (trades.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No closed trades yet.</td></tr>`;
                return;
            }

            tbody.innerHTML = trades.map(t => {
                const entry  = parseFloat(t.entry_premium  || 0);
                const exit   = parseFloat(t.close_premium  || t.current_premium || entry);
                const qty    = parseInt(t.quantity || 1);
                const pnlPt  = exit - entry;
                const pnlInr = pnlPt * qty;
                const pnlPct = entry > 0 ? (pnlPt / entry * 100) : 0;
                const isWin  = pnlInr >= 0;

                // Exit reason badge class — DB stores close_reason
                const reason = (t.close_reason || "CLOSED").toUpperCase();
                const badgeCls = reason === "SL" ? "sl" : reason === "TARGET" ? "tgt" : reason === "MANUAL" ? "manual" : "";

                const closedAt = t.close_time || "—";

                return `<tr>
                    <td>${t.symbol || "—"}</td>
                    <td>${t.option_type || "—"}</td>
                    <td>₹${entry.toFixed(2)}</td>
                    <td>₹${exit.toFixed(2)}</td>
                    <td class="${isWin ? "pnl-profit" : "pnl-loss"}">${isWin ? "+" : ""}₹${pnlInr.toFixed(0)}</td>
                    <td class="${isWin ? "pnl-profit" : "pnl-loss"}">${isWin ? "+" : ""}${pnlPct.toFixed(1)}%</td>
                    <td><span class="badge-exit ${badgeCls}">${reason}</span></td>
                    <td>${closedAt}</td>
                </tr>`;
            }).join("");

        } catch (e) {
            console.error("Trade history load error:", e);
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

    // ---------------------------------------------------------------------------
    // TradingView link helpers
    // ---------------------------------------------------------------------------

    // Your saved TradingView layout ID — all your indicators and settings
    // are stored in this chart. The ?symbol= parameter switches the instrument
    // on that layout without opening a blank chart.
    const TV_CHART_ID = "H8qWRNcF";

    // Symbol name as TradingView expects it on your saved layout.
    // For NSE indices, the raw name (NIFTY, BANKNIFTY etc.) works directly.
    const TV_SYMBOLS = {
        "NIFTY":      "NIFTY",
        "BANKNIFTY":  "BANKNIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "FINNIFTY":   "FINNIFTY",
        "SENSEX":     "SENSEX"
    };

    /**
     * Build the TradingView URL pointing to your saved chart layout (H8qWRNcF)
     * with the underlying symbol pre-selected.
     * e.g. https://www.tradingview.com/chart/H8qWRNcF/?symbol=NIFTY
     */
    function tvUrl(underlying) {
        if (!underlying) return "#";
        const sym = TV_SYMBOLS[underlying.toUpperCase()] || underlying.toUpperCase();
        return "https://www.tradingview.com/chart/" + TV_CHART_ID + "/?symbol=" + encodeURIComponent(sym);
    }

    /**
     * Render a small TradingView icon-link as an HTML string.
     * Uses an inline SVG of the TradingView logo (the "TV" chart icon)
     * so no external image request is needed and it respects the dark theme.
     */
    function tvLink(underlying) {
        if (!underlying) return "";
        const url  = tvUrl(underlying);
        const name = (underlying || "").toUpperCase();
        return (
            '<a href="' + url + '" target="_blank" rel="noopener noreferrer" ' +
            'class="tv-link" title="Open ' + name + ' on TradingView">' +
            '<svg class="tv-icon" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">' +
            '<rect width="28" height="28" rx="5" fill="#131722"/>' +
            '<path fill="#2962FF" d="M4 8h20v2H4zM4 13h8v2H4zM4 18h8v2H4z"/>' +
            '<path fill="#2962FF" d="M17 13h7l-3.5 8z"/>' +
            '</svg>' +
            '<span class="tv-label">' + name + '</span>' +
            '</a>'
        );
    }

    // ---------------------------------------------------------------------------
    // Helper functions
    // ---------------------------------------------------------------------------
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
    loadRiskStatus();

    // Refresh button on analytics tab
    const btnHistoryRefresh = document.getElementById("btn-history-refresh");
    if (btnHistoryRefresh) {
        btnHistoryRefresh.addEventListener("click", loadTradeHistory);
    }

    // ── Fast loop: positions + spot tickers (5 s) ─────────────────────────
    // Only calls fast endpoints (positions DB read, market-pulse spot LTP).
    // Greeks and risk-status are NOT called here — they have their own timer.
    mainUpdateIntervalId = setInterval(() => {
        loadPositions();   // DB read — fast
        loadIndexTicks();  // spot LTP only — fast
        if (activeTab === "tab-chain") {
            loadOptionChain();
        } else if (activeTab === "tab-logs") {
            loadLogs();
        }
    }, 5000);

    // ── Slow loop: risk status + portfolio greeks (30 s) ──────────────────
    // These hit the broker API or compute from multiple positions.
    // Running them every 30 s is plenty for a risk panel.
    setInterval(() => {
        loadRiskStatus();
        updatePortfolioGreeks();
    }, 30000);
});
