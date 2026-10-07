"""后台任务生命周期回归测试。"""

import json
import queue
import threading

from app import _run_agent_worker
from core.agent import AgentEvent, EventType
from core.threads import thread_manager


class _EventAgent:
    def __init__(self, events):
        self._events = events

    def chat(self, _query):
        yield from self._events


def _run_worker(thread, events):
    handle = thread_manager.start_task(thread.id)
    out_queue = queue.Queue()
    worker = threading.Thread(
        target=_run_agent_worker,
        args=(thread, _EventAgent(events), "query", out_queue, handle),
    )
    handle.worker = worker
    thread.status = "running"
    thread.save()
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive(), "worker 未在测试超时内结束"
    return out_queue


def test_delete_running_thread_is_not_resurrected(isolated_data_dir):
    """删除先标记再取消；worker 收尾不得把 JSON 写回来。"""
    started = threading.Event()
    release = threading.Event()

    class SlowAgent:
        def chat(self, _query):
            started.set()
            release.wait(timeout=3)
            yield AgentEvent(EventType.DONE, content="done", data={"type": "chat"})

    thread = thread_manager.create()
    handle = thread_manager.start_task(thread.id)
    out_queue = queue.Queue()
    worker = threading.Thread(
        target=_run_agent_worker,
        args=(thread, SlowAgent(), "query", out_queue, handle),
    )
    handle.worker = worker
    thread.status = "running"
    thread.save()
    worker.start()

    assert started.wait(timeout=2)
    assert thread_manager.delete(thread.id) is True
    release.set()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert thread_manager.get(thread.id) is None
    assert not (thread_manager._tasks.get(thread.id))


def test_worker_persists_error_event_as_error(isolated_data_dir):
    thread = thread_manager.create()
    _run_worker(
        thread,
        [AgentEvent(EventType.ERROR, content="LLM 返回不是合法 JSON")],
    )

    persisted = thread_manager.get(thread.id)
    assert persisted is not None
    assert persisted.status == "error"
    assert persisted.last_error == "LLM 返回不是合法 JSON"


def test_worker_persists_cancelled_done_event_as_cancelled(isolated_data_dir):
    thread = thread_manager.create()
    _run_worker(
        thread,
        [AgentEvent(EventType.DONE, content="已停止", data={"cancelled": True})],
    )

    persisted = thread_manager.get(thread.id)
    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.last_error is None


# ===================== T2：任务启动快照一致性 =====================


def test_begin_task_registers_handle_with_latest_snapshot(isolated_data_dir):
    """原子启动入口：handle 登记与最新快照读取在同一锁内完成。

    旧代码在 start_task 之前读取 thread 并构造 agent，启动前落地的
    消息编辑不会进入 agent 上下文；begin_task 返回的快照必须包含
    登记时刻磁盘上的全部已确认内容。
    """
    thread = thread_manager.create("启动快照测试")
    thread.memory.conversation.append(
        {"role": "user", "content": "原始上下文", "timestamp": "2026-01-01T00:00:00", "id": None}
    )
    thread.save()

    stale = thread_manager.get(thread.id)  # 旧路径在 start_task 前拿到的快照

    def add_concurrent_edit(t):
        t.memory.conversation.append(
            {"role": "user", "content": "并发编辑", "timestamp": "2026-01-01T00:00:01", "id": None}
        )

    assert thread_manager.mutate_messages(thread.id, add_concurrent_edit) is not None

    handle, fresh = thread_manager.begin_task(thread.id)
    try:
        assert handle.thread_id == thread.id
        assert not handle.finished.is_set()
        assert thread_manager.get_task(thread.id) is handle
        contents = [m.get("content") for m in fresh.memory.conversation]
        assert "原始上下文" in contents
        assert "并发编辑" in contents, "begin_task 必须读取登记时刻的最新快照"
        assert fresh is not stale, "启动路径不得复用登记前的旧快照引用"
    finally:
        thread_manager.finish_task(thread.id, handle)


