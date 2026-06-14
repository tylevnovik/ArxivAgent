import { defineConfig } from "@playwright/test";

export default defineConfig({
	testDir: "./tests/e2e",
	timeout: 60000,
	expect: { timeout: 15000 },
	retries: 0,
	use: { trace: "on-first-retry" },
});
