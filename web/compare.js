const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
const money = (value) => value === null || value === undefined ? "Not available" : `CHF ${new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(value)}`;
const plain = (value) => value === null || value === undefined ? "Unknown" : typeof value === "number" ? new Intl.NumberFormat("en-US", {maximumFractionDigits: 2}).format(value) : String(value);
const sourceName = (source) => ({database_cache: "dataset", web_search: "research"})[source] || source || "unknown";
const sourceBadge = (source) => `<span class="badge">${escapeHtml(sourceName(source))}</span>`;
const when = (month, date) => month === null || month === undefined ? "Not reached" : `Month ${escapeHtml(month)}${date ? ` · ${escapeHtml(date)}` : ""}`;
const human = (value) => String(value || "").replaceAll("_", " ");
let currentData;
let currentMode = "custom";
let latestSubmission = 0;

function showStatus(message, error = false) {
  const element = $("#status");
  element.textContent = message;
  element.classList.toggle("error", error);
}

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The comparison could not be calculated.");
  return data;
}

function metric(label, value, detail) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(money(value))}</strong><small>${escapeHtml(detail)}</small></div>`;
}

function renderFunding(metrics, start) {
  const gap = metrics.maximum_funding_need_chf;
  const first = metrics.first_funding_gap_period_start || metrics.first_funding_gap_date || "an unknown date";
  const low = metrics.min_cash_chf;
  const lowDate = metrics.min_cash_date;
  const cash = metrics.closing_cash_chf;
  const realCash = metrics.closing_real_cash_chf;
  const worth = metrics.closing_net_worth_chf;
  const realWorth = metrics.closing_real_net_worth_chf;
  const reserveCount = metrics.minimum_reserve_breaches?.length || 0;
  const warning = gap > 0 ? `Interim funding needed up to ${money(gap)}. First gap in the period beginning ${first} (month ${metrics.first_funding_gap_month}).` : "No funding gap is shown in this projection.";
  const reserve = reserveCount ? ` Minimum reserve breached in ${reserveCount} recorded periods.` : "";
  $("#funding").innerHTML = `<div class="warning ${gap > 0 || reserveCount ? "" : "good"}">${escapeHtml(warning + reserve)}</div><div class="metrics">${metric("Minimum cash", low, `Low point · ${lowDate}`)}${metric("Terminal cash", cash, "Nominal CHF")}${metric("Cash at base-month purchasing power", realCash, `Purchasing power at ${start}`)}${metric("Terminal net worth", worth, `Real value ${money(realWorth)} at ${start}`)}</div>`;
}

function renderGoals(goals) {
  $("#goals").innerHTML = goals?.length ? goals.map((goal) => {
    const status = goal.met === null ? "Beyond the horizon" : goal.met ? "On track at deadline" : "Short at deadline";
    const reason = goal.failure_reason === "insufficient_cash_at_deadline" ? "Insufficient cash at deadline" : goal.failure_reason === "deadline_beyond_horizon" ? "Deadline is beyond the projection" : "";
    return `<div class="goal"><strong>${escapeHtml(goal.name)}</strong>${sourceBadge(status)}<span class="detail">Target ${money(goal.target_chf)} · ${escapeHtml(when(goal.target_month, goal.target_date))}</span><span class="detail">Reserved ${money(goal.reserved_cash_chf)} · Prior reservations ${money(goal.prior_reserved_cash_chf)}</span><span class="detail">Deadline gap ${money(goal.deadline_gap_chf)} · First attainment ${escapeHtml(when(goal.first_attainment_month, goal.first_attainment_date))}</span>${reason ? `<span class="detail">${escapeHtml(reason)}</span>` : ""}</div>`;
  }).join("") : "<p class=\"detail\">No goals were added to this profile.</p>";
}

function provenanceEntry(label, item) {
  const value = typeof item.value === "number" ? item.unit === "CHF" ? money(item.value) : `${plain(item.value)} ${item.unit || ""}` : item.value ?? "Unknown";
  const details = [item.frequency, item.as_of_date ? `As of ${item.as_of_date}` : "Source date unknown", item.transformation && item.transformation !== "identity" ? item.transformation : null, item.file, item.row, ...(item.source_urls || [])].filter(Boolean).join(" · ");
  return `<div class="provenance-item"><strong>${escapeHtml(label)}</strong>${sourceBadge(item.source)}<span class="detail">${escapeHtml(value)}</span><span class="detail">${escapeHtml(details)}</span></div>`;
}

function renderProvenance(entries) {
  $("#provenance").innerHTML = entries.length ? entries.map(([label, item]) => provenanceEntry(label, item)).join("") : "<p class=\"detail\">No source details were returned.</p>";
}

function renderDrivers(drivers) {
  if (!drivers.length) {
    $("#drivers").innerHTML = '<tr><td colspan="5">No sensitivity results for this path.</td></tr>';
    return;
  }
  const sorted = [...drivers].sort((a, b) => Math.abs(b.terminal_cash_delta_chf ?? b.terminal_cash_impact_chf ?? 0) - Math.abs(a.terminal_cash_delta_chf ?? a.terminal_cash_impact_chf ?? 0));
  $("#drivers").innerHTML = sorted.map((item) => {
    const goals = item.goal_impacts || [];
    const base = item.base_value;
    const terminal = item.terminal_cash_delta_chf;
    const minimum = item.minimum_liquidity_delta_chf;
    const goalStatus = (value) => value === null || value === undefined ? "outside horizon" : value ? "met" : "unmet";
    const goalText = goals.length ? goals.map((goal) => {
      const gap = goal.deadline_gap_delta_chf;
      const baseMet = goal.base_met;
      return `${goal.name} · gap change ${money(gap)} · ${goalStatus(baseMet)} to ${goalStatus(goal.perturbed_met)}`;
    }).join("; ") : "No goal in this path";
    const label = item.label || item.parameter || item.field;
    const unit = item.unit || "";
    return `<tr><td><strong>${escapeHtml(label)}</strong>${sourceBadge(item.source)}${item.event_label ? `<span class="detail">${escapeHtml(item.event_label)}</span>` : ""}</td><td>${escapeHtml(plain(base))} to ${escapeHtml(plain(item.perturbed_value))} ${escapeHtml(unit)}<span class="detail">${escapeHtml(`Change ${plain(item.perturbation)}`)}</span></td><td>${escapeHtml(money(terminal))}</td><td>${escapeHtml(money(minimum))}</td><td>${escapeHtml(goalText)}</td></tr>`;
  }).join("");
}


function collectProvenance(value, prefix = "", entries = []) {
  if (!value || typeof value !== "object") return entries;
  if (!Array.isArray(value) && Object.hasOwn(value, "value") && Object.hasOwn(value, "source")) {
    entries.push([value.label || human(prefix), value]);
    return entries;
  }
  Object.entries(value).forEach(([key, item]) => collectProvenance(item, prefix ? `${prefix} · ${key}` : key, entries));
  return entries;
}

function renderComparison() {
  if (!currentData) return;
  const bundle = currentData.comparison_bundle;
  const name = $("#path-select").value;
  const path = bundle.paths[name];
  if (!path) return;
  const record = bundle.scenario_record;
  const start = record?.start || currentData.start || currentData.start_date;
  const horizon = currentData.horizon_months;
  $("#path-title").textContent = name;
  $("#period").textContent = `${currentData.profile?.name || "Your profile"} · ${start} · ${horizon} months`;
  $("#path-wrap").hidden = Object.keys(bundle.paths).length < 2;
  renderFunding(path.metrics, start);
  renderGoals(path.metrics.goals);
  const entries = collectProvenance(path.provenance);
  collectProvenance(bundle.assumptions, "", entries);
  renderProvenance(entries);
  renderDrivers(path.sensitivity_summary.drivers);
  $("#driver-note").textContent = `${path.sensitivity_summary.driver_count} one-at-a-time changes`;
  $("#commitments").innerHTML = Object.entries(path.commitments || {}).map(([key, value]) =>
    `<details><summary>${escapeHtml(human(key))}</summary><pre class="commitment-values">${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`).join("") || "<p>No commitments recorded.</p>";
  $("#checks").innerHTML = path.sanity_checks.map((check) => `<li>${escapeHtml(human(check.name))} · ${check.passed === null ? "unavailable" : check.passed ? "passed" : "needs review"}${check.maximum_error_chf == null ? "" : ` · maximum difference ${escapeHtml(money(check.maximum_error_chf))}`}</li>`).join("");
  $("#limitations").innerHTML = bundle.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#results").hidden = false;
}

function setComparison(data, selected) {
  currentData = data;
  const names = Object.keys(data.comparison_bundle.paths);
  $("#path-select").innerHTML = names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  $("#path-select").value = names.includes(selected) ? selected : data.comparison_bundle.baseline_path;
  renderComparison();
}

function setMode(mode) {
  currentMode = mode;
  for (const name of ["preset", "custom"]) {
    const active = name === mode;
    $(`#${name}-mode`).classList.toggle("active", active);
    $(`#${name}-mode`).setAttribute("aria-pressed", String(active));
  }
  $("#custom-controls").hidden = mode !== "custom";
  $("#results").hidden = true;
  showStatus("");
}

