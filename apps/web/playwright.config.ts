import { defineConfig, devices } from "@playwright/test";

// Minimal Playwright config — assumes the dev stack is already up.
// I'm not having Playwright start the server because the API + worker
// must be running too; the user already has the stack up via the
// dev-launcher in another shell.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "off",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
