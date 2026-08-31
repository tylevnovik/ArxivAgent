/**
 * Playwright Electron E2E。
 *
 * 流程：启动 mock-backend（端口 7860）→ 用 _electron 启动真实 Electron
 * 窗口（指向 dist/）→ 执行 UI workflow → 断言。
 *
 * 前置：需要先 `bun run build` 生成 dist/。
 * 运行：`bun run test:e2e`
 *
 * 注意：mock-backend 监听 7860（与 api.ts 硬编码端口一致），
 * 这样不需要改前端代码或注入 env。运行前请确保真实后端未占用 7860。
 *
 * 已知环境限制：Playwright 的 _electron launcher 会向 Electron 进程注入
 * --remote-debugging-port；与某些 Electron 版本组合时不兼容。
 * 此外本开发机的 electron 二进制以 Node 模式启动（非 Electron runtime），
 * 导致 require("electron").app 为 undefined。两者均为环境/安装问题，
 * 非测试代码缺陷。契约闭环由 tests/integration/contract.spec.ts 兜底
 * （它用真实 api.ts + eventReducer.ts 驱动 mock 后端，不依赖 Electron）。
 *
 * 在 Electron 正常安装的环境下，本 spec 应可直接运行。
 */
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { test, expect, _electron as electron } from "@playwright/test";

let backend: ChildProcess | null = null;

test.beforeAll(async () => {
	const serverPath = path.resolve(__dirname, "..", "mock-backend", "server.js");
	backend = spawn(process.execPath, [serverPath, "7860"], { stdio: "pipe" });
	await new Promise<void>((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error("mock backend timeout")), 5000);
		backend!.stdout!.on("data", (d) => {
			if (String(d).includes("listening")) {
				clearTimeout(timer);
				resolve();
			}
		});
		backend!.on("error", reject);
	});
});

test.afterAll(() => {
	if (backend) backend.kill("SIGKILL");
});

test("app boots, renders composer, and completes a search", async () => {
	const electronApp = await electron.launch({
		args: [path.resolve(__dirname, "..", "..", "src", "electron", "main.cjs")],
		env: { ...process.env, ARXIV_AGENT_E2E: "1" },
	});
	const window = await electronApp.firstWindow();
	await window.waitForLoadState("domcontentloaded");

	// 1. 健康可达（mock 已起）+ composer 渲染
	await expect(window.locator("textarea").first()).toBeVisible({ timeout: 15000 });

	// 2. 输入并发送
	await window.locator("textarea").first().fill("transformer 论文");
	await window.locator('button[aria-label="Send message"]').click();

	// 3. 等待终态：研究资料面板应出现文献卡片（来自结构化 papers）
	await expect(window.getByText("Attention Is All You Need")).toBeVisible({ timeout: 15000 });
	await window.getByRole("tab", { name: /文献/ }).click();
	await window.getByText("Mock RAG Survey Paper 18").waitFor({ state: "attached" });

	const researchScroll = await window.locator(".research-panel-scroll").evaluate((el) => ({
		clientHeight: el.clientHeight,
		scrollHeight: el.scrollHeight,
		overflowY: getComputedStyle(el).overflowY,
	}));
	expect(researchScroll.scrollHeight).toBeGreaterThan(researchScroll.clientHeight);
	expect(researchScroll.overflowY).toMatch(/auto|scroll/);

	await expect(window.locator("textarea").first()).toBeVisible();
	await expect(window.getByLabel("复制消息").first()).toBeVisible();
	await expect(window.getByLabel("编辑消息").first()).toBeVisible();
	await expect(window.getByLabel("删除消息").first()).toBeVisible();

	// 证据元数据应进入真实研究资料面板，而不只存在于 API payload。
	await window.getByRole("tab", { name: "报告" }).click();
	await expect(window.getByText("页码 2")).toBeVisible();
	await expect(window.getByText("章节 1. Introduction")).toBeVisible();
	await expect(window.getByText("打开原文")).toBeVisible();

	await electronApp.close();
});

test("can cancel a slow search without leaving the composer stuck", async () => {
	const electronApp = await electron.launch({
		args: [path.resolve(__dirname, "..", "..", "src", "electron", "main.cjs")],
		env: { ...process.env, ARXIV_AGENT_E2E: "1" },
	});
	const window = await electronApp.firstWindow();
	await window.waitForLoadState("domcontentloaded");
	await expect(window.locator("textarea").first()).toBeVisible({ timeout: 15000 });

	await window.locator("textarea").first().fill("[slow] cancel this search");
	await window.getByRole("button", { name: "Send message" }).click();
	await expect(window.getByRole("button", { name: "Stop search" })).toBeVisible();
	await window.getByRole("button", { name: "Stop search" }).click();
	await expect(window.getByRole("button", { name: "Send message" })).toBeVisible({ timeout: 10000 });
	await expect(window.getByText("已停止当前检索", { exact: true }).first()).toBeVisible();

	await electronApp.close();
});

test("can rename, switch away from, and delete a thread", async () => {
	const electronApp = await electron.launch({
		args: [path.resolve(__dirname, "..", "..", "src", "electron", "main.cjs")],
		env: { ...process.env, ARXIV_AGENT_E2E: "1" },
	});
	const window = await electronApp.firstWindow();
	await window.waitForLoadState("domcontentloaded");
	await expect(window.locator("textarea").first()).toBeVisible({ timeout: 15000 });

	await window.getByRole("button", { name: "新建检索" }).click();
	await expect(window.getByRole("button", { name: "线程 新对话" }).last()).toBeVisible();
	await window.getByRole("button", { name: /重命名 新对话/ }).last().click();
	const titleInput = window.locator('input[type="text"]').last();
	await titleInput.fill("First thread");
	await titleInput.press("Enter");
	await expect(window.getByText("First thread", { exact: true }).first()).toBeVisible();

	await window.getByRole("button", { name: "新建检索" }).click();
	await window.getByRole("button", { name: /重命名 新对话/ }).last().click();
	await window.locator('input[type="text"]').last().fill("Second thread");
	await window.locator('input[type="text"]').last().press("Enter");
	await expect(window.getByText("Second thread", { exact: true }).first()).toBeVisible();

	// 第一线程在运行中时切到第二线程；旧流不得把新线程 UI 重置。
	await window.getByRole("button", { name: "线程 First thread" }).click();
	await window.locator("textarea").first().fill("[slow] switch away");
	await window.getByRole("button", { name: "Send message" }).click();
	await expect(window.getByRole("button", { name: "Stop search" })).toBeVisible();
	await window.getByRole("button", { name: "线程 Second thread" }).click();
	await expect(window.getByText("Second thread", { exact: true }).first()).toBeVisible();
	await expect(window.getByRole("button", { name: "Stop search" })).toHaveCount(0);

	// 删除当前线程，确认应用回到一个可用线程，而不是卡在已删除对象。
	window.once("dialog", (dialog) => dialog.accept());
	await window.getByRole("button", { name: "删除 Second thread" }).click();
	await expect(window.getByText("First thread", { exact: true }).first()).toBeVisible({ timeout: 10000 });

	await electronApp.close();
});
