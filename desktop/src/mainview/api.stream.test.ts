/**
 * T5 回归测试：健壮读取 NDJSON 并保留错误。
 *
 * 用 ReadableStream 构造确定性字节流，覆盖：
 * - 中文 UTF-8 跨块、多个事件同块、CRLF、末行无换行（尾行不丢）；
 * - 坏 JSON 给明确协议错误（不静默跳过、不把原始行倒进 console）；
 * - 业务回调（onEvent）抛错必须原样上抛，不得被记成"NDJSON 解析失败"；
 * - EOF 前没有 done/error/cancelled 终态 → 明确的"流提前中断"错误；
 * - done 之后后端仍可能追加事件（如持久化失败 error），必须继续读完；
 * - AbortError 原样上抛；正常 EOF 不合成虚假 done。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { streamThreadMessage } from "./api";

const encoder = new TextEncoder();

function line(event: Record<string, unknown>, newline = true): string {
  return JSON.stringify(event) + (newline ? "\n" : "");
}

function env(type: string, message = "", payload?: Record<string, unknown>) {
  return { type, message, payload, timestamp: "2026-01-01T00:00:00" };
}

function responseFromChunks(chunks: Uint8Array[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

/** 把字符串按字节切成 n 段，可切开多字节 UTF-8 字符。 */
function splitBytes(text: string, parts: number): Uint8Array[] {
  const bytes = encoder.encode(text);
  const size = Math.ceil(bytes.length / parts);
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i += size) {
    chunks.push(bytes.slice(i, i + size));
  }
  return chunks;
}

function stubFetch(response: Response): void {
  vi.stubGlobal("fetch", vi.fn(async () => response));
}

async function collect(
  chunksSpec: () => Response,
  onEvent?: (env: unknown) => void,
): Promise<unknown[]> {
  stubFetch(chunksSpec());
  const events: unknown[] = [];
  await streamThreadMessage("t1", { query: "q" }, (e) => {
    events.push(e);
    onEvent?.(e);
  });
  return events;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("streamThreadMessage NDJSON 读取", () => {
  it("中文 UTF-8 跨块：多字节字符被切开也能正确解码", async () => {
    const text = line(env("done", "中文消息：检索 augmented 语义"));
    const events = await collect(() => responseFromChunks(splitBytes(text, 7)));
    expect(events).toHaveLength(1);
    expect((events[0] as { message: string }).message).toBe("中文消息：检索 augmented 语义");
  });

  it("多个事件同块：每条完整事件恰好分发一次", async () => {
    const onEvent = vi.fn();
    const events = await collect(
      () =>
        responseFromChunks([
          encoder.encode(line(env("intent", "a")) + line(env("thinking", "b")) + line(env("done", "c"))),
        ]),
      onEvent,
    );
    expect(events.map((e) => (e as { type: string }).type)).toEqual(["intent", "thinking", "done"]);
    expect(onEvent).toHaveBeenCalledTimes(3);
  });

  it("CRLF 行尾：事件正常分发", async () => {
    const events = await collect(() =>
      responseFromChunks([
        encoder.encode(JSON.stringify(env("intent", "a")) + "\r\n" + JSON.stringify(env("done", "b")) + "\r\n"),
      ]),
    );
    expect(events.map((e) => (e as { type: string }).type)).toEqual(["intent", "done"]);
  });

  it("末行无换行：尾行不丢失", async () => {
    const events = await collect(() =>
      responseFromChunks([encoder.encode(line(env("intent", "a")) + JSON.stringify(env("done", "tail")))]),
    );
    expect(events).toHaveLength(2);
    expect((events[1] as { message: string }).message).toBe("tail");
  });

  it("尾行跨块且无换行：不丢失", async () => {
    const payload = line(env("done", "跨块尾行"));
    const events = await collect(() =>
      responseFromChunks([
        encoder.encode(line(env("intent", "a"))),
        ...splitBytes(payload, 4),
      ]),
    );
    expect(events).toHaveLength(2);
    expect((events[1] as { message: string }).message).toBe("跨块尾行");
  });

  it("坏 JSON：抛出明确协议错误，不把完整原始行写进 console", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const badLine = '{"type": "done", "message": "' + "很长很长的坏行".repeat(50) + '"'; // 缺右括号
    stubFetch(
      responseFromChunks([encoder.encode(line(env("intent", "a")) + badLine + "\n")]),
    );
    const events: unknown[] = [];
    await expect(
      streamThreadMessage("t1", { query: "q" }, (e) => events.push(e)),
    ).rejects.toThrow(/NDJSON|协议|解析/i);
    // 坏行之前的事件已分发
    expect(events).toHaveLength(1);
    // 不得把完整原始行倒进 console
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining("很长很长的坏行"), expect.anything());
  });

  it("回调抛错：错误原样上抛，不被吞掉或误标为解析失败", async () => {
    stubFetch(responseFromChunks([encoder.encode(line(env("done", "ok")))]));
    const callbackError = new Error("业务回调内部错误");
    await expect(
      streamThreadMessage("t1", { query: "q" }, () => {
        throw callbackError;
      }),
    ).rejects.toThrow(callbackError.message);
  });

  it("空流：抛出流提前中断错误", async () => {
    stubFetch(responseFromChunks([]));
    await expect(
      streamThreadMessage("t1", { query: "q" }, () => {}),
    ).rejects.toThrow(/提前中断|终态/i);
  });

  it("只有中间事件就 EOF：抛出流提前中断错误", async () => {
    stubFetch(responseFromChunks([encoder.encode(line(env("thinking", "中间")))]));
    await expect(
      streamThreadMessage("t1", { query: "q" }, () => {}),
    ).rejects.toThrow(/提前中断|终态/i);
  });

  it("done 之后追加的 error 事件仍要分发（持久化失败告知）", async () => {
    const events = await collect(() =>
      responseFromChunks([
        encoder.encode(line(env("done", "完成")) + line(env("error", "⚠️ 检索已完成，但结果未能保存到磁盘"))),
      ]),
    );
    expect(events.map((e) => (e as { type: string }).type)).toEqual(["done", "error"]);
  });

  it("done 正常结尾：不抛错、不合成虚假事件", async () => {
    const onEvent = vi.fn();
    const events = await collect(() => responseFromChunks([encoder.encode(line(env("done", "ok")))]), onEvent);
    expect(events).toHaveLength(1);
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("AbortError：原样上抛", async () => {
    const controller = new AbortController();
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        controller.signal.addEventListener("abort", () => {
          const err = new Error("The operation was aborted");
          err.name = "AbortError";
          c.error(err);
        });
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(stream, { status: 200 })),
    );
    const pending = streamThreadMessage("t1", { query: "q" }, () => {}, controller.signal);
    controller.abort(); // 触发读取中止
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  }, 10000);
});
