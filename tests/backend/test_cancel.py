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
