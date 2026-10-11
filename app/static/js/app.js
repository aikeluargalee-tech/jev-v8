// Jev v8 Pure Decision-Support Terminal Client Controller

let currentProfile = "SWING";
let plainEnglishEnabled = false;
let latestRecord = null;

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    await fetchSystemStatus();
    // Run initial evaluation on startup
    await runEvaluation();

    // Auto-refresh: Poll live ticker price every 15s, and check for fresh decision packets every 5 minutes
    setInterval(updateLiveTickerPrice, 15000);
    setInterval(loadStaticPacket, 300000);

    // Live countdown to next hourly cloud collection (:05)
    startNextRefreshCountdown();
}

function setupEventListeners() {
    // 1. Profile toggles
    const btnSwing = document.getElementById("btn-profile-swing");
    const btnDay = document.getElementById("btn-profile-day");
    const runBtn = document.getElementById("run-btn");
    const togglePlainBtn = document.getElementById("btn-toggle-plain");
    const calcToggle = document.getElementById("calc-toggle");
    const btnCalc = document.getElementById("btn-calc-size");

    btnSwing.addEventListener("click", () => {
        currentProfile = "SWING";
        btnSwing.classList.add("active");
        btnDay.classList.remove("active");
        runEvaluation();
    });

    btnDay.addEventListener("click", () => {
        currentProfile = "DAY_CONTEXT";
        btnDay.classList.add("active");
        btnSwing.classList.remove("active");
        runEvaluation();
    });

    // 2. Plain English toggle
    togglePlainBtn.addEventListener("click", () => {
        plainEnglishEnabled = !plainEnglishEnabled;
        document.getElementById("plain-english-state").textContent = plainEnglishEnabled ? "ON" : "OFF";
        document.querySelectorAll(".plain-english-text").forEach(el => {
            if (plainEnglishEnabled) el.classList.remove("hidden");
            else el.classList.add("hidden");
        });
    });

    // 3. Refresh Data Button
    runBtn.addEventListener("click", () => {
        runEvaluation();
    });

    // 3b. On-Demand Cloud Trigger Button
    const btnCloud = document.getElementById("btn-cloud-trigger");
    if (btnCloud) {
        btnCloud.addEventListener("click", () => {
            const proceed = window.confirm(
                "Trigger an on-demand cloud calculation?\n\n" +
                "Click OK to open GitHub Actions in a new tab where you can click 'Run workflow' to immediately fetch 300+ Kraken candles and recalculate the decision card (~35s)."
            );
            if (proceed) {
                window.open("https://github.com/aikeluargalee-tech/jev-v8/actions/workflows/collector.yml", "_blank", "noopener");
            }
        });
    }

    // 4. One-Number Copy Buttons (AC-08: Strictly one number per field, never a range)
    document.querySelectorAll(".copy-field-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const field = e.target.dataset.field;
            let val = "";
            if (field === "Entry" && latestRecord?.levels?.entry_zone) {
                // Copy entry base price as single number
                val = `Entry: $${latestRecord.market.last_price.toLocaleString()}`;
            } else if (field === "Stop" && latestRecord?.levels?.stop) {
                val = `Stop-Loss: $${latestRecord.levels.stop.price.toLocaleString()}`;
            } else if (field === "Target 1" && latestRecord?.levels?.target_primary) {
                val = `Target 1: $${latestRecord.levels.target_primary.toLocaleString()}`;
            } else if (field === "Target 2" && latestRecord?.levels?.target_extended) {
                val = `Target 2: $${latestRecord.levels.target_extended.toLocaleString()}`;
            }

            if (val) {
                navigator.clipboard.writeText(val).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = "✅ Copied!";
                    setTimeout(() => { btn.textContent = orig; }, 1800);
                });
            }
        });
    });

    // 5. Deliberate Risk Calculator Accordion
    calcToggle.addEventListener("click", () => {
        const body = document.getElementById("calc-body");
        const arrow = document.getElementById("calc-arrow");
        body.classList.toggle("collapsed");
        arrow.textContent = body.classList.contains("collapsed") ? "▼" : "▲";
    });

    // 6. Risk Calculation
    btnCalc.addEventListener("click", () => {
        const equity = parseFloat(document.getElementById("calc-equity").value) || 0;
        const riskPct = parseFloat(document.getElementById("calc-risk-pct").value) || 0;
        const stopPct = parseFloat(document.getElementById("calc-stop-pct").value) || 0;

        if (equity <= 0 || riskPct <= 0 || stopPct <= 0) {
            alert("Please fill in positive numbers for Equity, Risk %, and Stop-Loss %.");
            return;
        }

        const maxRiskDollar = (equity * (riskPct / 100.0));
        const posSizeDollar = (maxRiskDollar / (stopPct / 100.0));

        document.getElementById("calc-max-risk").textContent = `$${maxRiskDollar.toFixed(2)}`;
        document.getElementById("calc-pos-size").textContent = `$${posSizeDollar.toFixed(2)}`;
        document.getElementById("calc-result").classList.remove("hidden");
    });

    // 7. AC-10 Calibration Drawer Toggle
    const calToggle = document.getElementById("cal-toggle");
    if (calToggle) {
        calToggle.addEventListener("click", () => {
            const body = document.getElementById("cal-body");
            const arrow = document.getElementById("cal-arrow");
            body.classList.toggle("collapsed");
            arrow.textContent = body.classList.contains("collapsed") ? "▼" : "▲";
        });
    }

    // 8. Save Outcome Handler
    const btnSaveOutcome = document.getElementById("btn-save-outcome");
    if (btnSaveOutcome) {
        btnSaveOutcome.addEventListener("click", async () => {
            const runId = document.getElementById("outcome-run-id").value;
            const choice = document.getElementById("outcome-choice").value;
            const pnlVal = document.getElementById("outcome-pnl").value;
            const pnl = pnlVal ? parseFloat(pnlVal) : null;

            if (!runId || runId.trim() === "") {
                alert("No run ID available to record outcome for. Please run an evaluation first.");
                return;
            }

            try {
                btnSaveOutcome.disabled = true;
                btnSaveOutcome.textContent = "Saving...";
                const res = await fetch(`/api/v1/verdicts/${encodeURIComponent(runId)}/outcome`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        outcome: choice,
                        pnl_percent: pnl,
                        hit_target: choice === "WIN",
                        hit_stop: choice === "LOSS"
                    })
                });

                if (res.ok) {
                    const successMsg = document.getElementById("outcome-saved-msg");
                    successMsg.classList.remove("hidden");
                    setTimeout(() => { successMsg.classList.add("hidden"); }, 3000);
                    await fetchCalibrationReport();
                } else {
                    const err = await res.json();
                    alert(`Failed to save outcome: ${err.detail || "Server error"}`);
                }
            } catch (e) {
                console.error("Error saving outcome:", e);
                alert(`Error saving outcome: ${e.message}`);
            } finally {
                btnSaveOutcome.disabled = false;
                btnSaveOutcome.textContent = "Save Verified Outcome";
            }
        });
    }
}