def test_begin_task_rejects_busy_and_missing(isolated_data_dir):
    """begin_task 保留 409/404 语义：忙抛 ThreadBusyError，缺失抛 ThreadDeletedError。"""
    import pytest

    from core.threads import ThreadBusyError, ThreadDeletedError

    thread = thread_manager.create()
    handle = thread_manager.start_task(thread.id)
    try:
        with pytest.raises(ThreadBusyError):
            thread_manager.begin_task(thread.id)
    finally:
        thread_manager.finish_task(thread.id, handle)

    with pytest.raises(ThreadDeletedError):
        thread_manager.begin_task("does-not-exist-0001")


# ===================== T3：启动失败必须清理 =====================


def _drain_ndjson(response):
    events = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        events.append(json.loads(line))
    return events


def _start_payload():
    return {"query": "RAG survey", "api_key": "sk-test"}


def test_start_failure_persist_error_releases_handle(
    client, mock_full_search, isolated_data_dir, monkeypatch
):
    """running 状态落盘抛 OSError：返回结构化 internal 错误、释放登记、
    尽力保存 error 状态；再次启动不得被旧 handle 卡 409。"""
    import app as appmod
    from core.threads import Thread as ThreadCls

    tid = client.post("/api/threads", json={"title": None}).json()["thread"]["id"]

    calls = {"v": 0}
    orig_save = ThreadCls.save

    def save_fails_once(self):
        if self.id == tid:
            calls["v"] += 1
            if calls["v"] == 1:
                raise OSError("注入的磁盘故障")
        return orig_save(self)

    monkeypatch.setattr(ThreadCls, "save", save_fails_once)

    r = client.post(f"/api/threads/{tid}/messages", json=_start_payload())
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal", "磁盘写入失败不得伪装成 404 或裸异常"
    assert thread_manager.get_task(tid) is None, "启动失败后任务登记未释放"

    saved = thread_manager.get(tid)
    assert saved is not None
    assert saved.status == "error"
    assert saved.last_error, "启动失败必须尽力保存明确 error 状态"

    # 再次启动：不因旧 handle 永久 409，正常完成
    with client.stream(
        "POST", f"/api/threads/{tid}/messages", json=_start_payload()
    ) as resp:
        assert resp.status_code == 200
        events = _drain_ndjson(resp)
    assert any(e["type"] == "done" for e in events)


def test_start_failure_agent_build_releases_handle_and_saves_error(
    client, mock_full_search, isolated_data_dir, monkeypatch
):
    """agent 构造失败：400 invalid_provider + 释放登记 + 尽力保存 error 状态。"""
    import app as appmod

    tid = client.post("/api/threads", json={"title": None}).json()["thread"]["id"]

    def build_boom(thread, msg, llm_config):
        raise ValueError("注入的 provider 构造失败")

    monkeypatch.setattr(appmod, "_build_agent_for_thread", build_boom)

    r = client.post(f"/api/threads/{tid}/messages", json=_start_payload())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_provider"
    assert thread_manager.get_task(tid) is None, "构造失败后任务登记未释放"

    saved = thread_manager.get(tid)
    assert saved is not None
    assert saved.status == "error"
    assert "构造失败" in (saved.last_error or "")


def test_start_failure_worker_start_releases_handle_and_allows_retry(
    client, mock_full_search, isolated_data_dir, monkeypatch
):
    """worker.start 抛 RuntimeError：结构化 internal 错误 + 释放登记；重试可正常完成。"""
    import threading as _threading

    import app as appmod

    tid = client.post("/api/threads", json={"title": None}).json()["thread"]["id"]

    raised = {"v": False}
    orig_start = _threading.Thread.start

    def start_that_fails(self, *args, **kwargs):
        if getattr(self, "_target", None) is appmod._run_agent_worker and not raised["v"]:
            raised["v"] = True
            raise RuntimeError("注入的 worker 启动失败")
        return orig_start(self, *args, **kwargs)

    monkeypatch.setattr(_threading.Thread, "start", start_that_fails)

    r = client.post(f"/api/threads/{tid}/messages", json=_start_payload())
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal"
    assert thread_manager.get_task(tid) is None, "worker 启动失败后任务登记未释放"

    saved = thread_manager.get(tid)
    assert saved is not None
    assert saved.status == "error"

    # 重试启动：旧 handle 已清理，不得 409
    with client.stream(
        "POST", f"/api/threads/{tid}/messages", json=_start_payload()
    ) as resp:
        assert resp.status_code == 200
        events = _drain_ndjson(resp)
    assert any(e["type"] == "done" for e in events)
