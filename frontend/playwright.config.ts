import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E cho Phase 2.
 * Mặc định chạy với SPA production (Nginx + FastAPI + PostgreSQL) tại http://localhost.
 * Có thể trỏ sang môi trường khác bằng biến E2E_BASE_URL.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1",
    locale: "vi-VN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
