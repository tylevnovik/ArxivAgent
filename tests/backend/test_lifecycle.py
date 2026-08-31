"""后台任务生命周期回归测试。"""

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