async function loadPreset() {
  setMode("preset");
  const submission = ++latestSubmission;
  try {
    showStatus("Loading comparison…");
    const data = await request("/api/comparisons/alexia");
    if (submission === latestSubmission && currentMode === "preset") {
      showStatus("");
      setComparison(data);
    }
  } catch (error) {
    if (submission === latestSubmission && currentMode === "preset") showStatus(error.message, true);
  }
}

$("#custom-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submission = ++latestSubmission;
  const data = new FormData(event.currentTarget);
  const number = (name) => Number(data.get(name));
  const profile = {age: number("age"), cash_chf: number("cash"), monthly_net_income_chf: number("income"), monthly_rent_chf: number("rent"), minimum_reserve_chf: number("reserve"), goals: [{name: "Your goal", target_chf: number("goal"), in_months: number("deadline"), priority: "medium"}]};
  const studyFields = {tuition: "annual_tuition_chf", duration: "duration_months", study_income: "income_share_while_studying", after_income: "income_after_chf"};
  const studyParams = {};
  for (const [name, key] of Object.entries(studyFields)) {
    if (data.get(name) !== "") studyParams[key] = number(name) / (name === "study_income" ? 100 : 1);
  }
  const scenarios = data.has("study") ? {"Study": [{type: "education", start_month: 0, params: studyParams}]} : {};
  const payload = {profile, scenarios, start: data.get("start"), horizon_years: number("years"), annual_inflation: number("inflation") / 100};
  try {
    showStatus("Calculating comparison…");
    const result = await request("/api/project", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    if (submission !== latestSubmission) return;
    if (currentMode === "custom") { showStatus(""); setComparison(result, scenarios.Study ? "Study" : "Baseline"); }
  } catch (error) { if (submission === latestSubmission && currentMode === "custom") showStatus(error.message, true); }
});

$("#preset-mode").addEventListener("click", loadPreset);
$("#custom-mode").addEventListener("click", () => setMode("custom"));
$("#path-select").addEventListener("change", renderComparison);
const today = new Date();
$("[name=start]").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
