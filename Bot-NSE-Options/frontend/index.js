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

    // Order Mode Buttons & Sync
    const btnManual = document.getElementById("btn-mode-manual");
    const btnAuto = document.getElementById("btn-mode-auto");

    async function setOrderMode(mode) {
        if (mode === "auto") {
            if (!confirm("Enable Auto Order Mode? Orders will be placed automatically for every new scan signal without manual confirmation.")) return;
        }
        if (btnManual) btnManual.classList.toggle("active", mode === "manual");
        if (btnAuto) btnAuto.classList.toggle("active", mode === "auto");

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
            console.error("Failed to update order mode:", e);
        }
    }

    if (btnManual) btnManual.addEventListener("click", () => setOrderMode("manual"));
    if (btnAuto) btnAuto.addEventListener("click", () => setOrderMode("auto"));

    // Sync initial Order Mode state from backend config
    fetch("/api/config").then(res => res.json()).then(cfg => {
        const mode = cfg.trading?.order_mode || "manual";
        if (btnManual) btnManual.classList.toggle("active", mode === "manual");
        if (btnAuto) btnAuto.classList.toggle("active", mode === "auto");
    // Bind Close All Positions button
    const btnCloseAllHeader = document.getElementById("btn-close-all-positions");
    if (btnCloseAllHeader) btnCloseAllHeader.addEventListener("click", closeAllPositions);

    // Symbol Search Filters — Fixed HTML Element IDs
    document.getElementById("buy-search").addEventListener("input", (e) => {
        filterTable("buy-signals-table", currentScanData.buy_results, e.target.value.toLowerCase(), "BUY");
    });

    document.getElementById("sell-search").addEventListener("input", (e) => {
        filterTable("sell-signals-table", currentScanData.sell_results, e.target.value.toLowerCase(), "SELL");
    });

    // Quick Filter Switches (+ [Sprint-1] risk guardrail toggles + [Sprint-2] signal-quality)
    const filterSwitches = [
        "dash-ut-enabled", "dash-sr-enabled",
        "dash-dedup-enabled", "dash-directional-gate", "dash-market-hours", "dash-daily-loss",
        "dash-atr-filter", "dash-adx-filter", "dash-spread-filter", "dash-consecutive-loss",
        // [Sprint-3] Position-sizing toggles
        "dash-position-sizing", "dash-grade-multiplier",
        // [Sprint-4] Alpha-enhancer toggles
        "dash-alpha-master", "dash-vix-regime", "dash-session-weight",
        "dash-volume-profile", "dash-greeks-filter", "dash-strict-mtf"
    ];
    filterSwitches.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", updateQuickFilters);
    });

    // [Sprint-1] Kill switch button
    const ksBtn = document.getElementById("btn-kill-switch");
    if (ksBtn) ksBtn.addEventListener("click", toggleKillSwitch);

    // [Sprint-1] Risk settings form
    const riskForm = document.getElementById("form-risk-settings");
    if (riskForm) riskForm.addEventListener("submit", saveRiskSettings);

    // [Sprint-2] Signal-quality settings form
    const sqForm = document.getElementById("form-sq-settings");
    if (sqForm) sqForm.addEventListener("submit", saveSignalQualitySettings);

    // [Sprint-3] Position-sizing settings form
    const psForm = document.getElementById("form-position-sizing");
    if (psForm) psForm.addEventListener("submit", savePositionSizingSettings);

    // [Sprint-4] Alpha-enhancer settings form
    const aeForm = document.getElementById("form-alpha-enhancers");
    if (aeForm) aeForm.addEventListener("submit", saveAlphaEnhancerSettings);

    // [Sprint-5] System health card + admin actions
    const sysForm = document.getElementById("form-sys-settings");
    if (sysForm) sysForm.addEventListener("submit", saveSystemSettings);
    const btnSysRefresh = document.getElementById("btn-sys-refresh");
    if (btnSysRefresh) btnSysRefresh.addEventListener("click", refreshSystemHealth);
    const btnSysDry = document.getElementById("btn-sys-reconcile-dry");
    if (btnSysDry) btnSysDry.addEventListener("click", () => runReconcile(true));
    const btnSysRun = document.getElementById("btn-sys-reconcile-run");
    if (btnSysRun) btnSysRun.addEventListener("click", () => runReconcile(false));
    // Pull system health once on load so the card shows real data.
    refreshSystemHealth();

    // Initial Setup
    loadOptionsGrid();
    loadConfig();
    loadIndices();
    runScan();
    startAutoRefresh();

    // [Sprint-1] Risk manager: initial status + load settings + polling every 5s
    refreshRiskStatus();
    loadRiskConfigIntoForm();
    _riskStatusInterval = setInterval(refreshRiskStatus, 5000);

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
        // [Sprint-1] Guardrail toggles
        dedup_enabled: document.getElementById("dash-dedup-enabled")?.checked ?? true,
        directional_gate_enabled: document.getElementById("dash-directional-gate")?.checked ?? true,
        market_hours_enabled: document.getElementById("dash-market-hours")?.checked ?? true,
        daily_loss_limit_enabled: document.getElementById("dash-daily-loss")?.checked ?? true,
        // [Sprint-2] Signal-quality + circuit-breaker toggles
        atr_filter_enabled: document.getElementById("dash-atr-filter")?.checked ?? true,
        adx_filter_enabled: document.getElementById("dash-adx-filter")?.checked ?? true,
        spread_filter_enabled: document.getElementById("dash-spread-filter")?.checked ?? true,
        consecutive_loss_breaker_enabled: document.getElementById("dash-consecutive-loss")?.checked ?? true,
        // [Sprint-3] Position-sizing toggles
        position_sizing_enabled: document.getElementById("dash-position-sizing")?.checked ?? true,
        grade_multiplier_enabled: document.getElementById("dash-grade-multiplier")?.checked ?? false,
        // [Sprint-4] Alpha-enhancer toggles
        alpha_enhancers_enabled: document.getElementById("dash-alpha-master")?.checked ?? true,
        vix_regime_enabled: document.getElementById("dash-vix-regime")?.checked ?? true,
        session_weighting_enabled: document.getElementById("dash-session-weight")?.checked ?? true,
        volume_profile_enabled: document.getElementById("dash-volume-profile")?.checked ?? true,
        greeks_filter_enabled: document.getElementById("dash-greeks-filter")?.checked ?? true,
        strict_mtf_enabled: document.getElementById("dash-strict-mtf")?.checked ?? false
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

