/**
 * NDJSON 事件流读取（T5）。
 *
 * 从 api.ts 的 streamThreadMessage 抽出，独立可测：
 * - 流结束时 flush 解码器并处理末行（旧实现 `if (done) break` 会丢尾行）；
 * - JSON 解析的 try/catch 只包解析：坏行抛出带行号与短摘要的协议错误，
 *   完整原始行不写 console；onEvent 的业务错误必须原样上抛；
 * - 校验最小信封形状（对象 + 非空字符串 type）；
 * - 跟踪终态：EOF 前没有 done/error/cancelled → 明确的"流提前中断"
 *   错误；看到 done 后继续读完（后端可能追加持久化失败 error）；
 * - 正常 EOF 不合成虚假事件；失败时取消底层读取并释放锁。
 */
import type { AgentEventEnvelope } from "./api";

const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set(["done", "error", "cancelled"]);

const MAX_ERROR_SNIPPET = 80;

export function isTerminalEvent(type: string): boolean {
  return TERMINAL_EVENT_TYPES.has(type);
}

function parseEnvelope(line: string, lineNumber: number): AgentEventEnvelope {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    const snippet = line.length > MAX_ERROR_SNIPPET ? line.slice(0, MAX_ERROR_SNIPPET) + "…" : line;
    throw new Error(`NDJSON 协议错误：第 ${lineNumber} 行不是合法 JSON（${snippet}）`);
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    typeof (parsed as { type?: unknown }).type !== "string" ||
    ((parsed as { type: string }).type).length === 0
  ) {
    const snippet = line.length > MAX_ERROR_SNIPPET ? line.slice(0, MAX_ERROR_SNIPPET) + "…" : line;
    throw new Error(`NDJSON 协议错误：第 ${lineNumber} 行缺少事件信封 type 字段（${snippet}）`);
  }
  return parsed as AgentEventEnvelope;
}

export async function readNdjsonStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (env: AgentEventEnvelope) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let lineNumber = 0;
  let sawTerminal = false;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 兼容 \n 与 \r\n：split 后 trim 去掉行尾 \r
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) continue;
        lineNumber += 1;
        const event = parseEnvelope(line, lineNumber);
        sawTerminal = sawTerminal || isTerminalEvent(event.type);
        // 业务回调错误不在解析 try/catch 内：必须原样上抛
        onEvent(event);
      }
    }
    // 流正常结束：flush 解码器残留字节，并处理末行（无换行符的完整行）
    buffer += decoder.decode();
    const tail = buffer.trim();
    if (tail) {
      lineNumber += 1;
      const event = parseEnvelope(tail, lineNumber);
      sawTerminal = sawTerminal || isTerminalEvent(event.type);
      onEvent(event);
    }
    if (!sawTerminal) {
      throw new Error("事件流提前中断：连接在收到 done/error/cancelled 终态前结束");
    }
  } catch (err) {
    // 失败/取消：主动取消底层读取，避免连接继续拉取数据
    try {
      await reader.cancel(err);
    } catch {
      // 流已关闭：忽略
    }
    throw err;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 锁已释放：忽略
    }
  }
}