async function fetchSystemStatus() {
    try {
        const res = await fetch("/api/v1/status");
        if (res.ok) {
            const data = await res.json();
            document.getElementById("shadow-run-count").textContent = data.total_logged_verdicts;
            document.getElementById("footer-cost").textContent = `$${data.monthly_cumulative_usd.toFixed(2)} / $${data.monthly_budget_usd.toFixed(2)}`;
        }
    } catch (e) {
        console.error("Failed to fetch system status:", e);
    }
}

async function runEvaluation() {
    const runBtn = document.getElementById("run-btn");
    const runText = document.getElementById("run-btn-text");
    const spinner = document.getElementById("run-spinner");

    runBtn.disabled = true;
    runText.textContent = "Loading...";
    spinner.classList.remove("hidden");

    let apiWorked = false;
    try {
        const res = await fetch("/api/v1/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: "BTC", profile: currentProfile })
        });

        if (res.ok) {
            const data = await res.json();
            latestRecord = data.record;
            renderRecord(data.record);
            await fetchSystemStatus();
            apiWorked = true;
        }
    } catch (err) {
        // Local API not reachable, proceed to static fallback
    }

    if (!apiWorked) {
        await loadStaticPacket();
    }

    runBtn.disabled = false;
    runText.textContent = `🔄 Refresh Data`;
    spinner.classList.add("hidden");
}

