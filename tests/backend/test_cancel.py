"""
取消机制测试。

分两层：
1. agent 层：cancel_event 被 set 后，agent.chat() 应抛 CancelledError 并经
   _run_search 顶层 try 转成 cancelled 终态事件。
2. HTTP 层：cancel 端点本身可用（对空闲线程返回 200）；并发流式取消需真实服务器，
   这里用线程级驱动验证 worker→cancelled→持久化链路。
"""
import json
import threading
import time

import pytest

from core.agent import ArxivAgent, EventType
from core.llm import CancelledError


def test_agent_cancel_emits_cancelled_event(mock_search_providers, isolated_data_dir, monkeypatch):
    """
    直接驱动 agent：在 query_parse 的 LLM 流过程中 set cancel_event，
    断言 agent 抛 CancelledError 并产生 data.cancelled=True 的 DONE 事件。
    """
    import core.llm as llm

    cancel_event = threading.Event()

    def stream_with_cancel_check(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        # 模拟真实 llm.stream_chat：每个 token 检查 cancel
        text = '{"arxiv_query":"x","keywords":["x"],"strategy":"s","sort_by":"relevance","max_results":5}'
        for tok in text.split():
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("test cancel")
            time.sleep(0.01)
            yield tok + " "

    monkeypatch.setattr(llm, "stream_chat", stream_with_cancel_check)

    agent = ArxivAgent(api_key="sk-test", cancel_event=cancel_event)

    # 在另一个线程里 set cancel，同时主线程迭代 generator
    def set_cancel():
        time.sleep(0.02)
        cancel_event.set()

    canceler = threading.Thread(target=set_cancel)
    canceler.start()

    events = list(agent.chat("检索 RAG 论文"))
    canceler.join()

    # 应当出现 cancelled 终态（DONE with data.cancelled）
    cancelled_or_done = [e for e in events if e.event_type == EventType.DONE]
    assert cancelled_or_done, "未产生 DONE 事件"
    last = cancelled_or_done[-1]
    assert (last.data or {}).get("cancelled") is True, f"终态不是 cancelled: {last}"


def test_cancel_endpoint_on_idle_thread(client):
    """对没有运行任务的线程调 cancel，应返回 200 且 status 提示无任务。"""
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(f"/api/threads/{t['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ===================== T3：取消绑定任务实例 =====================


def test_request_cancel_with_expected_handle_only_cancels_matching_task(isolated_data_dir):
    """request_cancel 带 expected_handle 时，只取消登记的同实例任务。"""
    from core.threads import TaskHandle, thread_manager

    thread = thread_manager.create()
    h1 = thread_manager.start_task(thread.id)

    # 不匹配的 handle 实例：不得取消当前任务
    outsider = TaskHandle(thread.id)
    assert thread_manager.request_cancel(thread.id, expected_handle=outsider) is False
    assert not h1.cancel_event.is_set()

    # 匹配的 handle 实例：取消
    assert thread_manager.request_cancel(thread.id, expected_handle=h1) is True
    assert h1.cancel_event.is_set()
    thread_manager.finish_task(thread.id, h1)

    # 不带参数：保留"取消当前任务"语义（用户显式 cancel API）
    h2 = thread_manager.start_task(thread.id)
    try:
        assert thread_manager.request_cancel(thread.id) is True
        assert h2.cancel_event.is_set()
    finally:
        thread_manager.finish_task(thread.id, h2)

    # 全部结束后：无可取消任务
    assert thread_manager.request_cancel(thread.id) is False


def test_stale_stream_close_does_not_cancel_new_task(isolated_data_dir):
    """旧流的 finally 收尾不得取消同会话上新登记的任务。

    确定性构造：旧 handle 登记并自然结束 → 新 handle 登记 →
    手动消费旧流第一条事件后关闭（触发 finally 的取消请求），
    断言新任务的 cancel_event 未被置位。不依赖网络竞速。
    """
    import asyncio
    import queue

    import app as appmod
    from core.threads import thread_manager

    thread = thread_manager.create()

    # 旧任务：登记 → 流生成器捕获 handle → 任务自然结束
    old_handle = thread_manager.start_task(thread.id)
    old_queue = queue.Queue()
    stream = appmod._make_ndjson_stream(thread, old_handle, old_queue)
    thread_manager.finish_task(thread.id, old_handle)

    # 新任务登记（同一会话，新一轮检索）
    new_handle = thread_manager.start_task(thread.id)
    try:
        async def scenario():
            first = await stream.__anext__()  # 消费 intent，流挂起在 yield
            assert '"intent"' in first
            await stream.aclose()  # 客户端断开 → finally 执行取消请求

        asyncio.run(scenario())

        assert not new_handle.cancel_event.is_set(), (
            "旧流的 finally 把新任务取消了（取消未绑定任务实例）"
        )
    finally:
        thread_manager.finish_task(thread.id, new_handle)


def test_stream_wait_exits_when_worker_dies_without_sentinel(isolated_data_dir):
    """worker 已死且无哨兵时，带界限的队列等待必须自行退出而非永久挂起。

    旧实现 asyncio.to_thread(out_queue.get) 无界限：取消/断流后若
    生产者不再投递，协程将永久等待。新实现带 0.5s 界限并检查
    取消/结束状态。
    """
    import asyncio
    import queue

    import app as appmod
    from core.threads import thread_manager

    thread = thread_manager.create()
    handle = thread_manager.start_task(thread.id)
    try:
        empty_queue = queue.Queue()  # 永远不会有条目（模拟 worker 已死、哨兵丢失）
        stream = appmod._make_ndjson_stream(thread, handle, empty_queue)
        handle.cancel_event.set()  # 模拟任务已被取消，生产者不再投递

        async def scenario():
            await stream.__anext__()  # intent
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(stream.__anext__(), timeout=3)

        asyncio.run(scenario())
    finally:
        thread_manager.finish_task(thread.id, handle)
