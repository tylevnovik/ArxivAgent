import { describe, expect, it } from "vitest";
import type { AgentEventEnvelope, ThreadDetail } from "./api";
import { applyEvent, deriveProgress, withUserMessage } from "./eventReducer";

function emptyThread(id = "t1"): ThreadDetail {
  return {
    id,
    title: "新对话",
    status: "idle",
    created_at: "",
    updated_at: "",
    messages: [],
    papers: [],
    report: "",
    evidence: [],
    last_error: null,
  };
}

const env = (
  type: AgentEventEnvelope["type"],
  message = "",
  payload?: Record<string, unknown>,
): AgentEventEnvelope => ({
  type,
  message,
  payload,
  timestamp: "2024-01-01T00:00:00",
});

describe("applyEvent", () => {
  it("user message is added optimistically", () => {
    const s = withUserMessage(emptyThread(), "hello");
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]).toMatchObject({ role: "user", content: "hello" });
    expect(s.status).toBe("running");
  });

  it("thinking events show a compact status instead of raw model JSON", () => {
    let s = emptyThread();
    s = applyEvent(s, env("thinking", "Hello "));
    s = applyEvent(s, env("thinking", "world"));
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0].content).toBe("正在分析检索需求与检索策略…");
    expect(s.messages[0].kind).toBe("status");
  });

  it("report tokens accumulate and update report field", () => {
    let s = emptyThread();
    s = applyEvent(s, env("report", "# Title"));
    s = applyEvent(s, env("report", " body"));
    expect(s.report).toBe("# Title body");
    expect(s.messages.filter((m) => m.kind === "report")).toHaveLength(1);
    expect(s.messages.find((m) => m.kind === "report")?.content).toBe("# Title body");
  });

  it("searching_done replaces papers with structured array", () => {
    let s = emptyThread();
    s = applyEvent(s, env("searching_done", "done", { papers: [{ title: "P1", authors: [] }] }));
    expect(s.papers).toHaveLength(1);
    expect(s.papers[0].title).toBe("P1");
  });

  it("done terminal sets status and carries final papers", () => {
    let s = emptyThread();
    s = applyEvent(s, env("done", "✅", { kind: "search", papers: [{ title: "Final", authors: [] }], report: "R" }));
    expect(s.status).toBe("done");
    expect(s.papers[0].title).toBe("Final");
    expect(s.report).toBe("R");
    expect(s.last_error).toBeNull();
  });

  it("error terminal sets error status and last_error", () => {
    let s = emptyThread();
    s = applyEvent(s, env("error", "❌ boom"));
    expect(s.status).toBe("error");
    expect(s.last_error).toBe("❌ boom");
    expect(s.messages[s.messages.length - 1]?.kind).toBe("error");
  });

  it("cancelled terminal sets cancelled status", () => {
    let s = emptyThread();
    s = applyEvent(s, env("cancelled", "已停止"));
    expect(s.status).toBe("cancelled");
  });

  it("done chat kind does not overwrite papers", () => {
    let s = emptyThread();
    s = applyEvent(s, env("searching_done", "ok", { papers: [{ title: "Kept", authors: [] }] }));
    s = applyEvent(s, env("chat", "thanks"));
    s = applyEvent(s, env("done", "✅", { kind: "chat" }));
    expect(s.papers[0].title).toBe("Kept");
  });

  it("done search writes structured evidence into state", () => {
    let s = emptyThread();
    s = applyEvent(
      s,
      env("done", "✅", {
        kind: "search",
        papers: [{ title: "P", authors: [] }],
        report: "R",
        evidence: [
          {
            paper_title: "Evidence Paper",
            arxiv_id: "2401.00001",
            chunk_index: "2",
            text: "supporting text",
            retrieval_sources: ["dense"],
            dense_score: 0.8,
            bm25_score: 0,
            hybrid_score: 0,
            rerank_score: 0,
            score: 0,
          },
        ],
      }),
    );
    expect(s.evidence).toHaveLength(1);
    expect(s.evidence[0].paper_title).toBe("Evidence Paper");
    expect(s.evidence[0].chunk_index).toBe("2");
  });

  it("status messages collapse consecutive updates into one row", () => {
    let s = emptyThread();
    s = applyEvent(s, env("searching", "step A"));
    s = applyEvent(s, env("searching", "step B"));
    const statusMsgs = s.messages.filter((m) => m.kind === "status");
    expect(statusMsgs).toHaveLength(1);
    expect(statusMsgs[0].content).toBe("step B");
  });
});