async function loadStaticPacket() {
    try {
        const res = await fetch("data/latest_decision.json?t=" + Date.now());
        if (!res.ok) throw new Error("Could not load latest_decision.json");
        const packet = await res.json();
        const rec = packet.profiles ? packet.profiles[currentProfile] : packet;
        latestRecord = rec;
        renderRecord(rec);

        if (packet.updated_at_myt) {
            document.getElementById("freshness-text").textContent = `Cloud Updated: ${packet.updated_at_myt}`;
        }

        document.getElementById("footer-run-id").textContent = rec.run_id;
        document.getElementById("footer-state-hash").textContent = rec.categorical ? rec.categorical.rule_ver : "--";
        document.getElementById("footer-cost").textContent = "Serverless Cloud ($0.00)";

        if (packet.calibration) {
            applyCalibrationReportData(packet.calibration);
        }

        updateLiveTickerPrice();
    } catch (err) {
        console.error("Static data load error:", err);
        renderErrorState("Could not load decision packet. Please check if data/latest_decision.json exists.");
    }
}

async function updateLiveTickerPrice() {
    try {
        const res = await fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD");
        if (res.ok) {
            const d = await res.json();
            const p = parseFloat(d.result.XXBTZUSD.c[0]);
            document.getElementById("market-price").textContent = `$${p.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        }
    } catch (e) {
        // Ticker fetch optional
    }
}

function renderRecord(rec) {
    const market = rec.market;
    const det = rec.deterministic;
    const features = rec.features;
    const levels = rec.levels;

    // 1. Header & Freshness
    document.getElementById("freshness-text").textContent = `Freshness: ${market.freshness_market_s}s`;
    document.getElementById("market-price").textContent = `$${market.last_price.toLocaleString()}`;
    document.getElementById("funding-rate").textContent = rec.categorical.derivatives_cat.funding_cat !== "unavailable" ? `${(rec.market.funding_rate_pct || 0).toFixed(4)}%` : "Unavailable";
    document.getElementById("rsi-val").textContent = features.rsi14 ? `${features.rsi14} (${features.rsi_dir})` : "--";
    document.getElementById("atr-val").textContent = features.atr14 ? `$${features.atr14} (${features.atr_pct}%)` : "--";

    // 2. Banners & Fail-Closed Alerts
    const alertBanner = document.getElementById("system-alert-banner");
    const alertText = document.getElementById("alert-banner-text");

    if (market.is_fallback || market.is_synthetic) {
        alertBanner.classList.remove("hidden");
        alertText.textContent = "DEMO DATA — NOT LIVE (Fallback Path Detected)";
    } else if (!det.data_gates_passed) {
        alertBanner.classList.remove("hidden");
        alertText.textContent = `DATA DEGRADED: ${det.reasons.join(" | ")}`;
    } else {
        alertBanner.classList.add("hidden");
    }

    // Shadow mode banner
    const shadowBanner = document.getElementById("shadow-alert-banner");
    if (rec.shadow_mode) {
        shadowBanner.classList.remove("hidden");
    } else {
        shadowBanner.classList.add("hidden");
    }

    // 3. Verdict Hero (Element 3 & 4)
    const badge = document.getElementById("verdict-badge");
    const badgeText = document.getElementById("verdict-text");
    const badgeIcon = document.getElementById("verdict-icon");

    badge.className = "verdict-badge";
    const verdict = det.final_verdict;
    badgeText.textContent = verdict;

    if (verdict === "BUY") {
        badge.classList.add("badge-buy");
        badgeIcon.textContent = "🟢";
    } else if (verdict === "SELL") {
        badge.classList.add("badge-sell");
        badgeIcon.textContent = "🔴";
    } else if (verdict === "HOLD") {
        badge.classList.add("badge-hold");
        badgeIcon.textContent = "🔵";
    } else if (verdict === "ABSTAIN") {
        badge.classList.add("badge-abstain");
        badgeIcon.textContent = "🟡";
    } else {
        badge.classList.add("badge-fail");
        badgeIcon.textContent = "⚪";
    }

    // Confidence
    document.getElementById("confidence-band").textContent = det.final_confidence_band || "--";
    document.getElementById("confidence-raw").innerHTML = det.final_confidence_raw !== null ? `Raw: ${det.final_confidence_raw} <span class="uncalibrated-pill">(uncalibrated)</span>` : "--";
    document.getElementById("verdict-timestamp").textContent = rec.ts_local_myt;

    // Conflict Surfacing
    const conflictBanner = document.getElementById("conflict-banner");
    const conflictMsg = document.getElementById("conflict-msg");
    if (det.conflicts && det.conflicts.length > 0) {
        conflictBanner.classList.remove("hidden");
        conflictMsg.textContent = det.conflicts.join("; ");
    } else {
        conflictBanner.classList.add("hidden");
    }

    // Reasons Box
    document.getElementById("reasons-box").textContent = det.reasons.join(" • ");

    // 4. Pillars (Element 5)
    renderPillar("tech", det.pillars.technicals, `EMA Stack: ${rec.categorical.market_cat.ema_stack} | Pos: ${rec.categorical.market_cat.ema200_pos}`);
    renderPillar("deriv", det.pillars.derivatives, `Funding Cat: ${rec.categorical.derivatives_cat.funding_cat}`);
    renderPillar("soc", det.pillars.social, `Polarity: ${rec.categorical.social_meta.polarity_cat}`);

    // 5. Decision Levels (Element 6)
    const shadowBox = document.getElementById("shadow-locked-box");
    const levelsContent = document.getElementById("levels-content");
    const shadowProgress = document.getElementById("shadow-progress-text");

    if (rec.shadow_mode) {
        shadowBox.classList.remove("hidden");
        levelsContent.classList.add("hidden");
        shadowProgress.textContent = `Verdict #${rec.run_id.slice(0, 8)} (Calibration window)`;
    } else if (levels.has_valid_levels) {
        shadowBox.classList.add("hidden");
        levelsContent.classList.remove("hidden");

        document.getElementById("lvl-entry").textContent = `$${levels.entry_zone[0].toLocaleString()} – $${levels.entry_zone[1].toLocaleString()}`;
        document.getElementById("lvl-stop").textContent = `$${levels.stop.price.toLocaleString()} (-${levels.stop.pct}%, ${levels.stop.atr_multiple}x ATR)`;
        document.getElementById("lvl-tp1").textContent = levels.target_primary ? `$${levels.target_primary.toLocaleString()}` : "Intraday session close";

        const rowExt = document.getElementById("row-target-ext");
        if (levels.target_extended) {
            rowExt.classList.remove("hidden");
            document.getElementById("lvl-tp2").textContent = `$${levels.target_extended.toLocaleString()} (3.0R)`;
        } else {
            rowExt.classList.add("hidden");
        }

        document.getElementById("lvl-invalidation").textContent = `Invalidation: ${levels.invalidation || "Structure breach"}`;
        document.getElementById("levels-method").textContent = levels.method;

        // Auto-fill calculator stop distance
        if (levels.stop.pct) {
            document.getElementById("calc-stop-pct").value = levels.stop.pct;
        }
    } else {
        shadowBox.classList.remove("hidden");
        levelsContent.classList.add("hidden");
        shadowBox.querySelector("h3").textContent = "No Trade Levels For This State";
        shadowBox.querySelector("p").textContent = "Levels are only calculated when an asymmetric structural setup is confirmed (not on ABSTAIN or NO_VERDICT).";
    }

    // 6. Footer
    document.getElementById("footer-run-id").textContent = rec.run_id;
    document.getElementById("footer-state-hash").textContent = rec.categorical ? rec.categorical.rule_ver : "--";

    // 7. Auto-populate outcome target run ID & update calibration stats
    const outcomeInput = document.getElementById("outcome-run-id");
    if (outcomeInput) outcomeInput.value = rec.run_id;
    fetchCalibrationReport();
}

function applyCalibrationReportData(report) {
    if (!report) return;
    const ac10 = report.ac10_gate || {};
    const cal = report.empirical_calibration || {};
    const bandPerf = ac10.band_performance || {};

    // 1. Update AC-10 badge
    const ac10Badge = document.getElementById("ac10-status-badge");
    if (ac10Badge) {
        ac10Badge.textContent = `AC-10: ${ac10.status || "COLLECTING"} (${ac10.total_verified_outcomes || 0}/${ac10.required_outcomes || 60})`;
        ac10Badge.className = "cal-badge";
        if (ac10.gate_passed) ac10Badge.classList.add("badge-passed");
        else if (ac10.status === "RECALIBRATION_REQUIRED") ac10Badge.classList.add("badge-recal");
    }

    // 2. Update Calibration badge
    const calBadge = document.getElementById("cal-status-badge");
    if (calBadge) {
        calBadge.textContent = `Calibration: ${cal.display_label || "uncalibrated"} (${cal.total_verified_outcomes || 0}/${cal.required_for_calibration || 100})`;
        if (cal.is_calibrated) calBadge.classList.add("badge-passed");
    }

    // 3. Update Message
    const calMsg = document.getElementById("cal-msg");
    if (calMsg && ac10.message) {
        calMsg.textContent = ac10.message;
    }

    // 4. Update Table
    const tbody = document.getElementById("cal-table-body");
    if (tbody) {
        tbody.innerHTML = "";
        ["High", "Medium", "Low"].forEach(b => {
            const row = bandPerf[b] || { total_outcomes: 0, wins: 0, losses: 0, win_rate: 0 };
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${b}</strong></td>
                <td>${row.total_outcomes}</td>
                <td>${row.wins}</td>
                <td>${row.losses}</td>
                <td><strong>${((row.win_rate || 0) * 100).toFixed(1)}%</strong></td>
            `;
            tbody.appendChild(tr);
        });
    }
}

async function fetchCalibrationReport() {
    try {
        const res = await fetch("/api/v1/calibration");
        if (res.ok) {
            const data = await res.json();
            applyCalibrationReportData(data.report || {});
            return;
        }
    } catch (e) {
        // Fallback to static calibration file
    }

    try {
        const resStatic = await fetch("data/calibration.json?t=" + Date.now());
        if (resStatic.ok) {
            const report = await resStatic.json();
            applyCalibrationReportData(report);
        }
    } catch (err) {
        console.warn("Could not load calibration data:", err);
    }
}

function renderPillar(id, state, detail) {
    const stateEl = document.getElementById(`state-${id}`);
    const detailEl = document.getElementById(`detail-${id}`);

    stateEl.className = "pillar-state";
    stateEl.textContent = state;

    if (state === "bullish") stateEl.classList.add("state-bullish");
    else if (state === "bearish") stateEl.classList.add("state-bearish");
    else if (state === "weak") stateEl.classList.add("state-weak");
    else if (state === "unavailable") stateEl.classList.add("state-unavailable");
    else stateEl.classList.add("state-neutral");

    if (detail) detailEl.textContent = detail;
}

function renderErrorState(errMsg) {
    const badge = document.getElementById("verdict-badge");
    badge.className = "verdict-badge badge-fail";
    document.getElementById("verdict-text").textContent = "NO_VERDICT";
    document.getElementById("verdict-icon").textContent = "⚪";

    const alertBanner = document.getElementById("system-alert-banner");
    alertBanner.classList.remove("hidden");
    document.getElementById("alert-banner-text").textContent = `EVALUATION ERROR: ${errMsg}`;
    document.getElementById("reasons-box").textContent = errMsg;
}

function startNextRefreshCountdown() {
    updateNextRefreshCountdown();
    setInterval(updateNextRefreshCountdown, 1000);
}

function updateNextRefreshCountdown() {
    const now = new Date();
    // Cloud collector runs at minute 5 of every hour (:05)
    let nextRun = new Date(now);
    if (now.getMinutes() < 5) {
        nextRun.setMinutes(5, 0, 0);
    } else {
        nextRun.setHours(now.getHours() + 1);
        nextRun.setMinutes(5, 0, 0);
    }
    const diffSec = Math.max(0, Math.floor((nextRun - now) / 1000));
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    const timeStr = `${mins}m ${secs < 10 ? '0' : ''}${secs}s`;

    const el = document.getElementById("next-refresh-text");
    if (el) {
        el.textContent = `Next Cloud Run: in ${timeStr}`;
    }
}
