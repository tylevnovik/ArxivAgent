/**
 * E2E mock 后端：纯 Node http，无 Python 依赖。
 *
 * 提供与真实后端一致的契约端点，但用固定脚本化的 NDJSON 响应。
 * 用途：Playwright E2E 驱动真实 Electron 窗口时，让它指向本 mock，
 * 避免 E2E 依赖真实 LLM / 检索源。
 *
 * 启动：node tests/mock-backend/server.js [port]
 * 默认端口 7861（避开真实后端的 7860）。
 */
const http = require("node:http");
const fs = require("node:fs");

const PORT = parseInt(process.argv[2] || "7861", 10);

// 内存线程存储
const threads = new Map();

// 测试控制：接下来 count 次 GET /api/threads/:id 延迟 ms 毫秒返回
// （由 POST /__mock/detail-delay 设置，用于 T1 乱序详情 E2E）。
const detailDelay = { ms: 0, count: 0 };

function threadMeta(id) {
	const t = threads.get(id);
	if (!t) return null;
	return {
		id: t.id,
		title: t.title,
		status: t.status,
		created_at: t.created_at,
		updated_at: t.updated_at,
		papers_count: t.papers.length,
		has_report: Boolean(t.report),
		message_count: t.messages.length,
		last_error: t.last_error,
	};
}

function newThread(title) {
	const id = "mock-" + Date.now() + "-" + Math.floor(Math.random() * 1000);
	const now = new Date().toISOString();
	const t = {
		id,
		title: title || "新对话",
		status: "idle",
		created_at: now,
		updated_at: now,
		messages: [],
		papers: [],
		report: "",
		evidence: [],
		citation_check: { total: 0, matched: 0, unmatched: [], all_matched: true },
		last_error: null,
	};
	threads.set(id, t);
	return t;
}

function detailMessages(t) {
	return t.messages.map((m, index) => ({
		id: m.id || `${t.id}:${index}`,
		persisted_index: index,
		role: m.role,
		content: m.content,
		timestamp: m.timestamp,
		kind: m.kind || "text",
	}));
}

const MOCK_PAPER = {
	title: "Attention Is All You Need",
	authors: ["Vaswani", "Shazeer"],
	abstract: "We propose a new architecture based solely on attention mechanisms.",
	categories: ["cs.CL"],
	published: "2017-06-12",
	updated: "2017-06-12",
	link: "https://arxiv.org/abs/1706.03762",
	pdf_link: "https://arxiv.org/pdf/1706.03762",
	arxiv_id: "1706.03762",
	source: "arxiv",
	source_id: "1706.03762",
	doi: "",
	citation_count: 100000,
	score: 0.99,
	reason: "foundational",
};

const MOCK_PAPERS = Array.from({ length: 18 }, (_, i) => ({
	...MOCK_PAPER,
	title: i === 0 ? MOCK_PAPER.title : `Mock RAG Survey Paper ${i + 1}`,
	abstract:
		i === 0
			? MOCK_PAPER.abstract
			: "A mock paper used to create enough research-panel content for scroll verification. ".repeat(3),
	arxiv_id: i === 0 ? MOCK_PAPER.arxiv_id : `2501.${String(i + 1).padStart(5, "0")}`,
	source_id: i === 0 ? MOCK_PAPER.source_id : `2501.${String(i + 1).padStart(5, "0")}`,
	link: i === 0 ? MOCK_PAPER.link : `https://arxiv.org/abs/2501.${String(i + 1).padStart(5, "0")}`,
	pdf_link:
		i === 0 ? MOCK_PAPER.pdf_link : `https://arxiv.org/pdf/2501.${String(i + 1).padStart(5, "0")}`,
	citation_count: i === 0 ? MOCK_PAPER.citation_count : 100 - i,
}));

const MOCK_EVIDENCE = {
	paper_title: "Attention Is All You Need",
	arxiv_id: "1706.03762",
	chunk_index: 0,
	text: "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.",
	retrieval_sources: ["dense", "bm25"],
	dense_score: 0.82,
	bm25_score: 3.1,
	hybrid_score: 0.0001,
	rerank_score: 0.0,
	score: 0.0001,
	page_number: 2,
	page_end: 2,
	section_title: "1. Introduction",
	source_url: "https://arxiv.org/abs/1706.03762",
	pdf_url: "https://arxiv.org/pdf/1706.03762",
};

function ndjson(res, events, { delayMs = 0, onComplete, onClose } = {}) {
	res.writeHead(200, { "Content-Type": "application/x-ndjson" });
	let index = 0;
	let timer = null;
	let closed = false;
	const finish = (callback) => {
		if (closed) return;
		closed = true;
		if (timer) clearTimeout(timer);
		callback?.();
	};
	const sendNext = () => {
		if (closed || res.writableEnded) return;
		if (index >= events.length) {
			res.end();
			finish(onComplete);
			return;
		}
		res.write(JSON.stringify(events[index++]) + "\n");
		if (delayMs > 0) timer = setTimeout(sendNext, delayMs);
		else sendNext();
	};
	res.on("close", () => {
		if (!closed) finish(onClose);
	});
	sendNext();
}

