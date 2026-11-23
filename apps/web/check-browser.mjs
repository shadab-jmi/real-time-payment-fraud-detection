import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { join } from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const evidence = process.env.BROWSER_EVIDENCE_DIR || fileURLToPath(
  new URL("../../docs/phase-5-images/", import.meta.url),
);
mkdirSync(evidence, { recursive: true });
function tool(args) {
  return execFileSync(
    "docker",
    ["compose", "run", "--rm", "-T", "--entrypoint", ...args],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] },
  ).trim();
}
const users = JSON.parse(
  tool(["cat", "tools", "/credentials/local-users.json"]),
);
const cid = tool([
  "python",
  "tools",
  "-c",
  "from tests.ui import fresh_case; print(fresh_case())",
])
  .split(/\r?\n/)
  .at(-1);
const base = process.env.WEB_URL || "http://127.0.0.1:3000";
const browser = await chromium.launch({
  channel: process.env.BROWSER_CHANNEL || "msedge",
  headless: true,
});
async function visit(role) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 960 },
  });
  const page = await ctx.newPage();
  await page.goto(base);
  await page.getByLabel("Username", { exact: true }).fill(role);
  await page.getByLabel("Password", { exact: true }).fill(users[role]);
  await page.getByRole("button", { name: "Sign in" }).press("Enter");
  await page.getByRole("heading", { name: "Payment volume" }).waitFor();
  return page;
}
async function tabTo(page, target) {
  for (let i = 0; i < 120; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error("Keyboard could not reach requested control");
}
try {
  const viewer = await visit("viewer");
  await viewer.screenshot({ path: join(evidence, "desktop-overview.png") });
  await viewer.goto(base + "/cases/" + cid);
  await viewer.getByText("Case details", { exact: true }).waitFor();
  assert.equal(
    await viewer.getByRole("button", { name: "Take ownership" }).count(),
    0,
  );
  assert.equal(await viewer.getByLabel("New note").count(), 0);
  await viewer.reload();
  await viewer.getByRole("heading", { name: "Case details" }).waitFor();
  // Unscored receipt states get deterministic UI fixtures without injecting failures into Kafka.
  const tid = "00000000-0000-4000-8000-000000000005";
  for (const state of ["pending", "failed"]) {
    const url = "**/v1/transactions/" + tid;
    await viewer.route(url, (route) =>
      route.fulfill({
        json: {
          transaction_id: tid,
          state,
          received_at: "2026-01-01T00:00:00Z",
          amount_minor: 10000,
          account_id: "acct_fixture",
          score: null,
          action: null,
          contributions: [],
        },
      }),
    );
    await viewer.goto(base + "/transactions/" + tid);
    await viewer.locator(".details .pill").filter({ hasText: state }).waitFor();
    await viewer.unroute(url);
  }
  await viewer.goto(base + "/transactions?run_id=" + tid);
  await viewer.getByText("No matches for the current filters.").waitFor();
  const page = await visit("analyst");
  await page.goto(base + "/cases");
  const filter = page.getByLabel("Status", { exact: true });
  await tabTo(page, filter);
  await page.keyboard.press("ArrowDown");
  await page.waitForURL("**/cases?status=open");
  const link = page.locator('[id="row-' + cid + '"]');
  await link.waitFor();
  await tabTo(page, link);
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Case details" }).waitFor();
  const back = page.getByRole("link", { name: "Back to cases", exact: false });
  await tabTo(page, back);
  await page.keyboard.press("Enter");
  await link.waitFor();
  await page.waitForFunction(
    (id) => document.activeElement?.id === "row-" + id,
    cid,
  );
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Case details" }).waitFor();
  await tabTo(page, page.getByRole("button", { name: "Take ownership" }));
  await page.keyboard.press("Enter");
  await page
    .getByLabel("Resolution", { exact: true })
    .fill("Synthetic evidence verified by browser check.");
  const note = page.getByLabel("New note");
  await tabTo(page, note);
  await page.keyboard.type("Synthetic analyst note from keyboard.");
  const stale = await page.context().newPage();
  await stale.goto(page.url());
  await stale.getByLabel("New note").fill("Concurrent reviewer evidence.");
  await stale.getByRole("button", { name: "Add note" }).click();
  await stale.getByText("Note saved", { exact: true }).waitFor();
  await page.bringToFront();
  const rejected = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.status() === 409,
  );
  await page.getByRole("button", { name: "Resolve case" }).click();
  await rejected;
  await page.getByText(/Case changed/).waitFor();
  assert.equal(
    await note.inputValue(),
    "Synthetic analyst note from keyboard.",
  );
  await tabTo(page, page.getByRole("button", { name: "Add note" }));
  await page.keyboard.press("Enter");
  await page.getByText("Note saved", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Resolve case" }).click();
  await page
    .getByText("Resolution · Synthetic evidence verified by browser check.")
    .waitFor();
  await page.reload();
  await page
    .getByText("Synthetic analyst note from keyboard.", { exact: true })
    .waitFor();
  await page.setViewportSize({ width: 360, height: 800 });
  await page.screenshot({ path: join(evidence, "mobile-case.png"), fullPage: true });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.goto(base + "/overview");
  await page.getByRole("heading", { name: "Payment volume" }).waitFor();
  await page.screenshot({
    path: join(evidence, "mobile-overview.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  console.log(
    "PASS browser roles, keyboard filters, case opening and note, stale draft preservation, resolution, refresh and responsive layouts",
    cid,
  );
} finally {
  await browser.close();
}
