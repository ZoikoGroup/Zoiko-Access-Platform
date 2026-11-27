import { defineConfig } from "@playwright/test";

// Browser tests: robots that open the real apps and click like a person. Development only.
// They need the whole stack running (docker compose up -d, and the four apps on ports 5173 to 5176)
// and the test clinic (python scripts/seed_e2e.py). See e2e/README.md.
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,                    // one robot at a time: they share one clinic
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