// ===================== deriveProgress（进度时间线推导） =====================

const penv = (
  type: AgentEventEnvelope["type"],
  opts: { message?: string; step?: string; round?: number; papers?: unknown[] } = {},
): AgentEventEnvelope => ({
  type,
  message: opts.message ?? "",
  payload: {
    ...(opts.step !== undefined ? { step: opts.step } : {}),
    ...(opts.papers !== undefined ? { papers: opts.papers } : {}),
  },
  timestamp: "2024-01-01T00:00:00",
  round: opts.round ?? null,
});

describe("deriveProgress", () => {
  it("starts at parse and maps intent steps to stages", () => {
    let p = deriveProgress(null, penv("intent", { step: "理解需求", message: "分析中" }));
    expect(p?.stage).toBe("parse");
    expect(p?.isSearchFlow).toBe(false);
    p = deriveProgress(p, penv("intent", { step: "解析正文", message: "进度 1/3" }));
    expect(p?.stage).toBe("pdf");
    expect(p?.detail).toBe("进度 1/3");
  });

  it("tracks search rounds and per-round paper counts", () => {
    let p = deriveProgress(null, penv("searching", { round: 1, message: "检索中" }));
    expect(p?.stage).toBe("search");
    expect(p?.round).toBe(1);
    expect(p?.isSearchFlow).toBe(true);
    p = deriveProgress(p, penv("searching_done", { round: 1, papers: [{}, {}, {}], message: "3 篇" }));
    expect(p?.roundPapers).toEqual([3]);
    p = deriveProgress(p, penv("searching", { round: 2, message: "第 2 轮" }));
    p = deriveProgress(p, penv("searching_done", { round: 2, papers: [{}], message: "1 篇" }));
    expect(p?.round).toBe(2);
    expect(p?.roundPapers).toEqual([3, 1]);
  });

  it("maps reviewing/refining to the review stage and report tokens to report", () => {
    let p = deriveProgress(null, penv("reviewing", { message: "审核完成" }));
    expect(p?.stage).toBe("review");
    p = deriveProgress(p, penv("refining", { message: "已优化" }));
    expect(p?.stage).toBe("review");
    p = deriveProgress(p, penv("report", { message: "# 报告" }));
    expect(p?.stage).toBe("report");
  });

  it("terminal events clear progress", () => {
    let p = deriveProgress(null, penv("searching", { round: 1 }));
    expect(p).not.toBeNull();
    expect(deriveProgress(p, penv("done"))).toBeNull();
    expect(deriveProgress(p, penv("error"))).toBeNull();
    expect(deriveProgress(p, penv("cancelled"))).toBeNull();
  });

  it("follow-up rebuild + chat reply stays a non-search flow", () => {
    let p = deriveProgress(null, penv("intent", { step: "恢复正文索引", message: "恢复中" }));
    expect(p?.stage).toBe("pdf");
    expect(p?.isSearchFlow).toBe(false);
    p = deriveProgress(p, penv("chat", { message: "回答 token" }));
    expect(p?.stage).toBe("report");
    expect(p?.isSearchFlow).toBe(false);
  });

  it("thinking events do not change the stage", () => {
    let p = deriveProgress(null, penv("searching", { round: 1 }));
    p = deriveProgress(p, penv("thinking", { message: "..." }));
    expect(p?.stage).toBe("search");
  });
});
