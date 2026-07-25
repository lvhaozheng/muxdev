import { defineConfig } from "@playwright/test";

const port = Number(process.env.MUXDEV_E2E_PORT || 18977);

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: ".test_workspaces/playwright-results",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.platform === "win32" ? "msedge" : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python scripts/e2e_server.py",
    url: `http://127.0.0.1:${port}/health`,
    reuseExistingServer: false,
    timeout: 30_000,
    env: { MUXDEV_E2E_PORT: String(port) },
  },
});
