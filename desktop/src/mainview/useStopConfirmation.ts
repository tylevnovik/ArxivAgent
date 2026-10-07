/**
 * 停止确认（T6）：让取消状态与后端真实状态一致。
 *
 * 后端语义：cancel API 受理 ≠ 任务已结束——worker 收尾需要时间，上游
 * 阻塞时详情可能长时间停留在 running。本地 abort 不能等同于任务已结束：
 * - 确认期间 phase=stopping：UI 保持"正在停止"，提交禁用；
 * - 带界限短轮询（默认 500ms 起步、最多 30s），每轮核对会话归属；
 * - 后端返回非 running 终态 → onConfirmed 恢复 UI；
 * - 超时 → unconfirmed（"停止尚未确认"），允许再次点击停止重试；
 * - cancel 请求失败必须回调可见，且不放弃确认；
 * - 切换会话/卸载 → 轮询静默结束（UI 复位由调用方负责）。
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { ThreadDetail } from "./api";

export type StopPhase = "idle" | "stopping" | "unconfirmed";

export interface UseStopConfirmationOptions {
  getThread: (id: string) => Promise<ThreadDetail>;
  cancelThread: (id: string) => Promise<unknown>;
  /** cancel API 失败时回调（必须可见，不得静默吞掉）。 */
  onCancelRequestError?: (error: unknown) => void;
  /** 后端确认终态（status !== "running"）后回调。 */
  onConfirmed?: (detail: ThreadDetail) => void;
  /** 超时仍未确认时回调：显示"停止尚未确认"，允许重试。 */
  onTimeout?: (threadId: string) => void;
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  delay?: (ms: number) => Promise<void>;
}

export interface StopConfirmation {
  /** 当前停止确认阶段（渲染路径）。 */
  phase: StopPhase;
  /** 当前停止确认阶段的同步镜像（非渲染路径读取）。 */
  phaseRef: RefObject<StopPhase>;
  /**
   * 发起停止确认：请求后端取消 + 带界限短轮询终态。
   * stillRelevant() 返回 false（用户切走/重选）时轮询静默结束，
   * 不触发 onConfirmed/onTimeout——UI 复位由调用方负责。
   */
  confirmStop: (threadId: string, stillRelevant: () => boolean) => Promise<void>;
  /** 结束未完成的轮询并复位 phase（切换会话/删除/新建时调用）。 */
  cancelPendingPoll: () => void;
}

const DEFAULT_POLL_INTERVAL_MS = 500;
const DEFAULT_POLL_TIMEOUT_MS = 30000;

export function useStopConfirmation(options: UseStopConfirmationOptions): StopConfirmation {
  const {
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
    pollTimeoutMs = DEFAULT_POLL_TIMEOUT_MS,
  } = options;
  const [phase, setPhase] = useState<StopPhase>("idle");
  const phaseRef = useRef<StopPhase>("idle");
  const tokenRef = useRef(0);
  const aliveRef = useRef(true);
  const getThreadRef = useRef(options.getThread);
  const cancelThreadRef = useRef(options.cancelThread);
  const callbacksRef = useRef(options);

  useEffect(() => {
    callbacksRef.current = options;
    getThreadRef.current = options.getThread;
    cancelThreadRef.current = options.cancelThread;
  });

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      tokenRef.current += 1;
    };
  }, []);

  const setPhaseBoth = useCallback((next: StopPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const confirmStop = useCallback(
    async (threadId: string, stillRelevant: () => boolean) => {
      const token = ++tokenRef.current;
      setPhaseBoth("stopping");
      const delay =
        options.delay ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));

      try {
        await cancelThreadRef.current(threadId);
      } catch (err) {
        if (!aliveRef.current || token !== tokenRef.current) return;
        // 停止请求失败必须可见；不静默，也不放弃——继续轮询后端真实终态
        callbacksRef.current.onCancelRequestError?.(err);
      }

      const deadline = Date.now() + pollTimeoutMs;
      while (Date.now() < deadline) {
        await delay(pollIntervalMs);
        if (!aliveRef.current || token !== tokenRef.current) return;
        if (!stillRelevant()) return;
        let detail: ThreadDetail;
        try {
          detail = await getThreadRef.current(threadId);
        } catch {
          continue; // 单次详情拉取失败：继续重试直到超时
        }
        if (!aliveRef.current || token !== tokenRef.current) return;
        if (!stillRelevant()) return;
        if (detail.status !== "running") {
          setPhaseBoth("idle");
          callbacksRef.current.onConfirmed?.(detail);
          return;
        }
      }

      if (!aliveRef.current || token !== tokenRef.current) return;
      if (!stillRelevant()) return;
      setPhaseBoth("unconfirmed");
      callbacksRef.current.onTimeout?.(threadId);
    },
    [options.delay, pollIntervalMs, pollTimeoutMs, setPhaseBoth],
  );

  const cancelPendingPoll = useCallback(() => {
    tokenRef.current += 1;
    setPhaseBoth("idle");
  }, [setPhaseBoth]);

  return { phase, phaseRef, confirmStop, cancelPendingPoll };
}