/* ================================================================
   [Sprint-1] Risk Manager — Kill Switch, Status Strip, Settings
   ================================================================ */

let _riskStatusInterval = null;

async function toggleKillSwitch() {
    try {
        const statusRes = await fetch("/api/risk/status");
        const status = await statusRes.json();
        const newState = !status.kill_switch;
        const confirmMsg = newState
            ? "⚠️ ENABLE KILL SWITCH?\nThis will block ALL new automated orders immediately.\nExisting positions will still be monitored."
            : "Disable Kill Switch and resume automated trading?";
        if (!confirm(confirmMsg)) return;

        const res = await fetch("/api/kill-switch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: newState })
        });
        const data = await res.json();
        console.log("Kill switch:", data);
        refreshRiskStatus();
    } catch (err) {
        alert("Failed to toggle kill switch: " + err);
    }
}

async function refreshRiskStatus() {
    try {
        const res = await fetch("/api/risk/status");
        if (!res.ok) return;
        const s = await res.json();

        const strip = document.getElementById("risk-status-strip");
        const badge = document.getElementById("risk-trading-badge");
        const ksBtn = document.getElementById("btn-kill-switch");
        const ksLabel = document.getElementById("btn-kill-switch-label");
        const pnlEl = document.getElementById("risk-day-pnl");
        const realEl = document.getElementById("risk-realized-pnl");
        const unrealEl = document.getElementById("risk-unrealized-pnl");
        const mktEl = document.getElementById("risk-market-state");
        if (!strip || !badge) return;

        if (s.trading_allowed) {
            badge.textContent = "ALLOWED";
            badge.className = "risk-status-badge ok";
            strip.classList.remove("halted");
        } else {
            const reason = s.kill_switch ? "KILL SWITCH ON"
                        : !s.market_hours_ok ? s.market_hours_reason
                        : !s.daily_loss_ok ? s.daily_loss_reason
                        : "HALTED";
            badge.textContent = reason;
            badge.className = "risk-status-badge blocked";
            strip.classList.add("halted");
        }

        const pnl = s.day_pnl || {};
        const total = pnl.total_pnl || 0;
        const pct = pnl.pct_of_equity || 0;
        const totCls = total >= 0 ? "text-buy" : "text-sell";
        pnlEl.innerHTML = `<span class="${totCls}">₹${total.toFixed(2)} (${pct.toFixed(2)}%)</span>`;
        realEl.textContent = `₹${(pnl.realized_pnl || 0).toFixed(2)}`;
        unrealEl.textContent = `₹${(pnl.unrealized_pnl || 0).toFixed(2)}`;
        mktEl.textContent = s.market_hours_ok ? "OPEN" : (s.market_hours_reason || "CLOSED");

        if (s.kill_switch) {
            ksLabel.textContent = "KILL SWITCH: ON";
            ksBtn.classList.add("active");
        } else {
            ksLabel.textContent = "KILL SWITCH: OFF";
            ksBtn.classList.remove("active");
        }

        // [Sprint-2] Circuit-breaker status pill
        const cbEl = document.getElementById("risk-cb-status");
        if (cbEl) {
            const cb = s.circuit_breaker || {};
            if (cb.tripped) {
                cbEl.textContent = `CB TRIPPED (${cb.streak}/${cb.max_losses})`;
                cbEl.className = "risk-status-badge blocked";
            } else if (cb.streak > 0) {
                cbEl.textContent = `Streak ${cb.streak}/${cb.max_losses}`;
                cbEl.className = "risk-status-badge warn";
            } else {
                cbEl.textContent = "CB: OK";
                cbEl.className = "risk-status-badge ok";
            }
        }

        // [Sprint-3] Exposure + concurrent-positions pills
        const ps = s.position_sizing || {};
        const expEl = document.getElementById("risk-exposure-status");
        if (expEl) {
            const cap = Number(ps.max_portfolio_exposure_pct ?? 15);
            const cur = Number(ps.exposure_pct ?? 0);
            expEl.textContent = `${cur.toFixed(1)}% / ${cap.toFixed(0)}%`;
            expEl.title = `Open premium ₹${Math.round(ps.total_premium || 0).toLocaleString()} of ₹${Math.round(ps.equity || 0).toLocaleString()}`;
            if (cur >= cap) expEl.className = "risk-status-badge blocked";
            else if (cur >= cap * 0.8) expEl.className = "risk-status-badge warn";
            else expEl.className = "risk-status-badge ok";
        }
        const posEl = document.getElementById("risk-positions-status");
        if (posEl) {
            const capN = Number(ps.max_concurrent_positions ?? 3);
            const curN = Number(ps.open_positions ?? 0);
            posEl.textContent = `${curN} / ${capN}`;
            if (curN >= capN) posEl.className = "risk-status-badge blocked";
            else if (curN >= Math.max(1, capN - 1)) posEl.className = "risk-status-badge warn";
            else posEl.className = "risk-status-badge ok";
        }

        // [Sprint-4] VIX-regime + session pills
        const ae = s.alpha_enhancers || {};
        const regEl = document.getElementById("risk-regime-status");
        if (regEl) {
            const regime = String(ae.regime || "UNKNOWN");
            const vix = Number(ae.vix || 0);
            const mult = Number(ae.regime_multiplier || 1);
            regEl.textContent = vix > 0 ? `${regime} · VIX ${vix.toFixed(1)}` : regime;
            regEl.title = `Risk multiplier: ${mult.toFixed(2)}× (LOW boosts, HIGH cuts)`;
            if (regime === "HIGH") regEl.className = "risk-status-badge blocked";
            else if (regime === "LOW") regEl.className = "risk-status-badge warn";
            else regEl.className = "risk-status-badge ok";
        }
        const sesEl = document.getElementById("risk-session-status");
        if (sesEl) {
            const bucket = String(ae.session || "prime");
            const bonus = Number(ae.session_bonus || 0);
            sesEl.textContent = bonus !== 0 ? `${bucket} (${bonus > 0 ? "+" : ""}${bonus})` : bucket;
            sesEl.title = `Session score modifier: ${bonus > 0 ? "+" : ""}${bonus}`;
            if (bucket === "prime") sesEl.className = "risk-status-badge ok";
            else if (bucket === "off") sesEl.className = "risk-status-badge blocked";
            else sesEl.className = "risk-status-badge warn";
        }
    } catch (e) {
        console.debug("risk status refresh error:", e);
    }
}

