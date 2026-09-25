// Headless browser smoke check for the Perspective terminal.
// Usage: node tests/terminal_smoke.mjs http://127.0.0.1:PORT/
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const url = process.argv[2];
// In sandboxed CI/dev containers outbound HTTPS may go through a proxy.
const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
const host = new URL(url).host;
const browser = await chromium.launch(
  proxy ? { args: [`--proxy-server=${proxy}`, `--proxy-bypass-list=<-loopback>;${host}`, "--disable-http2"] } : {},
);
// Explicit locale: containers often report "en-US@posix", which Intl rejects.
const context = await browser.newContext({ locale: "en-US", ...(proxy ? { ignoreHTTPSErrors: true } : {}) });
const page = await context.newPage();
// Optional: serve the pinned CDN files from unpacked npm tarballs (offline or
// flaky-proxy environments). SRI still verifies the bytes in the browser.
const vendor = process.env.PERSPECTIVE_VENDOR_DIR;
if (vendor) {
  const { readFile } = await import("node:fs/promises");
  const types = { js: "text/javascript", css: "text/css", wasm: "application/wasm", map: "application/json" };
  await context.route(/^https:\/\/cdn\.jsdelivr\.net\/npm\/@finos\//, async (route) => {
    const match = new URL(route.request().url()).pathname.match(/^\/npm\/@finos\/([^@/]+)@3\.8\.0\/(.+)$/);
    if (!match || match[2].includes("..")) return route.abort();
    try {
      const body = await readFile(`${vendor}/${match[1]}/${match[2]}`);
      await route.fulfill({ status: 200, body, headers: {
        "content-type": types[match[2].split(".").pop()] || "application/octet-stream",
        "access-control-allow-origin": "*",
      } });
    } catch {
      console.error("vendor miss", match[1], match[2]);
      await route.fulfill({ status: 404, body: "" });
    }
  });
}
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(url);
try {
  await page.waitForSelector("body[data-ready=true]", { state: "attached", timeout: 60000 });
} catch (error) {
  console.log(JSON.stringify({ timeout: String(error.message).split("\n")[0], errors }));
  await browser.close();
  process.exit(1);
}
try {
  await page.waitForFunction(() => document.getElementById("viewer").dataset.loadedTable, null, { timeout: 60000 });
} catch (error) {
  const statusText = await page.evaluate(() => document.getElementById("status").textContent);
  console.log(JSON.stringify({ timeout: "no table loaded", status: statusText, errors }));
  await browser.close();
  process.exit(1);
}
const result = await page.evaluate(async () => {
  const viewer = document.getElementById("viewer");
  const view = await (await viewer.getTable()).view();
  return {
    workspaces: [...document.querySelectorAll("#workspaces button:not(:disabled)")].map((b) => b.firstChild.textContent),
    table: viewer.dataset.loadedTable,
    rows: await view.num_rows(),
    integrity: document.getElementById("integrity").textContent,
    authority: document.getElementById("authority").textContent,
  };
});
await page.screenshot({ path: process.argv[3] || "terminal-smoke.png" });
await browser.close();
console.log(JSON.stringify({ ...result, errors }));
process.exit(errors.length || result.rows < 1 ? 1 : 0);
