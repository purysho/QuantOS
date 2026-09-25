// First Current Terminal — read-only view over an immutable export.
// No business logic lives here: tables are loaded as exported, after their
// SHA-256 is checked against terminal_manifest.json.
import perspective from "https://cdn.jsdelivr.net/npm/@finos/perspective@3.8.0/dist/cdn/perspective.js";

const $ = (id) => document.getElementById(id);
const status = (text, bad = false) => { $("status").textContent = text; $("status").className = bad ? "bad" : "muted"; };

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// The viewer module initializes the shared client wasm; wait for it first.
await customElements.whenDefined("perspective-viewer");
const worker = await perspective.worker();
const viewer = $("viewer");
const manifest = await (await fetch("terminal_manifest.json", { cache: "no-store" })).json();
if (manifest.authority !== "NONE" || manifest.read_only !== true) {
  status("Refusing to display: export does not declare authority NONE / read-only.", true);
  throw new Error("export authority is not NONE");
}
$("export-id").textContent = manifest.export_id + " · exported " + manifest.exported_at;
const loaded = new Map();
let verified = 0;

async function loadTable(entry) {
  if (loaded.has(entry.file)) return loaded.get(entry.file);
  const buffer = await (await fetch(entry.file, { cache: "no-store" })).arrayBuffer();
  const digest = await sha256Hex(buffer);
  if (digest !== entry.sha256) throw new Error(`integrity check failed for ${entry.file}`);
  verified += 1;
  $("integrity").textContent = `${verified} table(s) SHA-256 verified`;
  const doc = JSON.parse(new TextDecoder().decode(buffer));
  const table = await worker.table(doc.schema);
  if (doc.rows.length) await table.update(doc.rows);
  const result = { table, columns: doc.columns || Object.keys(doc.schema), view: doc.view || null };
  loaded.set(entry.file, result);
  return result;
}

async function showTable(entry, button) {
  for (const b of $("tables").children) b.setAttribute("aria-selected", String(b === button));
  status(`Loading ${entry.name} …`);
  try {
    const { table, columns, view } = await loadTable(entry);
    const base = { title: `${entry.workspace} · ${entry.name}`, columns, plugin: "Datagrid", plugin_config: { columns: {} },
                   group_by: [], split_by: [], filter: [], sort: [], aggregates: {} };
    // Apply the view in the same draw as the load, so the datagrid measures
    // widths for the columns it actually shows. A view is presentation only;
    // if it cannot be applied, the plain grid is shown instead.
    const loading = viewer.load(table);
    try {
      await viewer.restore(view ? { ...base, ...view } : base);
    } catch (error) {
      await viewer.restore(base);
    }
    await loading;
    await viewer.flush();
    status(`${entry.name} — ${entry.rows} row(s)${entry.truncated ? " (truncated)" : ""} · source ${entry.source}`);
    viewer.dataset.loadedTable = entry.name;
  } catch (error) {
    status(String(error.message || error), true);
  }
}

function showWorkspace(name, navButton) {
  for (const b of $("workspaces").children) b.setAttribute("aria-current", String(b === navButton));
  const entries = manifest.tables.filter((t) => t.workspace === name);
  const bar = $("tables");
  bar.replaceChildren();
  for (const entry of entries) {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "tab");
    b.textContent = `${entry.name} (${entry.rows})`;
    b.title = entry.source;
    b.addEventListener("click", () => showTable(entry, b));
    bar.append(b);
  }
  if (entries.length) showTable(entries[0], bar.firstChild);
  else status(`No stored artifacts for ${name} in this export.`);
}

const nav = $("workspaces");
for (const name of manifest.workspaces) {
  const count = manifest.tables.filter((t) => t.workspace === name).length;
  if (name === "Other" && count === 0) continue;
  const b = document.createElement("button");
  b.type = "button";
  b.innerHTML = "";
  b.append(document.createTextNode(name));
  const span = document.createElement("span");
  span.className = "count";
  span.textContent = String(count);
  b.append(span);
  b.disabled = count === 0;
  b.addEventListener("click", () => showWorkspace(name, b));
  nav.append(b);
}
if (manifest.unreadable_sources.length) {
  status(`${manifest.unreadable_sources.length} source(s) could not be read at export time.`, true);
}
const first = [...nav.children].find((b) => !b.disabled);
if (first) first.click();
document.body.dataset.ready = "true";
