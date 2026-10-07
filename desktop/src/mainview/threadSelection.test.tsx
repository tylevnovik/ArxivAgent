/**
 * T1 回归测试：阻止旧请求覆盖当前会话。
 *
 * 用可控 promise（deferred）构造确定性乱序，不依赖随机时序：
 * - 点击 B、再点击 C，C 先返回、B 后返回 → 仍必须显示/发送 C；
 * - 旧 run 收尾刷新开始后用户切换会话 → 旧刷新返回不得落地；
 * - A→B→A：B 的在途请求必须失效，也不能被"同会话提前 return"拦住；
 * - 旧请求失败不得弹当前会话错误；
 * - 详情加载期间旧详情被清空，无法向旧会话发送消息；
 * - 组件卸载后返回的响应不得再更新状态。
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, ThreadDetail } from "./api";
import { useThreadSelection } from "./useThreadSelection";

function thread(id: string, title = id, contents: string[] = []): ThreadDetail {
  const messages: ChatMessage[] = contents.map((content, i) => ({
    id: `${id}-m${i}`,
    role: i % 2 === 0 ? "user" : "assistant",
    content,
    timestamp: "2026-01-01T00:00:00",
    kind: "text",
  }));
  return {
    id,
    title,
    status: "idle",
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
    messages,
    papers: [],
    report: "",
    evidence: [],
    last_error: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** getThread 测试替身：按 id 记录可控行为（resolved 立即返回，deferred 等手动放行）。 */
