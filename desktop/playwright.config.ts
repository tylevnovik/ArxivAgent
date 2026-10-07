import { defineConfig } from "@playwright/test";

export default defineConfig({
	testDir: "./tests/e2e",
	timeout: 60000,
	expect: { timeout: 15000 },
	retries: 0,
	// 串行执行：app.spec 与 backend-flow 都独占 7860 端口（mock 后端 /
	// harness 真实后端），并发 worker 会互相抢占端口。
	workers: 1,
	// retain-on-failure：首次失败也留下 trace，便于 CI 上传与事后诊断
	// （此前 on-first-retry 与 retries=0 组合永远不会产生 trace）。
	use: { trace: "retain-on-failure" },
});