const server = http.createServer((req, res) => {
	const url = new URL(req.url, `http://localhost:${PORT}`);
	const path = url.pathname;
	let body = "";
	req.on("data", (c) => (body += c));
	req.on("end", () => {
		const json = body ? JSON.parse(body) : {};

		// ---------- health ----------
		if (path === "/api/health") {
			return json_(res, 200, { ok: true, version: "0.2.0-mock" });
		}
		if (path === "/api/config/health" && (req.method === "GET" || req.method === "POST")) {
			const invalidEndpoint = String(json.base_url || "").startsWith("javascript:");
			return json_(res, 200, {
				ok: !invalidEndpoint,
				api_key_configured: true,
				api_key_source: "env",
				provider: "deepseek",
				endpoint: "https://api.deepseek.com",
				model: "deepseek-v4-flash",
				data_dir: "/tmp/mock",
				llm_reachable: invalidEndpoint ? false : true,
				llm_detail: invalidEndpoint ? "API endpoint 无效" : "ok",
				providers: [{ name: "arxiv", ok: true, detail: "ok" }],
			});
		}

			// ---------- 测试控制端点（仅 E2E 使用） ----------
			if (path === "/__mock/detail-delay" && req.method === "POST") {
				detailDelay.ms = Number(json.ms) || 0;
				detailDelay.count = Number(json.count) || 1;
				return json_(res, 200, { ok: true, ...detailDelay });
			}

			// ---------- threads ----------
		if (path === "/api/threads" && req.method === "GET") {
			return json_(res, 200, { ok: true, threads: [...threads.keys()].map(threadMeta) });
		}
		if (path === "/api/threads" && req.method === "POST") {
			const t = newThread(json.title);
			return json_(res, 200, { ok: true, thread: threadMeta(t.id) });
		}

		const m = path.match(/^\/api\/threads\/([^/]+)(?:\/(.+))?$/);
		if (m) {
			const tid = m[1];
			const sub = m[2];
			const t = threads.get(tid);

			if (sub === undefined && req.method === "GET") {
				if (!t) return json_(res, 404, errBody("not_found", "线程不存在", false));
				const payload = {
					...threadMeta(tid),
					messages: detailMessages(t),
					papers: t.papers,
					report: t.report,
					evidence: t.evidence || [],
				};
				if (detailDelay.count > 0 && detailDelay.ms > 0) {
					detailDelay.count -= 1;
					const ms = detailDelay.ms;
					return setTimeout(() => json_(res, 200, payload), ms);
				}
				return json_(res, 200, payload);
			}
			if (sub === undefined && req.method === "PATCH") {
				if (!t) return json_(res, 404, errBody("not_found", "线程不存在", false));
				t.title = json.title;
				return json_(res, 200, threadMeta(tid));
			}
			if (sub === undefined && req.method === "DELETE") {
				threads.delete(tid);
				return json_(res, 200, { ok: true, status: "deleted" });
			}
			if (sub === "messages" && req.method === "POST") {
				if (!t) return json_(res, 404, errBody("not_found", "线程不存在", false));
				if (!json.query || !json.query.trim()) {
					return json_(res, 400, errBody("validation", "query 不能为空", true));
				}
				const keylessProvider = ["ollama", "vllm", "custom"].includes(String(json.provider || "").toLowerCase());
				if (!json.api_key && !keylessProvider) {
					return json_(res, 400, errBody("no_api_key", "未提供 API Key", true));
				}
				// 脚本化事件序列
				t.status = "running";
				t.messages.push({ role: "user", content: json.query, timestamp: now(), kind: "text" });
				const slow = String(json.query).includes("[slow]");
				// [sticky-cancel] / [sticky-cancel-<ms>]：取消已受理但 worker 仍在
				// 收尾——cancel 后线程保持 running 一段时间再转 cancelled，
				// 用于验证 UI 在上游阻塞时显示"正在停止"而不是立即声称已停止。
				const stickyMatch = String(json.query).match(/\[sticky-cancel(?:-(\d+))?\]/);
				if (stickyMatch) {
					t.stickyCancelUntil = Date.now() + (stickyMatch[1] ? parseInt(stickyMatch[1], 10) : 2500);
				}
				return ndjson(res, scriptedEvents(t), {
					delayMs: slow || stickyMatch ? 120 : 0,
					onComplete: () => finishScriptedThread(t),
					onClose: () => scheduleCancel(t),
				});
			}
			const msgMatch = sub && sub.match(/^messages\/(\d+)$/);
			if (msgMatch && req.method === "PATCH") {
				if (!t) return json_(res, 404, errBody("not_found", "线程不存在", false));
				const index = parseInt(msgMatch[1], 10);
				if (!t.messages[index]) return json_(res, 404, errBody("not_found", "消息不存在", false));
				if (!json.content || !String(json.content).trim()) {
					return json_(res, 400, errBody("validation", "消息内容不能为空", true));
				}
				const old = t.messages[index].content;
				t.messages[index].content = String(json.content).trim();
				if (old === t.report) t.report = t.messages[index].content;
				return json_(res, 200, {
					...threadMeta(tid),
					messages: detailMessages(t),
					papers: t.papers,
					report: t.report,
					evidence: t.evidence || [],
				});
			}
			if (msgMatch && req.method === "DELETE") {
				if (!t) return json_(res, 404, errBody("not_found", "线程不存在", false));
				const index = parseInt(msgMatch[1], 10);
				if (!t.messages[index]) return json_(res, 404, errBody("not_found", "消息不存在", false));
				const old = t.messages[index].content;
				t.messages.splice(index, 1);
				if (old === t.report) t.report = "";
				return json_(res, 200, {
					...threadMeta(tid),
					messages: detailMessages(t),
					papers: t.papers,
					report: t.report,
					evidence: t.evidence || [],
				});
			}
			if (sub === "cancel" && req.method === "POST") {
				if (t) scheduleCancel(t);
				return json_(res, 200, { ok: true, status: "cancel requested" });
			}
			if (sub === "papers" && req.method === "GET") {
				return json_(res, 200, { ok: true, papers: t ? t.papers : [] });
			}
			if (sub === "report" && req.method === "GET") {
				return json_(res, 200, { ok: true, report: t ? t.report : "" });
			}
			if (sub === "export" && req.method === "POST") {
				// 真实写一个文件供下载校验
				const content = "# Mock export\n";
				const fname = `mock_${json.type}_${Date.now()}.md`;
				const dir = process.env.MOCK_EXPORT_DIR || ".";
				fs.writeFileSync(require("node:path").join(dir, fname), content);
				return json_(res, 200, { ok: true, filename: fname, status: "✅ 已导出" });
			}
		}

		// ---------- download ----------
		if (path === "/api/download") {
			const fname = require("node:path").basename(url.searchParams.get("file") || "");
			const dir = process.env.MOCK_EXPORT_DIR || ".";
			const fp = require("node:path").join(dir, fname);
			if (fs.existsSync(fp)) {
				res.writeHead(200, { "Content-Type": "application/octet-stream" });
				return res.end(fs.readFileSync(fp));
			}
			return json_(res, 404, errBody("not_found", "文件不存在", false));
		}

		json_(res, 404, errBody("not_found", "unknown route " + path, false));
	});
});