function fakeApi(handlers: Record<string, () => Promise<ThreadDetail>>) {
  return Object.assign(vi.fn((id: string) => handlers[id]()), { calls: [] as string[] });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useThreadSelection", () => {
  it("乱序返回：B 后于 C 完成时不得覆盖当前会话 C", async () => {
    const b = deferred<ThreadDetail>();
    const c = deferred<ThreadDetail>();
    const getThread = fakeApi({
      b: () => b.promise,
      c: () => c.promise,
    });
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("b");
    });
    await act(async () => {
      void result.current.selectThread("c");
    });

    // C 先完成
    await act(async () => {
      c.resolve(thread("c", "会话C", ["C 的消息"]));
    });
    expect(result.current.threadId).toBe("c");
    expect(result.current.thread?.title).toBe("会话C");

    // B 晚到：必须被丢弃
    await act(async () => {
      b.resolve(thread("b", "会话B", ["B 的旧消息"]));
    });
    expect(result.current.threadId).toBe("c");
    expect(result.current.thread?.title).toBe("会话C");
    expect(result.current.thread?.id).toBe("c");
    expect(result.current.thread?.messages.map((m) => m.content)).toEqual(["C 的消息"]);
  });

  it("A→B→A：B 的在途请求失效，且重选 A 不被拦住", async () => {
    const a1 = deferred<ThreadDetail>();
    const b = deferred<ThreadDetail>();
    const a2 = deferred<ThreadDetail>();
    const aCalls: Array<typeof a1 | typeof a2> = [];
    const getThread = vi.fn((id: string) => {
      if (id === "a") {
        const p = aCalls.length === 0 ? a1 : a2;
        aCalls.push(p);
        return p.promise;
      }
      return b.promise;
    });
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("a");
    });
    await act(async () => {
      a1.resolve(thread("a", "会话A", ["A-1"]));
    });
    expect(result.current.thread?.title).toBe("会话A");

    await act(async () => {
      void result.current.selectThread("b");
    });
    // 重选 A：即使 A 是"之前的会话"，也必须发出新请求并使 B 失效
    await act(async () => {
      void result.current.selectThread("a");
    });
    await act(async () => {
      a2.resolve(thread("a", "会话A", ["A-2"]));
    });
    expect(result.current.threadId).toBe("a");
    expect(result.current.thread?.messages.map((m) => m.content)).toEqual(["A-2"]);

    // B 晚到：不得覆盖 A
    await act(async () => {
      b.resolve(thread("b", "会话B", ["B 的消息"]));
    });
    expect(result.current.threadId).toBe("a");
    expect(result.current.thread?.id).toBe("a");
    expect(result.current.thread?.messages.map((m) => m.content)).toEqual(["A-2"]);
  });

  it("旧 run 收尾刷新：切换会话后旧刷新返回不得落地", async () => {
    const b = deferred<ThreadDetail>();
    const c = deferred<ThreadDetail>();
    const refresh = deferred<ThreadDetail>();
    let refreshCall = 0;
    const getThread = vi.fn((id: string) => {
      if (id === "b") return refreshCall++ === 0 ? b.promise : refresh.promise;
      return c.promise;
    });
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("b");
    });
    await act(async () => {
      b.resolve(thread("b", "会话B", ["B-1"]));
    });
    const gen = result.current.generationRef.current;

    // 模拟 run 收尾刷新：开始时 B 仍是当前会话
    let refreshPromise: Promise<ThreadDetail | null> = Promise.resolve(null);
    await act(async () => {
      refreshPromise = result.current.fetchDetailIfCurrent("b", gen);
    });
    // 用户切换到 C
    await act(async () => {
      void result.current.selectThread("c");
      c.resolve(thread("c", "会话C", ["C-1"]));
    });
    expect(result.current.threadId).toBe("c");

    // 旧刷新返回：不得覆盖 C
    await act(async () => {
      refresh.resolve(thread("b", "会话B", ["B-旧收尾快照"]));
      await refreshPromise;
    });
    expect(result.current.threadId).toBe("c");
    expect(result.current.thread?.id).toBe("c");
    expect(result.current.thread?.messages.map((m) => m.content)).toEqual(["C-1"]);
  });

  it("旧 run 收尾刷新：新 run 启动后（stillValid=false）不得落地", async () => {
    const b = deferred<ThreadDetail>();
    const refresh = deferred<ThreadDetail>();
    let firstB = true;
    const getThread = vi.fn((id: string) => {
      if (id === "b") {
        const p = firstB ? b.promise : refresh.promise;
        firstB = false;
        return p;
      }
      return Promise.resolve(thread(id));
    });
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("b");
      b.resolve(thread("b", "会话B", ["B-1"]));
    });
    const gen = result.current.generationRef.current;

    let refreshPromise: Promise<ThreadDetail | null> = Promise.resolve(null);
    await act(async () => {
      // 新 run 已在同一会话启动：乐观消息已追加，旧快照落地会清掉它
      refreshPromise = result.current.fetchDetailIfCurrent("b", gen, () => false);
      refresh.resolve(thread("b", "会话B", ["B-收尾快照"]));
      await refreshPromise;
    });
    expect(result.current.thread?.messages.map((m) => m.content)).toEqual(["B-1"]);
  });

  it("加载期间清空旧详情并进入 loading，无法向旧会话发送", async () => {
    const b = deferred<ThreadDetail>();
    const c = deferred<ThreadDetail>();
    const getThread = fakeApi({
      b: () => b.promise,
      c: () => c.promise,
    });
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("c");
      c.resolve(thread("c", "会话C", ["C-1"]));
    });
    expect(result.current.thread?.id).toBe("c");

    await act(async () => {
      void result.current.selectThread("b");
    });
    // B 在途：旧详情立即清空 → handleSendQuery 的 !activeThread 检查会拦住发送
    expect(result.current.loading).toBe(true);
    expect(result.current.thread).toBeNull();
    expect(result.current.threadId).toBe("b");

    await act(async () => {
      b.resolve(thread("b", "会话B", ["B-1"]));
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.thread?.id).toBe("b");
  });

  it("旧请求失败不弹当前会话错误；当前请求失败才回调 onLoadError", async () => {
    const b = deferred<ThreadDetail>();
    const c1 = deferred<ThreadDetail>();
    const c2 = deferred<ThreadDetail>();
    const cCalls: Array<typeof c1 | typeof c2> = [];
    const getThread = vi.fn((id: string) => {
      if (id === "b") return b.promise;
      const p = cCalls.length === 0 ? c1 : c2;
      cCalls.push(p);
      return p.promise;
    });
    const onLoadError = vi.fn();
    const { result } = renderHook(() => useThreadSelection({ getThread, onLoadError }));

    await act(async () => {
      void result.current.selectThread("b");
    });
    await act(async () => {
      void result.current.selectThread("c");
      c1.resolve(thread("c", "会话C"));
    });
    // B 失败时用户已在 C：不得弹 C 的错误
    await act(async () => {
      b.reject(new Error("b 加载失败"));
    });
    expect(onLoadError).not.toHaveBeenCalled();
    expect(result.current.thread?.id).toBe("c");

    // 当前会话 C 的请求失败：回调一次
    await act(async () => {
      void result.current.selectThread("c");
      c2.reject(new Error("c 加载失败"));
    });
    expect(onLoadError).toHaveBeenCalledTimes(1);
  });

  it("组件卸载后返回的响应不再更新状态", async () => {
    const b = deferred<ThreadDetail>();
    const getThread = fakeApi({ b: () => b.promise });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const { result, unmount } = renderHook(() => useThreadSelection({ getThread }));

    await act(async () => {
      void result.current.selectThread("b");
    });
    unmount();
    await act(async () => {
      b.resolve(thread("b", "会话B"));
    });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("正常顺序：成功落地详情、loading 复位、返回详情", async () => {
    const getThread = vi.fn((id: string) => Promise.resolve(thread(id, `会话${id}`, ["hi"])));
    const { result } = renderHook(() => useThreadSelection({ getThread }));

    let applied: ThreadDetail | null = null;
    await act(async () => {
      applied = (await result.current.selectThread("b")) as ThreadDetail | null;
    });
    expect((applied as ThreadDetail | null)?.id).toBe("b");
    expect(result.current.threadId).toBe("b");
    expect(result.current.thread?.title).toBe("会话b");
    expect(result.current.loading).toBe(false);
  });
});
