const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000", trace: "retain-on-failure" },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: "python manage.py runserver 127.0.0.1:8000",
    url: "http://127.0.0.1:8000/health/",
    reuseExistingServer: true,
  },
});