function scriptedEvents(t) {
	// 模拟一次完整检索流：intent → thinking → searching → searching_done → report → done
	t.papers = MOCK_PAPERS;
	t.report = "# 检索报告\n\nTransformer 是基础架构【正文: Attention Is All You Need | 分块 0】。";
	t.evidence = [MOCK_EVIDENCE];
	t.citation_check = { total: 1, matched: 1, unmatched: [], all_matched: true };
	return [
		{ type: "intent", message: "Agent 已启动…", timestamp: now() },
		{ type: "thinking", message: "分析检索需求…", timestamp: now() },
		{ type: "searching", message: "检索 arXiv 中…", timestamp: now() },
		{ type: "searching_done", message: "获得 18 篇论文", timestamp: now(), payload: { papers: MOCK_PAPERS } },
		{ type: "report", message: t.report, timestamp: now() },
		{
			type: "done",
			message: "✅ 检索完成！",
			timestamp: now(),
			payload: {
				kind: "search",
				papers: MOCK_PAPERS,
				report: t.report,
				evidence: [MOCK_EVIDENCE],
				citation_check: t.citation_check,
			},
		},
	];
}

function finishScriptedThread(t) {
	t.status = "done";
	if (!t.messages.some((message) => message.kind === "report" && message.content === t.report)) {
		t.messages.push({ role: "assistant", content: t.report, timestamp: now(), kind: "report" });
	}
}

/**
 * 取消落地：sticky 窗口内保持 running（模拟 worker 收尾/上游阻塞），
 * 窗口结束后才转 cancelled。幂等：窗口计时器只挂一个。
 */
function scheduleCancel(t) {
	const remaining = (t.stickyCancelUntil || 0) - Date.now();
	if (remaining > 0) {
		if (!t.stickyTimer) {
			t.stickyTimer = setTimeout(() => {
				t.status = "cancelled";
				t.stickyTimer = null;
			}, remaining);
		}
		return;
	}
	t.status = "cancelled";
}

function json_(res, status, obj) {
	res.writeHead(status, { "Content-Type": "application/json" });
	res.end(JSON.stringify(obj));
}
function errBody(code, message, recoverable) {
	return { ok: false, error: { code, message, recoverable } };
}
function now() {
	return new Date().toISOString();
}

server.listen(PORT, () => {
	console.log(`[mock-backend] listening on http://127.0.0.1:${PORT}`);
});

module.exports = server;
