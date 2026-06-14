/**
 * 契约一致性集成测试（node 环境，不启动 Electron）。
 *
 * 启动 mock-backend，然后用真实的前端 api.ts + eventReducer.ts 驱动一次完整流程：
 * 建线程 → 发消息 → 消费 NDJSON 事件 → 断言 activeThread 终态。
 *
 * 这覆盖了"前端契约消费者 ↔ 后端契约"的真实闭环，
 * 而无需 Playwright + Electron 的重型启动。
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import { pathToFileURL } from "node:url";
import path from "node:path";

// 把后端契约端口指向 mock；api.ts 读的是模块常量，无法 monkeypatch，
// 因此用 vi.stubEnv + 动态 import 触发其默认值。这里改用直接重写 fetch base。
// 更简单：让 mock 监听 7860（api.ts 的硬编码端口），避免改前端代码。

const MOCK_PORT = 7860;

function startMockBackend(): Promise<ChildProcess> {
	const serverPath = path.resolve(__dirname, "..", "mock-backend", "server.js");
	const child = spawn(process.execPath, [serverPath, String(MOCK_PORT)], {
		stdio: "pipe",
		env: { ...process.env },
	});
	return new Promise((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error("mock backend start timeout")), 5000);
		child.stdout!.on("data", (d) => {
			const msg = String(d);
			if (msg.includes("listening")) {
				clearTimeout(timer);
				resolve(child);
			}
		});
		child.on("error", (e) => {
			clearTimeout(timer);
			reject(e);
		});
	});
}

async function waitPortDown() {
	// 等端口释放，最多 3s
	for (let i = 0; i < 30; i++) {
		try {
			const r = await fetch(`http://127.0.0.1:${MOCK_PORT}/api/health`);
			await r.text();
			await new Promise((res) => setTimeout(res, 100));
		} catch {
			return;
		}
	}
}

describe("frontend contract against mock backend", () => {
	let backend: ChildProcess | null = null;

	beforeEach(async () => {
		backend = await startMockBackend();
	});

	afterEach(async () => {
		if (backend) {
			backend.kill("SIGKILL");
			backend = null;
		}
		await waitPortDown();
	});

	it("full thread lifecycle: create → message → done with papers", async () => {
		const { createThread, streamThreadMessage, applyEvent, withUserMessage } =
			await importIntegration();
		const meta = await createThread();
		expect(meta.id).toBeTruthy();

		let state = await loadDetailForTest(meta.id);
		state = withUserMessage(state, "transformer 论文");

		const seenTypes: string[] = [];
		await streamThreadMessage(meta.id, { query: "transformer 论文", api_key: "sk-test" }, (env) => {
			seenTypes.push(env.type);
			state = applyEvent(state, env);
		});

		// 事件序列覆盖关键阶段
		expect(seenTypes).toContain("searching_done");
		expect(seenTypes).toContain("report");
		expect(seenTypes[seenTypes.length - 1]).toBe("done");

		// 终态：结构化 papers 已进入线程
		expect(state.status).toBe("done");
		expect(state.papers.length).toBeGreaterThanOrEqual(1);
		expect(state.papers[0].title).toBe("Attention Is All You Need");
		expect(state.report).toContain("检索报告");
	});
});

// 动态 import 前端模块（api.ts 导入的是相对 API_BASE_URL，硬编码 7860）
async function importIntegration() {
	// vitest 在 node 环境下可直接 import ts
	const api = await import("../../src/mainview/api");
	const reducer = await import("../../src/mainview/eventReducer");
	return {
		createThread: api.createThread,
		streamThreadMessage: api.streamThreadMessage,
		applyEvent: reducer.applyEvent,
		withUserMessage: reducer.withUserMessage,
	};
}

async function loadDetailForTest(id: string) {
	const api = await import("../../src/mainview/api");
	return api.getThread(id);
}
