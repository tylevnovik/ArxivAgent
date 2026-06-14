/**
 * 后端契约层的前端镜像 + fetch 辅助。
 *
 * 这一层是前后端的唯一接触面：App.tsx 不直接 fetch /api/search，
 * 也不再用 emoji markdown 反解析。所有数据形状都和后端 core/contracts.py 对齐。
 */

export const API_BASE_URL = "http://127.0.0.1:7860";

// ===================== 论文 =====================

export type Paper = {
  title: string;
  authors: string[];
  abstract: string;
  categories: string[];
  published: string;
  updated: string;
  link: string;
  pdf_link: string;
  arxiv_id: string;
  source: string;
  source_id: string;
  doi: string;
  citation_count: number;
  score: number;
  reason: string;
};

// ===================== 线程 =====================

export type ThreadStatus = "idle" | "running" | "done" | "error" | "cancelled";

export type ThreadMeta = {
  id: string;
  title: string;
  status: ThreadStatus;
  created_at: string;
  updated_at: string;
  papers_count: number;
  has_report: boolean;
  message_count: number;
  last_error: string | null;
};

export type ChatMessage = {
  id?: string | null;
  persisted_index?: number | null;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  kind: "text" | "thinking" | "report" | "status" | "error";
};

/** 报告引用证据：一个命中的 RAG 正文切片。 */
export type EvidenceChunk = {
  paper_title: string;
  arxiv_id: string;
  chunk_index: string;
  text: string;
  retrieval_sources: string[];
  dense_score: number;
  bm25_score: number;
  hybrid_score: number;
  rerank_score: number;
  score: number;
};

export type ThreadDetail = {
  id: string;
  title: string;
  status: ThreadStatus;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  papers: Paper[];
  report: string;
  evidence: EvidenceChunk[];
  last_error: string | null;
};

// ===================== 事件 =====================

export type EventType =
  | "intent"
  | "thinking"
  | "searching"
  | "searching_done"
  | "reviewing"
  | "refining"
  | "report"
  | "chat"
  | "papers"
  | "done"
  | "error"
  | "cancelled";

export type AgentEventEnvelope = {
  type: EventType;
  message: string;
  payload?: Record<string, unknown> | null;
  timestamp: string;
  round?: number | null;
};

// ===================== 错误 =====================

export type ErrorCode =
  | "no_api_key"
  | "invalid_provider"
  | "llm_error"
  | "search_error"
  | "export_empty"
  | "not_found"
  | "rate_limited"
  | "cancelled"
  | "thread_busy"
  | "validation"
  | "internal";

export type ErrorResponse = {
  ok: false;
  error: { code: ErrorCode; message: string; recoverable: boolean };
};

// ===================== 配置健康 =====================

export type ProviderHealth = { name: string; ok: boolean; detail: string };

export type ConfigHealth = {
  ok: boolean;
  api_key_configured: boolean;
  api_key_source: "request" | "env" | "none";
  provider: string;
  endpoint: string;
  model: string;
  data_dir: string;
  llm_reachable: boolean | null;
  llm_detail: string;
  providers: ProviderHealth[];
  encryption_available: boolean | null;
};

// ===================== 请求/运行期配置 =====================

export type AppConfig = {
  provider: string;
  endpoint: string;
  apiKey: string;
  modelName: string;
  maxSearchRounds: number;
  maxResultsPerRound: number;
  providers: string[];
  openalexMailto: string;
  crossrefMailto: string;
  semanticScholarApiKey: string;
};

export type MessageRequest = {
  query: string;
  api_key?: string;
  base_url?: string;
  model?: string;
  provider?: string;
  max_search_rounds?: number;
  max_results_per_round?: number;
  providers?: string[];
  openalex_mailto?: string;
  crossref_mailto?: string;
  semantic_scholar_api_key?: string;
};

// ===================== fetch 辅助 =====================

