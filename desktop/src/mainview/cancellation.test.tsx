/**
 * T6 回归测试：让取消状态与后端真实状态一致。
 *
 * 后端语义：cancel API 受理 ≠ 任务已结束（worker 收尾需要时间，上游
 * 阻塞时状态可能长时间停留在 running）。因此：
 * - 详情连续返回 running 时不得声称"已停止"，提交保持禁用（phase=stopping）；
 * - cancel 请求失败必须可见（onCancelRequestError），且不得放弃确认；
 * - 带界限短轮询：超时进入 unconfirmed（"停止尚未确认"），允许重试；
 * - 切换会话（stillRelevant=false）静默结束轮询，不误报终态；
 * - 卸载后不再回调、不再更新状态。
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ThreadDetail } from "./api";
import { useStopConfirmation } from "./useStopConfirmation";

function detail(id: string, status: ThreadDetail["status"]): ThreadDetail {
  return {
    id,
    title: "会话",
    status,
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
    messages: [],
    papers: [],
    report: "",
    evidence: [],
    last_error: null,
  };
}

/** getThread 替身：按调用顺序弹出状态；序列耗尽后重复最后一个。 */
function scriptedGetThread(statuses: ThreadDetail["status"][]) {
  let call = 0;
  return vi.fn(async (_id: string) => {
    const status = statuses[Math.min(call, statuses.length - 1)];
    call += 1;
    return detail("t1", status);
  });
}

const fastPoll = { pollIntervalMs: 5, pollTimeoutMs: 60 };

describe("useStopConfirmation", () => {
  it("详情连续 running 时不声称已停止，后端确认 cancelled 后才恢复", async () => {
    const getThread = scriptedGetThread(["running", "running", "cancelled"]);
    const cancelThread = vi.fn(async () => undefined);
    const onConfirmed = vi.fn();
    const { result } = renderHook(() =>
      useStopConfirmation({ getThread, cancelThread, onConfirmed, ...fastPoll }),
    );

    await act(async () => {
      void result.current.confirmStop("t1", () => true);
    });
    expect(result.current.phaseRef.current).toBe("stopping");

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
    // 必须轮询后端并用后端返回的终态恢复，而不是本地 abort 即声称已停止
    expect(getThread).toHaveBeenCalled();
    expect(onConfirmed.mock.calls[0][0].status).toBe("cancelled");
    expect(result.current.phaseRef.current).toBe("idle");
  });

  it("cancel 请求失败必须可见，且继续向后端确认终态", async () => {
    const getThread = scriptedGetThread(["running", "cancelled"]);
    const cancelThread = vi.fn(async () => {
      throw new Error("停止请求失败");
    });
    const onCancelRequestError = vi.fn();
    const onConfirmed = vi.fn();
    const { result } = renderHook(() =>
      useStopConfirmation({ getThread, cancelThread, onCancelRequestError, onConfirmed, ...fastPoll }),
    );

    await act(async () => {
      void result.current.confirmStop("t1", () => true);
    });

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
    expect(onCancelRequestError).toHaveBeenCalledTimes(1);
    expect(onConfirmed.mock.calls[0][0].status).toBe("cancelled");
  });

  it("超时进入 unconfirmed（不声称已停止），重试后可确认", async () => {
    const runningForever = scriptedGetThread(["running"]);
    const onTimeout = vi.fn();
    const onConfirmed = vi.fn();
    const cancelThread = vi.fn(async () => undefined);
    // renderHook 的回调闭包在挂载时固化，用可变委托切换后续脚本
    let currentGetThread = runningForever;
    const { result } = renderHook(() =>
      useStopConfirmation({
        getThread: (id: string) => currentGetThread(id),
        cancelThread,
        onTimeout,
        onConfirmed,
        pollIntervalMs: 5,
        pollTimeoutMs: 40,
      }),
    );

    await act(async () => {
      void result.current.confirmStop("t1", () => true);
    });
    await waitFor(() => expect(onTimeout).toHaveBeenCalledTimes(1));
    expect(onConfirmed).not.toHaveBeenCalled();
    expect(result.current.phaseRef.current).toBe("unconfirmed");

    // 重试：后端此时已落终态
    const recovered = scriptedGetThread(["cancelled"]);
    currentGetThread = recovered;
    await act(async () => {
      void result.current.confirmStop("t1", () => true);
    });
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
    expect(recovered).toHaveBeenCalled();
    expect(result.current.phaseRef.current).toBe("idle");
  });

  it("切换会话（stillRelevant=false）：轮询静默结束，不误报终态", async () => {
    const getThread = scriptedGetThread(["running"]);
    const onConfirmed = vi.fn();
    const onTimeout = vi.fn();
    const onCancelRequestError = vi.fn();
    const { result } = renderHook(() =>
      useStopConfirmation({
        getThread,
        cancelThread: vi.fn(async () => undefined),
        onConfirmed,
        onTimeout,
        onCancelRequestError,
        pollIntervalMs: 5,
        pollTimeoutMs: 5000,
      }),
    );

    let relevant = true;
    await act(async () => {
      void result.current.confirmStop("t1", () => relevant);
    });
    // 等第一次轮询发生后再切换
    await waitFor(() => expect(getThread).toHaveBeenCalled());
    await act(async () => {
      relevant = false;
    });
    await new Promise((r) => setTimeout(r, 40));
    expect(onConfirmed).not.toHaveBeenCalled();
    expect(onTimeout).not.toHaveBeenCalled();

    // App 在切换时会调用 cancelPendingPoll 复位
    await act(async () => {
      result.current.cancelPendingPoll();
    });
    expect(result.current.phaseRef.current).toBe("idle");
  });

  it("卸载后：轮询结束，不再回调", async () => {
    const getThread = scriptedGetThread(["running"]);
    const onConfirmed = vi.fn();
    const onTimeout = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const { result, unmount } = renderHook(() =>
      useStopConfirmation({
        getThread,
        cancelThread: vi.fn(async () => undefined),
        onConfirmed,
        onTimeout,
        pollIntervalMs: 5,
        pollTimeoutMs: 5000,
      }),
    );

    await act(async () => {
      void result.current.confirmStop("t1", () => true);
    });
    unmount();
    await new Promise((r) => setTimeout(r, 40));
    expect(onConfirmed).not.toHaveBeenCalled();
    expect(onTimeout).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
  });
});
