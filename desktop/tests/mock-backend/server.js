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
};

function ndjson(res, events) {
	res.writeHead(200, { "Content-Type": "application/x-ndjson" });
	for (const e of events) {
		res.write(JSON.stringify(e) + "\n");
	}
	res.end();
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
			return json_(res, 200, {
				ok: true,
				api_key_configured: true,
				api_key_source: "env",
				provider: "deepseek",
				endpoint: "https://api.deepseek.com",
				model: "deepseek-v4-flash",
				data_dir: "/tmp/mock",
				llm_reachable: true,
				llm_detail: "ok",
				providers: [{ name: "arxiv", ok: true, detail: "ok" }],
			});
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
				return json_(res, 200, {
					...threadMeta(tid),
					messages: detailMessages(t),
					papers: t.papers,
					report: t.report,
					evidence: t.evidence || [],
				});
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
				if (!json.api_key) {
					return json_(res, 400, errBody("no_api_key", "未提供 API Key", true));
				}
				// 脚本化事件序列
				t.status = "running";
				t.messages.push({ role: "user", content: json.query, timestamp: now(), kind: "text" });
				return ndjson(res, scriptedEvents(t));
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
				if (t) t.status = "cancelled";
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
	t.status = "done";
	t.evidence = [MOCK_EVIDENCE];
	t.messages.push({ role: "assistant", content: t.report, timestamp: now(), kind: "report" });
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
			payload: { kind: "search", papers: MOCK_PAPERS, report: t.report, evidence: [MOCK_EVIDENCE] },
		},
	];
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