async function saveRiskSettings(e) {
    e.preventDefault();
    const payload = {
        account_equity: parseFloat(document.getElementById("cfg-risk-equity").value) || 100000,
        daily_loss_max_pct: parseFloat(document.getElementById("cfg-risk-dloss-pct").value) || 3.0,
        daily_loss_auto_square_off: document.getElementById("cfg-risk-dloss-auto").value === "true",
        min_grade: document.getElementById("cfg-risk-min-grade").value || "B",
        min_score: parseFloat(document.getElementById("cfg-risk-min-score").value) || 60,
        dedup_cooldown_minutes: parseInt(document.getElementById("cfg-risk-cooldown").value) || 5,
        entry_cutoff_time: document.getElementById("cfg-risk-entry-cutoff").value || "14:45",
        market_open: document.getElementById("cfg-risk-mkt-open").value || "09:15",
        market_close: document.getElementById("cfg-risk-mkt-close").value || "15:30",
        // [Sprint-2] Consecutive-loss breaker
        consecutive_loss_max: parseInt(document.getElementById("cfg-risk-cbrk-max")?.value) || 3,
        consecutive_loss_cooldown_min: parseInt(document.getElementById("cfg-risk-cbrk-cooldown")?.value) || 30
    };
    try {
        const res = await fetch("/api/risk/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message || "Risk settings saved");
        refreshRiskStatus();
    } catch (err) {
        alert("Failed to save risk settings: " + err);
    }
}

async function loadRiskConfigIntoForm() {
    try {
        const res = await fetch("/api/config");
        const cfg = await res.json();
        const risk = cfg.risk || {};
        const dll = risk.daily_loss_limit || {};
        const trading = cfg.trading || {};
        const dedup = trading.dedup || {};
        const bot = cfg.bot || {};
        const dg = trading.directional_gate || {};

        const set = (id, v) => { const el = document.getElementById(id); if (el != null && v !== undefined && v !== null) el.value = v; };
        const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };

        set("cfg-risk-equity", risk.account_equity ?? 100000);
        set("cfg-risk-dloss-pct", dll.max_loss_pct ?? 3.0);
        set("cfg-risk-dloss-auto", (dll.auto_square_off ?? true) ? "true" : "false");
        set("cfg-risk-min-grade", trading.min_grade ?? "B");
        set("cfg-risk-min-score", trading.min_score ?? 60);
        set("cfg-risk-cooldown", dedup.cooldown_minutes ?? 5);
        set("cfg-risk-mkt-open", bot.market_open ?? "09:15");
        set("cfg-risk-mkt-close", bot.market_close ?? "15:30");
        set("cfg-risk-entry-cutoff", bot.entry_cutoff_time ?? "14:45");

        setChk("dash-dedup-enabled", dedup.enabled ?? true);
        setChk("dash-directional-gate", dg.enabled ?? true);
        setChk("dash-market-hours", bot.market_hours_check ?? true);
        setChk("dash-daily-loss", dll.enabled ?? true);

        // [Sprint-2] Load signal-quality + circuit-breaker fields
        const sq = cfg.signal_quality || {};
        const cbrk = risk.consecutive_loss_breaker || {};
        set("cfg-sq-scoring-enabled", (sq.scoring_enabled ?? true) ? "true" : "false");
        set("cfg-sq-adx-min", sq.adx_min ?? 20);
        set("cfg-sq-atr-min", sq.atr_pct_min ?? 0.5);
        set("cfg-sq-atr-max", sq.atr_pct_max ?? 8.0);
        set("cfg-sq-spread-max", sq.max_spread_pct ?? 1.5);
        set("cfg-sq-min-oi", sq.min_open_interest ?? 500);
        set("cfg-risk-cbrk-max", cbrk.max_losses ?? 3);
        set("cfg-risk-cbrk-cooldown", cbrk.cooldown_minutes ?? 30);
        setChk("dash-atr-filter", sq.atr_filter_enabled ?? true);
        setChk("dash-adx-filter", sq.adx_filter_enabled ?? true);
        setChk("dash-spread-filter", sq.spread_filter_enabled ?? true);
        setChk("dash-consecutive-loss", cbrk.enabled ?? true);

        // [Sprint-3] Load position-sizing fields + toggles
        const ps = cfg.position_sizing || {};
        set("cfg-ps-mode", ps.mode ?? "fixed_fractional");
        set("cfg-ps-risk-pct", ps.risk_per_trade_pct ?? 1.0);
        set("cfg-ps-max-risk-pct", ps.max_risk_per_trade_pct ?? 3.0);
        set("cfg-ps-max-exposure", ps.max_portfolio_exposure_pct ?? 15);
        set("cfg-ps-max-positions", ps.max_concurrent_positions ?? 3);
        set("cfg-ps-kelly-frac", ps.kelly_fraction ?? 0.25);
        set("cfg-ps-kelly-min", ps.kelly_min_trades ?? 20);
        setChk("dash-position-sizing", ps.enabled ?? true);
        setChk("dash-grade-multiplier", ps.grade_multiplier_enabled ?? false);

        // [Sprint-4] Load alpha-enhancer fields + toggles
        const ae = cfg.alpha_enhancers || {};
        const vr = ae.vix_regime || {};
        const vm = vr.risk_multipliers || {};
        const sw = ae.session_weighting || {};
        const sb = sw.bonuses || {};
        const vp = ae.volume_profile || {};
        const gk = ae.greeks || {};
        const sm = ae.strict_mtf || {};
        set("cfg-ae-vix-low", vr.low_threshold ?? 15.0);
        set("cfg-ae-vix-high", vr.high_threshold ?? 22.0);
        set("cfg-ae-mult-low", vm.LOW ?? 1.10);
        set("cfg-ae-mult-normal", vm.NORMAL ?? 1.00);
        set("cfg-ae-mult-high", vm.HIGH ?? 0.60);
        set("cfg-ae-open-mins", sw.opening_minutes ?? 30);
        set("cfg-ae-close-mins", sw.closing_minutes ?? 30);
        set("cfg-ae-bonus-opening", sb.opening ?? -5.0);
        set("cfg-ae-bonus-prime", sb.prime ?? 5.0);
        set("cfg-ae-bonus-closing", sb.closing ?? -10.0);
        set("cfg-ae-poc-dist", vp.max_poc_distance_pct ?? 1.5);
        set("cfg-ae-min-delta", gk.min_abs_delta ?? 0.20);
        set("cfg-ae-max-theta", gk.max_theta_pct ?? 5.0);
        set("cfg-ae-mtf-tfs", (sm.required_timeframes || ["5m", "15m"]).join(","));
        setChk("dash-alpha-master", ae.enabled ?? true);
        setChk("dash-vix-regime", vr.enabled ?? true);
        setChk("dash-session-weight", sw.enabled ?? true);
        setChk("dash-volume-profile", vp.enabled ?? true);
        setChk("dash-greeks-filter", gk.enabled ?? true);
        setChk("dash-strict-mtf", sm.enabled ?? false);
    } catch (e) {
        console.debug("loadRiskConfigIntoForm error:", e);
    }
}

