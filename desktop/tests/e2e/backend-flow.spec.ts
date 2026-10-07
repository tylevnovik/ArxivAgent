/**
 * 真实后端 + 真实 Electron 全链路 E2E（T7）。
 *
 * 与 app.spec.ts 的差别：这里启动**真实 FastAPI 后端**（路由、worker、
 * 持久化、HMAC 认证链全部真实），由 desktop/tests/e2e/backend_harness.py
 * 仅替换外部边界（LLM 流、检索源、PDF 下载、embedding 模型——均确定性、
 * 无网络、无付费调用）。
 *
 * 数据链路：每个测试使用全新临时 DATA_DIR（ARXIV_AGENT_E2E_DATA_DIR），
 * Electron 主进程经 ARXIV_AGENT_E2E_BACKEND_ENTRY 以 harness 作为后端入口
 * spawn（生产默认仍是 app.py），认证凭证从其 stdout 捕获。
 *
 * 覆盖用户路径：UI 检索 → 正文证据 → 导出 → 关闭重启 → 历史恢复。
 *
 * 端口：7860 预检失败（被占用）时快速报错说明，不杀任何进程。
 */
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { test, expect, _electron as electron, type Page } from "@playwright/test";

const MAIN_CJS = path.resolve(__dirname, "..", "..", "src", "electron", "main.cjs");
const HARNESS_PATH = path.resolve(__dirname, "backend_harness.py");

/** 端口被占用时快速报错（说明占用可能来源），绝不终止占用进程。 */
async function assertPortFree(port: number): Promise<void> {
	await new Promise<void>((resolve, reject) => {
		const probe = net.createServer();
		probe.once("error", (err: NodeJS.ErrnoException) => {
			if (err.code === "EADDRINUSE") {
				reject(
					new Error(
						`端口 ${port} 已被占用（可能是正在运行的 ArxivAgent 后端或其他服务）。` +
							"测试未启动、也未终止任何进程；请释放端口后重试。",
					),
				);
				return;
			}
			reject(err);
		});
		probe.once("listening", () => probe.close(() => resolve()));
		probe.listen(port, "127.0.0.1");
	});
}

/** 等后端退出、端口释放（确保重启阶段启动的是全新实例）。 */
async function waitForPortFree(port: number, timeoutMs: number): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		try {
			await assertPortFree(port);
			return;
		} catch {
			await new Promise((r) => setTimeout(r, 300));
		}
	}
	throw new Error(`端口 ${port} 在超时内未释放，重启阶段无法安全启动新后端`);
}

test.describe("real backend flow", () => {
	test("UI search → evidence → export → restart → history restored", async () => {
		test.setTimeout(240000);
		await assertPortFree(7860);
		const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "arxivagent-e2e-data-"));
		const exportDir = fs.mkdtempSync(path.join(os.tmpdir(), "arxivagent-e2e-export-"));
		const launchEnv = {
			ARXIV_AGENT_E2E_DATA_DIR: dataDir,
			ARXIV_AGENT_E2E_EXPORT_DIR: exportDir,
			ARXIV_AGENT_E2E_BACKEND_ENTRY: HARNESS_PATH,
			PYTHONUNBUFFERED: "1",
		};

		/**
		 * 启动一次完整应用（Electron 主进程自行 spawn harness 后端并捕获
		 * HMAC 凭证），执行断言后 try/finally 关闭；随后确认本次启动的
		 * 后端已退出（端口释放）。
		 */
		const launchAndRun = async (assertions: (window: Page) => Promise<void>) => {
			const electronApp = await electron.launch({
				args: [MAIN_CJS],
				env: { ...process.env, ARXIV_AGENT_E2E: "1", ...launchEnv },
			});
			try {
				const window = await electronApp.firstWindow();
				await window.waitForLoadState("domcontentloaded");
				await expect(window.locator("textarea").first()).toBeVisible({ timeout: 30000 });
				await assertions(window);
			} finally {
				await electronApp.close();
			}
			await waitForPortFree(7860, 15000);
		};

		try {
			// ---------- 阶段一：检索 → 正文证据 ----------
			await launchAndRun(async (window) => {
				await window.locator("textarea").first().fill("transformer attention 论文检索");
				await window.getByRole("button", { name: "Send message" }).click();

				// 真实链路完成：文献卡片出现（结构化 papers）
				await expect(window.getByText("Attention Is All You Need")).toBeVisible({ timeout: 60000 });
				await window.getByRole("tab", { name: /文献/ }).click();
				await expect(window.getByText("Attention Is All You Need").first()).toBeVisible();

				// 正文证据（真实 RAG 链路：harness 伪向量 + 真实 qdrant:memory:/BM25）
				await window.getByRole("tab", { name: "报告" }).click();
				await expect(window.getByText(/E2E 检索报告/).first()).toBeVisible({ timeout: 30000 });
				await expect(window.getByText("页码 2").first()).toBeVisible();
				await expect(window.getByText("章节 1. Introduction").first()).toBeVisible();
			});

			// ---------- 阶段二：关闭重启 → 历史恢复 → UI 导出 ----------
			await launchAndRun(async (window) => {
				// 重启后历史恢复：线程标题来自首条用户消息（侧栏）
				await expect(
					window.getByText("transformer attention 论文检索", { exact: true }).first(),
				).toBeVisible({ timeout: 30000 });

				// 重启后研究面板初始为关闭态：先打开，再核对其余历史
				await window.getByRole("button", { name: "研究资料" }).click();
				await window.getByRole("tab", { name: /文献/ }).click();
				await expect(window.getByText("Attention Is All You Need").first()).toBeVisible();
				await window.getByRole("tab", { name: "报告" }).click();
				await expect(window.getByText(/E2E 检索报告/).first()).toBeVisible();

				// UI 导出报告：后端真实写盘 → 主进程桥复制到 exportDir
				await window.getByRole("button", { name: "导出报告" }).click();
				await expect(window.getByText("总结报告已导出")).toBeVisible({ timeout: 15000 });
			});

			// 导出产物与持久化数据都真实落盘（后端导出文件名为中文：最终报告_*.md）
			expect(fs.readdirSync(exportDir).some((f) => f.startsWith("最终报告") && f.endsWith(".md"))).toBe(true);
			expect(fs.readdirSync(path.join(dataDir, "threads")).some((f) => f.endsWith(".json"))).toBe(true);
		} finally {
			fs.rmSync(dataDir, { recursive: true, force: true });
			fs.rmSync(exportDir, { recursive: true, force: true });
		}
	});

	test("port 7860 occupied fails fast without killing anything", async () => {
		// 占用端口的假服务：测试自有子进程，测试结束自行清理
		const occupier = spawn(
			process.execPath,
			["-e", "require('node:http').createServer((q,s)=>s.end('ok')).listen(7860,'127.0.0.1')"],
			{ stdio: "ignore" },
		);
		try {
			await new Promise((r) => setTimeout(r, 600));
			await expect(assertPortFree(7860)).rejects.toThrow(/7860 已被占用/);
		} finally {
			occupier.kill("SIGKILL");
		}
	});
});
