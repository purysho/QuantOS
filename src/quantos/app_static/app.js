// QuantOS control center. Talks only to this app's loopback API, with
// the per-launch token the app put in the URL fragment.
const token = (() => {
  const match = location.hash.match(/token=([A-Za-z0-9_-]+)/);
  if (match) { sessionStorage.setItem("fc-token", match[1]); history.replaceState(null, "", location.pathname); }
  return sessionStorage.getItem("fc-token");
})();
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...children) => { const n = Object.assign(document.createElement(tag), props); n.append(...children); return n; };

async function api(name, body) {
  const options = { method: body === undefined ? "GET" : "POST", headers: { "X-Quantos-Token": token || "" } };
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const response = await fetch(`/api/${name}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function notice(text, error = false) {
  const n = $("notice"); n.textContent = text; n.hidden = !text; n.className = error ? "notice error" : "notice";
}

const STEP_LABELS = { universe: "Universe", fundamentals: "Fundamentals", analysis: "Analysis", rates: "Rates & FX",
  factors: "Factors", prices: "Prices", research: "Research", terminal: "Terminal" };
const ICON = { OK: "✓", FAILED: "✕", RUNNING: "…" };

function renderSteps(steps) {
  $("steps").replaceChildren(...steps.map((s) => el("li", {},
    el("span", { className: `state-${s.state}`, textContent: ICON[s.state] || "" }),
    el("strong", { textContent: STEP_LABELS[s.step] || s.step }),
    el("span", { className: "muted", textContent: s.summary || (s.state === "RUNNING" ? "working…" : "") }))));
}

function renderStatus(status) {
  $("version").textContent = ` v${status.version}`;
  $("setup-card").hidden = status.configured && !window.editingSetup;
  const form = $("setup-form");
  if (!form.dataset.filled) {
    form.contact_email.value = status.config.contact_email;
    form.organization.value = status.config.organization;
    form.universe.value = status.config.universe.join(", ");
    $("key-fields").replaceChildren(...Object.entries(status.keys).map(([name, info]) =>
      el("label", {}, `${name} `, el("span", { className: "muted", textContent: info.configured ? "configured — leave blank to keep" : info.purpose }),
        el("input", { type: "password", name: `key:${name}`, autocomplete: "off", placeholder: info.configured ? "••••••••" : "paste key (optional)" }))));
    form.dataset.filled = "1";
  }
  const cfg = status.config;
  $("config").replaceChildren(
    el("dt", { textContent: "Contact" }), el("dd", { textContent: cfg.contact_email || "not set" }),
    el("dt", { textContent: "Tickers" }), el("dd", { textContent: cfg.universe.join(", ") || "none yet" }),
    el("dt", { textContent: "Stock prices" }), el("dd", { textContent: cfg.price_provider === "none" ? "keyless mode (add a free Tiingo key)" : cfg.price_provider }),
    el("dt", { textContent: "Data folder" }), el("dd", { textContent: status.home }));
  const run = status.last_run;
  if (run) {
    const failed = run.results.filter((r) => !r.ok).length;
    $("last-run").textContent = `Last update ${new Date(run.finished_at).toLocaleString()} — ${failed ? `${failed} step(s) failed` : "all steps succeeded"}.`;
    if (!status.job.running) renderSteps(run.results.map((r) => ({ step: r.step, state: r.ok ? "OK" : "FAILED", summary: r.summary })));
  }
  $("open-terminal").classList.toggle("disabled", !status.terminal_ready);
  $("update").disabled = !status.configured || status.job.running;
  if (!status.configured) notice("Start by entering a contact email below.");
}

async function refresh() {
  try { renderStatus(await api("status")); } catch (error) { notice(`Cannot reach the app: ${error.message}. Restart QuantOS.`, true); }
}

async function pollJob() {
  const job = await api("job");
  renderSteps(job.steps);
  if (job.running) { setTimeout(pollJob, 1000); return; }
  $("update").disabled = false;
  if (job.error) notice(job.error, true);
  await refresh();
}

$("setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const keys = {};
  for (const input of form.querySelectorAll("input[name^='key:']")) if (input.value.trim()) keys[input.name.slice(4)] = input.value.trim();
  $("setup-status").textContent = "Saving…";
  try {
    await api("setup", { contact_email: form.contact_email.value, organization: form.organization.value, universe: form.universe.value, keys });
    for (const input of form.querySelectorAll("input[name^='key:']")) input.value = "";
    $("setup-status").textContent = "Saved.";
    window.editingSetup = false; form.dataset.filled = ""; notice("");
    await refresh();
  } catch (error) { $("setup-status").textContent = error.message; }
});

$("edit-setup").addEventListener("click", () => { window.editingSetup = true; $("setup-card").hidden = false; $("setup-card").scrollIntoView({ behavior: "smooth" }); });

$("update").addEventListener("click", async () => {
  $("update").disabled = true; notice("");
  try { await api("update", {}); pollJob(); } catch (error) { notice(error.message, true); $("update").disabled = false; }
});

const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const bn = (v) => (v == null ? "—" : (v / 1e9).toFixed(1));
const num = (v) => (v == null ? "—" : Number(v).toFixed(2));
$("analyze-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const ticker = event.target.ticker.value.trim();
  if (!ticker) return;
  $("analyze-status").textContent = `Analyzing ${ticker.toUpperCase()}… (the first time fetches its SEC filings)`;
  $("analysis").replaceChildren();
  try {
    const result = await api("analyze", { ticker });
    const rows = result.metrics.slice(-8).reverse();  // most recent fiscal year first
    const spec = [["Revenue ($bn)", "revenue", bn], ["Growth", "revenue_growth", pct], ["Gross margin", "gross_margin", pct],
      ["Operating margin", "operating_margin", pct], ["Net margin", "net_margin", pct], ["Diluted EPS", "eps_diluted", num],
      ["Free cash flow ($bn)", "free_cash_flow", bn], ["Return on equity", "return_on_equity", pct], ["Debt / equity", "debt_to_equity", num],
      ["Validation issues", "validation_errors", (v) => String(v ?? 0)]];
    const head = el("tr", {}, el("th", { textContent: result.ticker }), ...rows.map((r) => el("th", { textContent: r.fiscal_year_end })));
    const body = spec.map(([label, key, fmt]) => el("tr", {}, el("td", { textContent: label }), ...rows.map((r) => el("td", { textContent: fmt(r[key]) }))));
    $("analysis").replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, ...body)));
    const errors = result.issues.filter((i) => i.severity === "ERROR").length;
    $("analyze-status").textContent = `As originally filed in 10-Ks. ${errors ? `${errors} accounting check(s) flagged — details in the terminal.` : "All accounting checks passed."}`;
  } catch (error) { $("analyze-status").textContent = error.message; }
});

$("doctor").addEventListener("click", async () => {
  $("checks").replaceChildren(el("li", { className: "muted", textContent: "Checking…" }));
  try {
    const { checks } = await api("doctor", { online: $("doctor-online").checked });
    const icons = { OK: "✓", WARN: "!", FAIL: "✕" };
    $("checks").replaceChildren(...checks.map((c) => el("li", {},
      el("span", { className: `status-${c.status}`, textContent: icons[c.status] }), el("strong", { textContent: c.name }),
      el("span", { className: "muted", textContent: c.detail }))));
  } catch (error) { $("checks").replaceChildren(el("li", { textContent: error.message })); }
});

$("quit").addEventListener("click", async () => {
  try { await api("quit", {}); } catch {}
  document.body.replaceChildren(el("main", {}, el("div", { className: "card" }, el("h2", { textContent: "QuantOS has stopped." }),
    el("p", { className: "muted", textContent: "You can close this tab. Start the app again to continue." }))));
});

if (!token) notice("Open QuantOS from its app icon: this page needs the link the app opens for you.", true);
refresh().then(async () => { const job = await api("job").catch(() => null); if (job && job.running) pollJob(); });
