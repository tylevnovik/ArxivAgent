/**
 * 会话选择的归属管理（T1）。
 *
 * 抽自 App.tsx 的 selectThread / run 收尾刷新逻辑：集中管理
 * activeThreadId / activeThread 状态，使所有选择入口（初始加载、
 * 新建、切换、删除后自动选择）走同一处。
 */
import { useCallback, useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import type { ThreadDetail } from "./api";

export interface UseThreadSelectionOptions {
  getThread: (id: string) => Promise<ThreadDetail>;
  /** 详情加载失败时回调（用于 toast）。 */
  onLoadError?: (error: unknown) => void;
}

export interface ThreadSelection {
  /** 当前选中会话 ID。 */
  threadId: string | null;
  /** 当前会话详情。 */
  thread: ThreadDetail | null;
  /** 详情请求在途。 */
  loading: boolean;
  /** 选中意图序号引用：响应返回后据此核对归属。 */
  generationRef: RefObject<number>;
  /** 当前选中会话 ID 的同步镜像（供流事件回调等非渲染路径读取）。 */
  threadIdRef: RefObject<string | null>;
  selectThread: (id: string) => Promise<ThreadDetail | null>;
  /**
   * 拉取详情并在返回时核对归属（供 run 收尾刷新使用）。
   * 已过期则丢弃；返回落地后的详情或 null。
   */
  fetchDetailIfCurrent: (
    id: string,
    gen: number,
    stillValid?: () => boolean,
  ) => Promise<ThreadDetail | null>;
  /**
   * 直接更新当前详情。仅限已由调用方做好归属检查的路径使用
   * （如 run 事件应用、消息编辑），常规选择一律走 selectThread。
   */
  setThread: Dispatch<SetStateAction<ThreadDetail | null>>;
}

export function useThreadSelection(options: UseThreadSelectionOptions): ThreadSelection {
  const { onLoadError } = options;
  const [threadId, setThreadId] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const generationRef = useRef(0);
  const threadIdRef = useRef<string | null>(null);
  const aliveRef = useRef(true);
  const getThreadRef = useRef(options.getThread);
  const onLoadErrorRef = useRef(onLoadError);

  useEffect(() => {
    getThreadRef.current = options.getThread;
    onLoadErrorRef.current = onLoadError;
  });

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  /** 归属核对：存活、generation、threadId 三者都对上才允许落地。 */
  const applyIfCurrent = useCallback(
    (
      id: string,
      gen: number,
      detail: ThreadDetail,
      stillValid?: () => boolean,
    ): ThreadDetail | null => {
      if (!aliveRef.current) return null;
      if (gen !== generationRef.current) return null;
      if (threadIdRef.current !== id) return null;
      if (stillValid && !stillValid()) return null;
      setThread(detail);
      return detail;
    },
    [],
  );

  const selectThread = useCallback(async (id: string): Promise<ThreadDetail | null> => {
    // 选中意图立即生效：先递增 generation，使此前所有在途详情请求
    // （包括同会话的旧请求，A→B→A）失效；同时立即清空旧详情并标记
    // loading，加载期间 activeThread 为 null，无法向旧会话发送消息。
    const gen = ++generationRef.current;
    threadIdRef.current = id;
    setThreadId(id);
    setThread(null);
    setLoading(true);
    try {
      const detail = await getThreadRef.current(id);
      const applied = applyIfCurrent(id, gen, detail);
      if (applied) {
        setLoading(false);
      }
      return applied;
    } catch (err) {
      // 只有该选择仍是当前意图时才落地错误提示；过期请求的失败必须静默，
      // 避免旧会话的失败弹成当前会话的错误。
      if (aliveRef.current && gen === generationRef.current) {
        setLoading(false);
        onLoadErrorRef.current?.(err);
      }
      return null;
    }
  }, [applyIfCurrent]);

  const fetchDetailIfCurrent = useCallback(
    async (id: string, gen: number, stillValid?: () => boolean): Promise<ThreadDetail | null> => {
      try {
        const detail = await getThreadRef.current(id);
        return applyIfCurrent(id, gen, detail, stillValid);
      } catch (err) {
        console.warn("刷新线程详情失败", err);
        return null;
      }
    },
    [applyIfCurrent],
  );

  return {
    threadId,
    thread,
    loading,
    generationRef,
    threadIdRef,
    selectThread,
    fetchDetailIfCurrent,
    setThread,
  };
}
