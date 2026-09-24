const { test, expect } = require("@playwright/test");

test("sender creates an envelope and reaches the field editor", async ({ page }) => {
  await page.goto("/accounts/signup/");
  await page.getByLabel("Full name").fill("Alex Morgan");
  await page.getByLabel("Organization").fill("Northstar Legal");
  await page.getByLabel("Email").fill(`alex-${Date.now()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Playwright-Contract-93!");
  await page.getByLabel("Password confirmation").fill("Playwright-Contract-93!");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: /Welcome back/ })).toBeVisible();
  await page.getByRole("button", { name: /Start/ }).click();
  await expect(page.getByRole("heading", { name: "Prepare your envelope" })).toBeVisible();
  await page.locator("#documents").setInputFiles({ name: "agreement.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF") });
  await page.getByRole("button", { name: "Upload" }).click();
  await page.getByLabel("Name").last().fill("Jane Doe");
  await page.getByLabel("Email").last().fill("jane@example.com");
  await page.getByRole("button", { name: "Add recipient" }).click();
  await page.getByRole("link", { name: "Next: add fields" }).click();
  await expect(page.getByText("Standard fields")).toBeVisible();
});

test("invalid signing token returns an access error", async ({ page }) => {
  const response = await page.goto("/sign/not-a-real-token/");
  expect(response.status()).toBe(403);
});
