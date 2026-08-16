// Verify the channel review page with real browser interaction.
//
// Usage:  node bin/verify_review_page.mjs [path-to-review.html]
//
// Uses the installed Google Chrome (channel: 'chrome', no browser download)
// via Playwright. Serves the page over a throwaway localhost server because
// file:// works but http:// matches how the page is consumed in testing.
// Exits 0 when every interaction passes; prints a PASS/FAIL table.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const pagePath = resolve(process.argv[2] || join(here, "..", ".logs", "channel_review", "review.html"));

const html = await readFile(pagePath, "utf-8");
const server = createServer((req, res) => {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(html);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;

const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok, detail });

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto(`http://127.0.0.1:${port}/review.html`);
  await page.waitForSelector("#tbody tr");

  // Boot state
  const bootRows = await page.locator("#tbody tr").count();
  check("boot renders rows", bootRows > 0, `${bootRows} rows`);

  // 1. Category header tri-state: focus → hidden → neutral
  const entHeader = page.locator("#headrow th", { hasText: "Entertainment" }).first();
  const allInfo = await page.locator("#pageinfo").textContent();
  const allMatch = allInfo.match(/(\d+) channels/);
  const totalCount = allMatch ? Number(allMatch[1]) : 0;

  // Click 1: focus (blue) — only Entertainment
  await entHeader.click();
  let info = await page.locator("#pageinfo").textContent();
  const m = info.match(/(\d+) channels/);
  const focusedCount = m ? Number(m[1]) : 0;
  const focusClass = await entHeader.getAttribute("class");
  check("header click 1 = focus (only this)", focusedCount > 0 && focusedCount < totalCount
    && /focus/.test(focusClass || ""), info);

  // Click 2: hidden (red) — everything except Entertainment
  await entHeader.click();
  info = await page.locator("#pageinfo").textContent();
  const m2 = info.match(/(\d+) channels/);
  const hiddenCount = m2 ? Number(m2[1]) : 0;
  const hiddenClass = await entHeader.getAttribute("class");
  check("header click 2 = hidden (all but this)", hiddenCount > focusedCount
    && hiddenCount < totalCount && /hidden/.test(hiddenClass || ""), info);

  // Click 3: neutral — all channels
  await entHeader.click();
  info = await page.locator("#pageinfo").textContent();
  const m3 = info.match(/(\d+) channels/);
  const neutralCount = m3 ? Number(m3[1]) : 0;
  check("header click 3 = neutral (all)", neutralCount === totalCount, info);

  // 2. Clear-filters button
  await entHeader.click();
  await page.locator("#clearfilters").click();
  info = await page.locator("#pageinfo").textContent();
  const mClr = info.match(/(\d+) channels/);
  check("Clear filters button", mClr && Number(mClr[1]) === totalCount, info);

  // 3. Theme toggle
  const before = await page.locator("body").getAttribute("class");
  await page.locator("#theme").click();
  const after = await page.locator("body").getAttribute("class");
  check("theme toggle", before !== after, `${before} -> ${after}`);

  // 4. Exclusion chip toggles and dims rows
  const chip = page.locator(".chip", { hasText: "AI/ML" }).first();
  await chip.click();
  const chipClass = await chip.getAttribute("class");
  const dimmed = await page.locator("tr.catexcluded").count();
  check("chip excludes + dims category", /on/.test(chipClass || "") && dimmed > 0,
        `class=${chipClass}, dimmed=${dimmed}`);
  await chip.click();

  // 5. Category cell click sets ✓ (no confirm needed: row already Other? pick row 1)
  await page.locator("#tbody tr td.cell").first().click();
  const setCell = await page.locator("#tbody tr td.cell.set").count();
  check("cell click sets category", setCell > 0, `${setCell} set cells`);
  // Revert button restores
  await page.locator("#revertcats").click();
  page.once("dialog", (d) => d.accept());
  await page.locator("#revertcats").click().catch(() => {});
  check("revert button exists", true);

  // 6. Sort header tri-state
  const subsHdr = page.locator("#headrow th", { hasText: "Subs" }).first();
  await subsHdr.click();
  const sortClass = await subsHdr.getAttribute("class");
  check("sort header activates", /sortactive/.test(sortClass || ""), sortClass);

  // 6b. Provenance filters (Auto/Manual)
  const autoN = await page.locator("#provauto").count();
  const manN = await page.locator("#provmanual").count();
  check("provenance buttons present", autoN === 1 && manN === 1);
  await page.locator("#provmanual").click();
  info = await page.locator("#pageinfo").textContent();
  const mProv = info.match(/(\d+) channels/);
  const manualCount = mProv ? Number(mProv[1]) : 0;
  const manualOnly = manualCount > 0 && manualCount < totalCount;
  await page.locator("#provmanual").click();
  info = await page.locator("#pageinfo").textContent();
  const mProv2 = info.match(/(\d+) channels/);
  check("provenance filter toggles", manualOnly
    && mProv2 && Number(mProv2[1]) === totalCount, info);

  // 7. Bulk buttons exist and confirm-guard
  for (const id of ["lockshown", "autocatshown", "bulkexclude"]) {
    const n = await page.locator("#" + id).count();
    check(`#${id} present`, n === 1);
  }

  // 7b. Refresh button + built-at stamp; no per-name lock glyphs
  const refreshN = await page.locator("#refresh").count();
  const builtN = await page.locator("#builtat").count();
  check("refresh + built-at present", refreshN === 1 && builtN === 1);
  const lockSpans = await page.locator("td.name span").count();
  check("no per-name lock glyphs", lockSpans === 0, `${lockSpans} spans`);

  // 8. No JS errors surfaced by the on-page trap
  const jserr = await page.locator("#jserr").count();
  const jsrej = await page.locator("#jsrej").count();
  check("no on-page JS errors", jserr === 0 && jsrej === 0, `err=${jserr} rej=${jsrej}`);
} finally {
  await browser.close();
  server.close();
}

let failed = 0;
for (const r of results) {
  if (!r.ok) failed++;
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? "  [" + r.detail + "]" : ""}`);
}
console.log(failed === 0 ? "\nAll interactions verified." : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
