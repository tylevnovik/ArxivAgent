import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		// 单测 + 集成测试；e2e 由 playwright 独立运行（见 playwright.config.ts）
		include: ["src/**/*.test.{ts,tsx}", "tests/integration/**/*.spec.{ts,tsx}"],
		exclude: ["tests/e2e/**", "node_modules/**", "dist/**", "build/**"],
		globals: false,
		// 默认 DOM 环境（单测用 localStorage/window）；
		// 集成测试在纯 node 跑（fetch + child_process，不需要 DOM）。
		environment: "jsdom",
		environmentMatchGlobs: [["tests/integration/**", "node"]],
	},
});