export async function jsonOrError<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`后端返回非 JSON: ${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    const err = data as ErrorResponse | { detail?: string } | null;
    if (err && typeof err === "object" && "error" in err && err.error) {
      const e = err as ErrorResponse;
      const ex = new Error(e.error.message) as Error & { code?: ErrorCode; recoverable?: boolean };
      ex.code = e.error.code;
      ex.recoverable = e.error.recoverable;
      throw ex;
    }
    // FastAPI 校验错误等
    if (err && typeof err === "object" && "detail" in err && err.detail) {
      throw new Error(`请求校验失败: ${JSON.stringify((err as { detail: unknown }).detail)}`);
    }
    throw new Error(`HTTP ${res.status}`);
  }
  return data as T;
}

export async function listThreads(): Promise<ThreadMeta[]> {
  const res = await fetch(`${API_BASE_URL}/api/threads`);
  const data = await jsonOrError<{ threads: ThreadMeta[] }>(res);
  return data.threads;
}

export async function createThread(title?: string): Promise<ThreadMeta> {
  const res = await fetch(`${API_BASE_URL}/api/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  });
  const data = await jsonOrError<{ thread: ThreadMeta }>(res);
  return data.thread;
}

export async function getThread(id: string): Promise<ThreadDetail> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}`);
  return jsonOrError<ThreadDetail>(res);
}

export async function renameThread(id: string, title: string): Promise<ThreadMeta> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return jsonOrError<ThreadMeta>(res);
}

export async function updateThreadMessage(
  threadId: string,
  messageIndex: number,
  content: string,
): Promise<ThreadDetail> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${threadId}/messages/${messageIndex}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return jsonOrError<ThreadDetail>(res);
}

export async function deleteThreadMessage(
  threadId: string,
  messageIndex: number,
): Promise<ThreadDetail> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${threadId}/messages/${messageIndex}`, {
    method: "DELETE",
  });
  return jsonOrError<ThreadDetail>(res);
}

export async function deleteThread(id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}`, { method: "DELETE" });
  await jsonOrError(res);
}

export async function cancelThread(id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}/cancel`, { method: "POST" });
  await jsonOrError(res);
}

export async function getThreadPapers(id: string): Promise<Paper[]> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}/papers`);
  const data = await jsonOrError<{ papers: Paper[] }>(res);
  return data.papers;
}

export async function getThreadReport(id: string): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${id}/report`);
  const data = await jsonOrError<{ report: string }>(res);
  return data.report;
}

/**
 * 发起一次检索/对话，流式读取 NDJSON 事件信封。
 * onEvent 在每条事件上调用；返回一个 AbortController 供 UI 停止读取。
 * 注意：这里只控制 HTTP 读取的 abort；真正的后端取消要走 cancelThread()。
 */
export async function streamThreadMessage(
  threadId: string,
  body: MessageRequest,
  onEvent: (env: AgentEventEnvelope) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    // 非流式错误（如 no_api_key）直接抛
    const data = await res.json().catch(() => null);
    if (data && data.error) {
      const e = data as ErrorResponse;
      const ex = new Error(e.error.message) as Error & {
        code?: ErrorCode;
        recoverable?: boolean;
      };
      ex.code = e.error.code;
      ex.recoverable = e.error.recoverable;
      throw ex;
    }
    throw new Error(`HTTP ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("无法读取返回流");
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const env = JSON.parse(trimmed) as AgentEventEnvelope;
        onEvent(env);
      } catch (err) {
        console.warn("Failed to parse NDJSON line", err, trimmed);
      }
    }
  }
}

// ===================== 导出 =====================

export type ExportType = "chat" | "md" | "csv" | "json" | "report";

export async function exportThread(
  threadId: string,
  type: ExportType,
): Promise<{ filename: string; status: string }> {
  const res = await fetch(`${API_BASE_URL}/api/threads/${threadId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type }),
  });
  return jsonOrError<{ filename: string; status: string }>(res);
}

export async function downloadExport(filename: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/api/download?file=${encodeURIComponent(filename)}`);
  return res.blob();
}

// ===================== 配置健康 =====================

export async function getConfigHealth(
  body: Partial<MessageRequest> & { ping_llm?: boolean },
): Promise<ConfigHealth> {
  const res = await fetch(`${API_BASE_URL}/api/config/health`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrError<ConfigHealth>(res);
}
