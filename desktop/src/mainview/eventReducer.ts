/**
 * 活动线程的事件 reducer。
 *
 * 输入：流式 NDJSON 事件信封（AgentEventEnvelope）。
 * 输出：ThreadDetail 的增量演进（消息 / 论文 / 报告 / 状态）。
 *
 * 关键规则：
 * - thinking/report/chat 是流式 token，需累积到最后一条助手消息上。
 * - searching_done / done(payload.papers) 把结构化论文替换进 papers。
 * - done/error/cancelled 是终态，置 status。
 * - intent/searching/reviewing/refining 是状态提示，作为一条可折叠的"思考/状态"消息。
 */
import type {
  AgentEventEnvelope,
  ChatMessage,
  Paper,
  ThreadDetail,
  ThreadStatus,
} from "./api";

// 把流式 token 累积到末尾的助手消息（同 kind），或新建一条。
function appendStreaming(
  messages: ChatMessage[],
  kind: ChatMessage["kind"],
  token: string,
  statusHint: string,
): ChatMessage[] {
  const copy = [...messages];
  const last = copy[copy.length - 1];
  if (last && last.role === "assistant" && last.kind === kind) {
    copy[copy.length - 1] = { ...last, content: last.content + token };
    return copy;
  }
  copy.push({
    role: "assistant",
    content: token || statusHint,
    timestamp: new Date().toISOString(),
    kind,
  });
  return copy;
}

// 把一条状态/步骤提示作为可折叠思考消息追加（避免重复刷屏同类提示）。
function appendStatus(messages: ChatMessage[], content: string): ChatMessage[] {
  if (!content.trim()) return messages;
  const last = messages[messages.length - 1];
  // 合并连续的状态消息
  if (last && last.role === "assistant" && last.kind === "status") {
    return [...messages.slice(0, -1), { ...last, content }];
  }
  return [
    ...messages,
    { role: "assistant", content, timestamp: new Date().toISOString(), kind: "status" },
  ];
}

function appendError(messages: ChatMessage[], content: string): ChatMessage[] {
  if (!content.trim()) return messages;
  return [
    ...messages,
    { role: "assistant", content, timestamp: new Date().toISOString(), kind: "error" },
  ];
}

export function applyEvent(state: ThreadDetail, env: AgentEventEnvelope): ThreadDetail {
  switch (env.type) {
    case "intent":
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, env.message),
      };
    case "searching":
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, env.message),
      };
    case "reviewing":
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, "已完成结果审核。"),
      };
    case "refining":
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, "已生成优化检索策略。"),
      };

    case "thinking": {
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, "正在分析检索需求与检索策略…"),
      };
    }

    case "chat": {
      return {
        ...state,
        status: "running",
        messages: appendStreaming(state.messages, "text", env.message, "Agent 回复中…"),
      };
    }

    case "report": {
      return {
        ...state,
        status: "running",
        messages: appendStreaming(state.messages, "report", env.message, "生成报告中…"),
        // 同时累积 report 字段，方便侧栏快速判断 has_report
        report: state.report + env.message,
      };
    }

    case "searching_done":
    case "papers": {
      const payload = (env.payload || {}) as { papers?: Paper[] };
      const papers = payload.papers ?? [];
      // searching_done 是某一轮中间结果，papers 是最终；都用最新替换展示
      return {
        ...state,
        status: "running",
        messages: appendStatus(state.messages, env.message),
        papers: papers.length ? papers : state.papers,
      };
    }

    case "done": {
      const payload = (env.payload || {}) as {
        kind?: string;
        papers?: Paper[];
        report?: string;
        evidence?: import("./api").EvidenceChunk[];
      };
      // 检索类完成：用最终 papers/report/evidence 覆盖
      let papers = state.papers;
      let report = state.report;
      let evidence = state.evidence;
      if (payload.kind === "search") {
        if (payload.papers && payload.papers.length) papers = payload.papers;
        if (typeof payload.report === "string" && payload.report) report = payload.report;
        if (Array.isArray(payload.evidence)) evidence = payload.evidence;
      }
      return {
        ...state,
        status: "done" as ThreadStatus,
        messages: appendStatus(state.messages, env.message || "✅ 完成"),
        papers,
        report,
        evidence,
        last_error: null,
      };
    }

    case "error": {
      return {
        ...state,
        status: "error" as ThreadStatus,
        messages: appendError(state.messages, env.message || "出错"),
        last_error: env.message || "出错",
      };
    }

    case "cancelled": {
      return {
        ...state,
        status: "cancelled" as ThreadStatus,
        messages: appendStatus(state.messages, env.message || "已停止当前检索。"),
      };
    }

    default:
      return state;
  }
}

// 把一条用户消息加入线程（发送前乐观追加）。
export function withUserMessage(state: ThreadDetail, query: string): ThreadDetail {
  return {
    ...state,
    status: "running",
    messages: [
      ...state.messages,
      { role: "user", content: query, timestamp: new Date().toISOString(), kind: "text" },
    ],
  };
}