/* ================================================================
   [Sprint-2] Signal-Quality Settings Form
   ================================================================ */
async function saveSignalQualitySettings(e) {
    e.preventDefault();
    const payload = {
        scoring_enabled: document.getElementById("cfg-sq-scoring-enabled").value === "true",
        atr_pct_min: parseFloat(document.getElementById("cfg-sq-atr-min").value) || 0.5,
        atr_pct_max: parseFloat(document.getElementById("cfg-sq-atr-max").value) || 8.0,
        adx_min: parseFloat(document.getElementById("cfg-sq-adx-min").value) || 20.0,
        max_spread_pct: parseFloat(document.getElementById("cfg-sq-spread-max").value) || 1.5,
        min_open_interest: parseInt(document.getElementById("cfg-sq-min-oi").value) || 500
    };
    try {
        const res = await fetch("/api/signal-quality/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message || "Signal-quality settings saved");
    } catch (err) {
        alert("Failed to save signal-quality settings: " + err);
    }
}


/* ================================================================
   [Sprint-3] Position-Sizing Settings Form
   ================================================================ */
async function savePositionSizingSettings(e) {
    e.preventDefault();
    const payload = {
        mode: document.getElementById("cfg-ps-mode").value || "fixed_fractional",
        risk_per_trade_pct: parseFloat(document.getElementById("cfg-ps-risk-pct").value) || 1.0,
        max_risk_per_trade_pct: parseFloat(document.getElementById("cfg-ps-max-risk-pct").value) || 3.0,
        max_portfolio_exposure_pct: parseFloat(document.getElementById("cfg-ps-max-exposure").value) || 15.0,
        max_concurrent_positions: parseInt(document.getElementById("cfg-ps-max-positions").value) || 3,
        kelly_fraction: parseFloat(document.getElementById("cfg-ps-kelly-frac").value) || 0.25,
        kelly_min_trades: parseInt(document.getElementById("cfg-ps-kelly-min").value) || 20
    };
    try {
        const res = await fetch("/api/position-sizing/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message || "Position-sizing settings saved");
        refreshRiskStatus();
    } catch (err) {
        alert("Failed to save position-sizing settings: " + err);
    }
}


/* ================================================================
   [Sprint-4] Alpha-Enhancer Settings Form
   ================================================================ */
async function saveAlphaEnhancerSettings(e) {
    e.preventDefault();
    const payload = {
        vix_low_threshold: parseFloat(document.getElementById("cfg-ae-vix-low").value) || 15.0,
        vix_high_threshold: parseFloat(document.getElementById("cfg-ae-vix-high").value) || 22.0,
        vix_low_multiplier: parseFloat(document.getElementById("cfg-ae-mult-low").value) || 1.10,
        vix_normal_multiplier: parseFloat(document.getElementById("cfg-ae-mult-normal").value) || 1.00,
        vix_high_multiplier: parseFloat(document.getElementById("cfg-ae-mult-high").value) || 0.60,
        session_opening_minutes: parseInt(document.getElementById("cfg-ae-open-mins").value) || 30,
        session_closing_minutes: parseInt(document.getElementById("cfg-ae-close-mins").value) || 30,
        session_opening_bonus: parseFloat(document.getElementById("cfg-ae-bonus-opening").value) || -5.0,
        session_prime_bonus: parseFloat(document.getElementById("cfg-ae-bonus-prime").value) || 5.0,
        session_closing_bonus: parseFloat(document.getElementById("cfg-ae-bonus-closing").value) || -10.0,
        poc_max_distance_pct: parseFloat(document.getElementById("cfg-ae-poc-dist").value) || 1.5,
        greeks_min_abs_delta: parseFloat(document.getElementById("cfg-ae-min-delta").value) || 0.20,
        greeks_max_theta_pct: parseFloat(document.getElementById("cfg-ae-max-theta").value) || 5.0,
        strict_mtf_timeframes: document.getElementById("cfg-ae-mtf-tfs").value || "5m,15m"
    };
    try {
        const res = await fetch("/api/alpha/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message || "Alpha-enhancer settings saved");
        refreshRiskStatus();
    } catch (err) {
        alert("Failed to save alpha-enhancer settings: " + err);
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
        // [Sprint-2] Extend confluence pills with MTF; ATR/ADX pills use r.atr_pct / r.adx
        const confHtml = `
            <div class="confluence-icons">
                <span class="icon-tag ${c.ema ? 'active' : ''}">EMA</span>
                <span class="icon-tag ${c.rsi ? 'active' : ''}">RSI</span>
                <span class="icon-tag ${c.vol ? 'active' : ''}">Vol</span>
                <span class="icon-tag ${c.sr ? 'active' : ''}">S/R</span>
                <span class="icon-tag ${c.mtf ? 'active' : ''}">MTF</span>
                <span class="icon-tag ${c.sqz ? 'active' : ''}">Sqz</span>
            </div>
        `;
        // [Sprint-2] Build score-breakdown tooltip for the grade badge
        const bd = r.score_breakdown || {};
        const bdTooltip = Object.keys(bd).length
            ? "Score breakdown:\n" + Object.entries(bd).map(([k, v]) =>
                `  ${k.padEnd(8)} ${v.earned.toFixed(1)}/${v.weight.toFixed(0)}  ${v.pass ? '✓' : '✗'}  (${v.detail})`
              ).join("\n")
            : "";
        const sqReject = r.sq_reject_reason ? `\n⚠ Rejected: ${r.sq_reject_reason}` : "";
        const atrAdxHtml = (r.atr_pct !== undefined || r.adx !== undefined)
            ? `<div class="sq-metrics" style="font-size:0.7rem;color:var(--text-secondary);margin-top:2px;">ATR ${(r.atr_pct||0).toFixed(2)}% · ADX ${(r.adx||0).toFixed(0)}</div>`
            : "";
        // [Sprint-3] Position-sizing display: computed qty + risk₹ + mode
        const ps = r.position_sizing || {};
        const psQty = Number(r.sized_quantity ?? 0);
        const psRiskAmt = Number(ps.risk_amount ?? 0);
        const psRiskPct = Number(ps.risk_pct ?? 0);
        const psMode = ps.mode || "disabled";
        const psTooltip = `Position sizing (${psMode}):\n  Qty: ${psQty}\n  Risk: ₹${psRiskAmt.toFixed(0)} (${psRiskPct.toFixed(2)}% equity)\n  Reason: ${ps.reason || "-"}`;
        const psHtml = (psQty > 0)
            ? `<div class="ps-metrics" title="${psTooltip.replace(/"/g,'&quot;')}" style="font-size:0.7rem;color:var(--accent-blue);margin-top:2px;">Qty ${psQty} · ₹${psRiskAmt.toFixed(0)} risk (${psRiskPct.toFixed(2)}%)</div>`
            : (ps.reason && ps.reason !== "DISABLED_FALLBACK"
                ? `<div class="ps-metrics" style="font-size:0.7rem;color:var(--color-sell);margin-top:2px;">⚠ ${ps.reason}</div>`
                : "");
        // [Sprint-4] Alpha telemetry mini-badges: regime · session · POC-dist · delta
        const aeParts = [];
        if (r.regime && r.regime !== "DISABLED" && r.regime !== "UNKNOWN") aeParts.push(`${r.regime}`);
        if (r.session && r.session !== "prime") aeParts.push(`s:${r.session}`);
        if (r.poc_distance_pct !== undefined && Number(r.poc_distance_pct) > 0) aeParts.push(`POC ${Number(r.poc_distance_pct).toFixed(2)}%`);
        if (r.delta !== undefined && Math.abs(Number(r.delta)) > 0) aeParts.push(`Δ ${Number(r.delta).toFixed(2)}`);
        const aeReject = r.alpha_reject_reason ? ` · ⚠ ${r.alpha_reject_reason}` : "";
        const aeHtml = (aeParts.length || aeReject)
            ? `<div class="ae-metrics" title="Alpha telemetry: regime · session · POC-distance · delta" style="font-size:0.7rem;color:${r.alpha_reject_reason ? 'var(--color-sell)' : 'var(--text-secondary)'};margin-top:2px;">${aeParts.join(" · ")}${aeReject}</div>`
            : "";
        const qtyPrefill = psQty > 0 ? psQty : (r.lot_size || 65);
        return `
            <tr>
                <td style="font-weight: 700; font-family: var(--font-mono);">${r.symbol}</td>
                <td style="font-weight: 700; color: var(--accent-blue);">₹${r.price.toFixed(2)}</td>
                <td style="font-family: var(--font-mono);">${r.win_rate}</td>
                <td>
                    <div class="score-container">
                        <span class="grade-badge grade-${r.grade.toLowerCase()}" title="${(bdTooltip + sqReject).replace(/"/g, '&quot;')}">${r.grade} ${r.setup_score}</span>
                        <button class="btn-analyze" onclick="openTradingViewChart('${r.symbol}', '${r.underlying}')" title="View ${r.symbol} chart on TradingView">
                            <i class="fa-solid fa-chart-line"></i>
                        </button>
                        <button class="btn-analyze" onclick="openTradingViewChart('', '${r.underlying}')" title="Open Underlying Index (${r.underlying}) Chart on TradingView" style="opacity: 0.6; margin-left: 2px;">
                            <i class="fa-solid fa-chart-area"></i>
                        </button>
                    </div>
                    ${atrAdxHtml}
                    ${psHtml}
                    ${aeHtml}
                </td>
                <td>${confHtml}</td>
                <td>
                    <div class="action-cell">
                        <div class="order-qty-wrap">
                            <span class="order-qty-label">QTY</span>
                            <input type="number" class="order-qty-input" value="${qtyPrefill}" min="1" step="1">
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

async function closeAllPositions() {
    const btn = document.getElementById("btn-close-all-positions");
    if (!confirm("⚠️ Are you sure you want to CLOSE ALL active open positions immediately?")) return;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Closing All...';
    }
    try {
        const res = await fetch("/api/positions/close-all", { method: "POST" });
        const data = await res.json();
        alert(data.message || "Successfully closed positions.");
        loadPositions();
    } catch (err) {
        alert("Failed to close all positions");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-square-xmark"></i> Close All Positions';
        }
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


// ============================================================================
// [Sprint-5] Production-Hardening dashboard wiring
// ============================================================================

function _sysStatusBadge(status) {
    const colors = { ok: "var(--color-buy)", degraded: "#f5a623", down: "var(--color-sell)" };
    const c = colors[status] || "var(--text-secondary)";
    return `<span style="color:${c}; font-weight:700; text-transform:uppercase;">${status || "?"}</span>`;
}

function _setSysText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

async function refreshSystemHealth() {
    try {
        const res = await fetch("/api/health");
        const data = await res.json();

        const overallEl = document.getElementById("sys-health-status");
        if (overallEl) overallEl.innerHTML = _sysStatusBadge(data.status);

        _setSysText("sys-health-uptime", data.uptime_human || `${data.uptime_seconds || 0}s`);

        const checks = data.checks || {};
        const b = checks.broker || {};
        _setSysText("sys-health-broker",
            b.reachable ? `OK (${b.latency_ms || "?"}ms)` : `DOWN (${b.detail || "?"})`);

        const db = checks.database || {};
        _setSysText("sys-health-db",
            db.reachable ? `OK (${db.open_positions || 0} open)` : "UNREACHABLE");

        const staleCount = (db.stale_positions || 0);
        const staleEl = document.getElementById("sys-health-stale");
        if (staleEl) {
            staleEl.textContent = `${staleCount} (>${db.stale_cutoff_hours || 24}h)`;
            staleEl.style.color = staleCount > 0 ? "#f5a623" : "var(--text-primary)";
        }

        const lg = checks.logging || {};
        _setSysText("sys-health-logsize", lg.exists ? `${lg.size_mb || 0} MB` : "no file");

        const dk = checks.disk || {};
        _setSysText("sys-health-disk",
            dk.reachable ? `${dk.free_gb || 0} GB free (${dk.used_pct || 0}% used)` : "n/a");

        const cfgCheck = checks.config || {};
        const src = cfgCheck.secret_sources || {};
        const envCount = Object.values(src).filter(v => v === "env").length;
        const cfgCount = Object.values(src).filter(v => v === "config").length;
        _setSysText("sys-health-secrets", `env: ${envCount}, config: ${cfgCount}`);

        _prefillSystemForm();
    } catch (err) {
        console.error("[Sprint-5] health fetch error:", err);
        _setSysText("sys-health-status", "ERROR");
    }
}

async function _prefillSystemForm() {
    if (window.__sprint5FormPrefilled) return;
    try {
        const res = await fetch("/api/config");
        const cfg = await res.json();
        const bot = cfg.bot || {};
        const retry = bot.retry || {};
        const rl = bot.rate_limit || {};

        const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        setVal("cfg-sys-log-mb", Math.max(1, Math.round((bot.log_max_bytes || 10485760) / (1024 * 1024))));
        setVal("cfg-sys-log-backups", bot.log_backup_count ?? 5);
        setVal("cfg-sys-retry-attempts", retry.max_attempts ?? 3);
        setVal("cfg-sys-retry-backoff", retry.backoff_base_sec ?? 0.5);
        setVal("cfg-sys-rate-per-min", rl.enabled ? (rl.per_minute ?? 120) : 0);
        setVal("cfg-sys-stale-hours", bot.stale_position_cutoff_hours ?? 24);
        window.__sprint5FormPrefilled = true;
    } catch (err) {
        console.warn("[Sprint-5] prefill skipped:", err);
    }
}

async function saveSystemSettings(e) {
    e.preventDefault();
    const perMin = parseInt(document.getElementById("cfg-sys-rate-per-min").value) || 0;
    const payload = {
        bot: {
            log_max_bytes: Math.max(1, parseInt(document.getElementById("cfg-sys-log-mb").value) || 10) * 1024 * 1024,
            log_backup_count: parseInt(document.getElementById("cfg-sys-log-backups").value) || 5,
            stale_position_cutoff_hours: parseInt(document.getElementById("cfg-sys-stale-hours").value) || 24,
            retry: {
                enabled: true,
                max_attempts: parseInt(document.getElementById("cfg-sys-retry-attempts").value) || 3,
                backoff_base_sec: parseFloat(document.getElementById("cfg-sys-retry-backoff").value) || 0.5,
            },
            rate_limit: {
                enabled: perMin > 0,
                per_minute: perMin > 0 ? perMin : 120,
            },
        }
    };
    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        alert(data.message || "System settings saved. Restart the app for logging/rate-limit changes to take full effect.");
        refreshSystemHealth();
    } catch (err) {
        alert("Failed to save system settings: " + err);
    }
}

async function runReconcile(dryRun) {
    const hours = parseInt(document.getElementById("cfg-sys-stale-hours")?.value) || 24;
    const label = dryRun ? "preview" : "reconcile";
    if (!dryRun && !confirm(`Close ALL OPEN positions older than ${hours} hours?\nThis marks them CLOSED with exit_reason='STALE_RECONCILE' (zero PnL).`)) {
        return;
    }
    try {
        const res = await fetch("/api/admin/reconcile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cutoff_hours: hours, dry_run: !!dryRun }),
        });
        const data = await res.json();
        const summary = `${label} complete\n` +
                        `status:     ${data.status}\n` +
                        `candidates: ${data.candidates}\n` +
                        `closed:     ${data.closed}\n` +
                        `errors:     ${(data.errors || []).length}`;
        alert(summary);
        refreshSystemHealth();
        if (typeof loadPositions === "function") loadPositions();
    } catch (err) {
        alert("Reconcile failed: " + err);
    }
}



// ===========================================================================
// [Sprint-6] Live Metrics & Watchdog — Settings tab observability card
// ===========================================================================

async function refreshMetrics() {
    try {
        const res = await fetch("/api/metrics/snapshot");
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const m = data.metrics || {};
        const wd = data.watchdog || {};

        const setText = (id, v) => {
            const el = document.getElementById(id);
            if (el) el.textContent = v;
        };

        // Watchdog block
        const state = (wd.state || "unknown").toUpperCase();
        setText("metric-wd-state", state);
        const el = document.getElementById("metric-wd-state");
        if (el) {
            el.style.color = state === "UP" ? "var(--accent-green, #4ade80)"
                          : state === "DOWN" ? "var(--accent-red, #f87171)"
                          : "var(--text-secondary)";
        }
        setText("metric-wd-latency",
            (wd.last_latency_ms != null) ? (wd.last_latency_ms + " ms") : "--");
        setText("metric-wd-fails", wd.consecutive_failures ?? 0);

        // ---- helper: sum counter values across all label buckets ----
        const sumSeries = (name, filterFn) => {
            const bucket = m[name];
            if (!bucket || !Array.isArray(bucket.series)) return 0;
            return bucket.series
                .filter(s => (typeof filterFn === "function") ? filterFn(s) : true)
                .reduce((acc, s) => acc + (Number(s.value) || 0), 0);
        };

        // Orders — split by outcome label
        const parseLabel = (labels, key) => {
            if (!labels) return "";
            const m2 = labels.match(new RegExp(key + '="([^"]*)"'));
            return m2 ? m2[1] : "";
        };
        const ordersOk   = sumSeries("orders_total", s => parseLabel(s.labels, "outcome") === "ok");
        const ordersFail = sumSeries("orders_total", s => parseLabel(s.labels, "outcome") === "fail");
        setText("metric-orders", `${ordersOk} / ${ordersFail}`);

        // Signals accepted
        const signalsAcc = sumSeries("signals_total", s => parseLabel(s.labels, "outcome") === "accepted");
        setText("metric-signals", signalsAcc);

        // Retry attempts avg (from histogram-lite: sum + count summed across ops)
        const retryHist = m["retry_attempts"];
        let retrySum = 0, retryCnt = 0;
        if (retryHist && Array.isArray(retryHist.series)) {
            for (const s of retryHist.series) {
                retrySum += Number(s.sum) || 0;
                retryCnt += Number(s.count) || 0;
            }
        }
        setText("metric-retry-avg", retryCnt ? (retrySum / retryCnt).toFixed(2) : "0.00");

        // Retries exhausted
        setText("metric-retry-exhausted", sumSeries("retry_exhausted_total"));

        // Rate-limit blocks
        setText("metric-ratelimit", sumSeries("ratelimit_blocks_total"));
    } catch (err) {
        console.warn("refreshMetrics failed:", err);
        const el = document.getElementById("metric-wd-state");
        if (el) { el.textContent = "ERR"; el.style.color = "var(--accent-red, #f87171)"; }
    }
}

// Wire the refresh button + piggy-back on the existing System Health refresh.
document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("btn-metrics-refresh");
    if (btn) btn.addEventListener("click", refreshMetrics);

    // Auto-refresh when the user opens the Settings tab.
    const tabBtn = document.querySelector('[data-tab="settings"], [href="#panel-settings"]');
    if (tabBtn) tabBtn.addEventListener("click", () => setTimeout(refreshMetrics, 200));

    // Kick once on load so numbers appear if Settings is already active.
    setTimeout(refreshMetrics, 800);
});

// If refreshSystemHealth exists, wrap it to also refresh metrics — non-invasive.
if (typeof refreshSystemHealth === "function" && !window.__sprint6MetricsWrapped) {
    const _origRefresh = refreshSystemHealth;
    refreshSystemHealth = async function () {
        try { await _origRefresh.apply(this, arguments); } finally {
            try { refreshMetrics(); } catch (_) { /* ignore */ }
        }
    };
    window.__sprint6MetricsWrapped = true;
}

