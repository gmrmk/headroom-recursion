import { expect, test } from "@playwright/test";

/**
 * End-to-end smoke for the investigation flow. The user's directive
 * 2026-05-11: "Playwright this over and over until you are sure it
 * works. If it doesnt work we have to find out why."
 *
 * What each test asserts:
 *   1. Page loads cleanly (no console errors, no hydration warnings)
 *   2. Form renders with all expected semantic fields
 *   3. Typing into a single field updates "What this will run"
 *   4. Investigate button dispatches; report fills with events
 *   5. Save-dossier affordances are gone (stealth-first directive)
 *
 * Each test creates a fresh investigation via the API so they don't
 * interfere. The dev stack (API on :8000 + worker + Next on :3000)
 * must be up before running.
 */

const API_BASE = process.env.OSINT_API_URL ?? "http://127.0.0.1:8000";

async function createInvestigation(): Promise<string> {
  const r = await fetch(`${API_BASE}/investigations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      subject: { kind: "person", value: "e2e" },
      investigator_handle: "playwright",
      notes: "",
    }),
  });
  if (!r.ok) throw new Error(`createInvestigation: HTTP ${r.status}`);
  const { id } = (await r.json()) as { id: string };
  return id;
}

test.describe("investigation dashboard end-to-end", () => {
  test("loads page without console errors", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

    const id = await createInvestigation();
    await page.goto(`/investigations/${id}`);

    // The page renders an <h2>Dossier</h2> heading; specify role to
    // disambiguate from the nav link with the same text.
    await expect(
      page.getByRole("heading", { name: "Dossier" }),
    ).toBeVisible();

    // Confirm no console errors (filter out benign extension noise)
    const real = consoleErrors.filter(
      (e) =>
        !e.includes("favicon") &&
        !e.toLowerCase().includes("extension") &&
        !e.includes("DEP0"),
    );
    expect(real, `Console errors on load: ${real.join("\n")}`).toEqual([]);
  });

  test("form renders all 9 semantic fields", async ({ page }) => {
    const id = await createInvestigation();
    await page.goto(`/investigations/${id}`);

    // Each field label from FIELD_META
    const labels = [
      "Address",
      "Host / owner name",
      "Listing photo URL",
      "Email",
      "Phone",
      "Username / handle",
      "IP address",
      "Domain",
      "Inside Airbnb CSV (optional)",
      "Notes",
    ];
    for (const label of labels) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
  });

  test('typing a field updates "What this will run" preview', async ({ page }) => {
    const id = await createInvestigation();
    await page.goto(`/investigations/${id}`);

    // Before typing: preview shows "Fill any field above"
    await expect(page.getByText(/Fill any field above/)).toBeVisible();

    // Type into the IP field; preview should immediately show w10.ip
    await page.getByLabel("IP address").fill("8.8.8.8");
    await expect(page.getByText(/IP Vetting/)).toBeVisible();
    await expect(page.getByText("w10.ip")).toBeVisible();
  });

  test("Investigate dispatches and report fills with findings", async ({ page }) => {
    // Diagnostics: capture every network call + console message so the
    // failure mode tells us EXACTLY where the chain breaks.
    const network: Array<{ url: string; status: number; method: string }> = [];
    const consoleErrors: string[] = [];
    page.on("response", (r) => {
      const u = r.url();
      if (u.includes("/api/") || u.includes("/investigations/")) {
        network.push({ url: u, status: r.status(), method: r.request().method() });
      }
    });
    page.on("console", (msg) => {
      if (msg.type() === "error" || msg.type() === "warning")
        consoleErrors.push(`${msg.type()}: ${msg.text()}`);
    });
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

    const id = await createInvestigation();
    await page.goto(`/investigations/${id}`);

    await page.getByLabel("IP address").fill("8.8.8.8");
    await page.getByRole("button", { name: /Investigate/i }).click();

    // Give the dispatch + SSE + worker chain time to land at least one
    // event in the React state.
    let lastCount = 0;
    let lastText = "";
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline) {
      const el = page.locator("text=/\\d+ events?/").first();
      try {
        lastText = (await el.textContent({ timeout: 1500 })) ?? "";
        const m = lastText.match(/(\d+)/);
        lastCount = m ? parseInt(m[1] ?? "0", 10) : 0;
        if (lastCount > 0) break;
      } catch {
        // element not yet visible
      }
      await page.waitForTimeout(500);
    }

    // Build a full diagnostic message if we fail
    const diag = [
      `last event count: ${lastCount}`,
      `last text: ${JSON.stringify(lastText)}`,
      `network calls (${network.length}):`,
      ...network.map((n) => `  ${n.method} ${n.status} ${n.url}`),
      `console (${consoleErrors.length}):`,
      ...consoleErrors.slice(0, 10).map((e) => `  ${e}`),
    ].join("\n");

    expect(lastCount, `events never arrived.\n${diag}`).toBeGreaterThan(0);
  });

  test("no save-dossier buttons on the page (stealth-first)", async ({ page }) => {
    const id = await createInvestigation();
    await page.goto(`/investigations/${id}`);

    // .md and .html buttons should be absent
    await expect(page.getByRole("button", { name: /\.md/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /\.html/i })).toHaveCount(0);
    await expect(page.getByText(/save dossier/i)).toHaveCount(0);
  });
});
