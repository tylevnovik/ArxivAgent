"""线程消息编辑/删除端点。"""

import threading
import time

import pytest

from core.threads import ThreadBusyError, thread_manager


def _run_mock_search(client, thread_id: str):
    with client.stream(
        "POST",
        f"/api/threads/{thread_id}/messages",
        json={"query": "RAG survey", "api_key": "sk-test"},
    ):
        pass


def _seed_thread(client, title="原始标题"):
    t = client.post("/api/threads", json={"title": title}).json()["thread"]
    _run_mock_search(client, t["id"])
    return t["id"]


def test_patch_thread_message_persists(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    _run_mock_search(client, t["id"])

    before = client.get(f"/api/threads/{t['id']}").json()
    assert before["messages"][0]["persisted_index"] == 0

    r = client.patch(
        f"/api/threads/{t['id']}/messages/0",
        json={"content": "updated query"},
    )
    assert r.status_code == 200
    assert r.json()["messages"][0]["content"] == "updated query"

    after = client.get(f"/api/threads/{t['id']}").json()
    assert after["messages"][0]["content"] == "updated query"


def test_delete_thread_message_persists(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    _run_mock_search(client, t["id"])

    before = client.get(f"/api/threads/{t['id']}").json()
    assert len(before["messages"]) >= 2

    r = client.delete(f"/api/threads/{t['id']}/messages/0")
    assert r.status_code == 200
    data = r.json()
    assert all(m["content"] != "RAG survey" for m in data["messages"])

    after = client.get(f"/api/threads/{t['id']}").json()
    assert all(m["content"] != "RAG survey" for m in after["messages"])


def test_patch_thread_message_rejects_empty_content(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    _run_mock_search(client, t["id"])

    r = client.patch(
        f"/api/threads/{t['id']}/messages/0",
        json={"content": "   "},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation"


# ===================== T2：一致性边界回归 =====================


def test_patch_message_under_concurrent_rename_keeps_confirmed_title(
    client, isolated_data_dir, monkeypatch
):
    """消息 PATCH 读-改-写窗口内落地的 rename 不得被旧快照回滚。

    注入方式：monkeypatch thread_manager.get，在路由首次读取快照时
    同步完成一次 rename（此刻双方都未持锁，是两个版本共同的合法窗口）。
    旧代码此后 persist 旧快照会把已确认的标题恢复成旧值；新代码的
    mutate_messages 在锁内读取最新快照，rename 与编辑都应存活。
    """
    tid = _seed_thread(client)

    true_get = thread_manager.get
    fired = {"v": False}
    rename_done = {"v": False}

    def get_with_rename(tid_, *args, **kwargs):
        t0 = true_get(tid_)
        if tid_ == tid and t0 is not None and not fired["v"]:
            fired["v"] = True
            renamed = thread_manager.rename(tid, "并发重命名")
            rename_done["v"] = renamed is not None
        return t0

    monkeypatch.setattr(thread_manager, "get", get_with_rename)

    r = client.patch(f"/api/threads/{tid}/messages/0", json={"content": "编辑后的提问"})
    assert r.status_code == 200
    final = client.get(f"/api/threads/{tid}").json()
    assert final["messages"][0]["content"] == "编辑后的提问"
    if rename_done["v"]:
        assert final["title"] == "并发重命名", (
            f"已确认的 rename 被消息 PATCH 的旧快照回滚: {final['title']}"
        )


def test_message_mutation_excludes_rename(isolated_data_dir):
    """mutate_messages 持锁期间 rename 必须等待；两者都基于最新快照按序生效。"""
    t = thread_manager.create("原始标题")
    t.memory.conversation.append(
        {"role": "user", "content": "原始消息", "timestamp": "2026-01-01T00:00:00", "id": None}
    )
    t.save()

    entered = threading.Event()
    release = threading.Event()

    def mutator(thread):
        entered.set()
        assert release.wait(timeout=3), "测试未释放 mutator（超时）"
        thread.memory.conversation[0]["content"] = "编辑后的消息"

    result = {}

    def run_mutate():
        result["r"] = thread_manager.mutate_messages(t.id, mutator)

    w = threading.Thread(target=run_mutate)
    w.start()
    assert entered.wait(timeout=2), "mutator 未进入临界区"

    rw = threading.Thread(target=lambda: result.update(rename=thread_manager.rename(t.id, "并发重命名")))
    rw.start()
    # 这里的 sleep 只用于给 rename 抢锁的机会以检测互斥破坏（负向断言，
    # 不决定通过与否；互斥成立时无论 sleep 多久 rename 都保持阻塞）。
    time.sleep(0.05)
    assert rw.is_alive(), "rename 在消息变更持锁期间穿插完成，一致性边界失效"

    release.set()
    w.join(timeout=3)
    rw.join(timeout=3)

    assert result["r"] is not None
    assert result.get("rename") is not None
    final = thread_manager.get(t.id)
    assert final.title == "并发重命名"
    assert final.memory.conversation[0]["content"] == "编辑后的消息"


def test_message_mutation_rejected_while_task_running(isolated_data_dir):
    """任务运行期间 mutate_messages 必须明确 409（ThreadBusyError），不得改动快照。"""
    t = thread_manager.create()
    t.memory.conversation.append(
        {"role": "user", "content": "运行中的消息", "timestamp": "2026-01-01T00:00:00", "id": None}
    )
    t.save()

    handle = thread_manager.start_task(t.id)
    try:
        with pytest.raises(ThreadBusyError):
            thread_manager.mutate_messages(t.id, lambda th: None)
    finally:
        thread_manager.finish_task(t.id, handle)

    final = thread_manager.get(t.id)
    assert final.memory.conversation[0]["content"] == "运行中的消息"


def test_message_delete_excludes_start_task(isolated_data_dir):
    """DELETE 消息持锁期间 start_task 必须等待；不得出现旧快照覆盖 running。"""
    t = thread_manager.create()
    t.memory.conversation.extend(
        [
            {"role": "user", "content": "要删除的消息", "timestamp": "2026-01-01T00:00:00", "id": None},
            {"role": "assistant", "content": "回复", "timestamp": "2026-01-01T00:00:01", "id": None},
        ]
    )
    t.save()

    entered = threading.Event()
    release = threading.Event()

    def mutator(thread):
        entered.set()
        assert release.wait(timeout=3), "测试未释放 mutator（超时）"
        del thread.memory.conversation[0]

    result = {}
    w = threading.Thread(target=lambda: result.update(r=thread_manager.mutate_messages(t.id, mutator)))
    w.start()
    assert entered.wait(timeout=2), "mutator 未进入临界区"

    sw = threading.Thread(target=lambda: result.update(handle=thread_manager.start_task(t.id)))
    sw.start()
    # 同上：负向断言的检测窗口，不决定通过与否。
    time.sleep(0.05)
    assert sw.is_alive(), "start_task 在消息删除持锁期间完成登记，一致性边界失效"

    release.set()
    w.join(timeout=3)
    sw.join(timeout=3)

    assert result["r"] is not None
    assert result.get("handle") is not None
    try:
        final = thread_manager.get(t.id)
        assert [m.get("content") for m in final.memory.conversation] == ["回复"]
    finally:
        thread_manager.finish_task(t.id, result["handle"])


def test_delete_message_route_does_not_overwrite_running_state(
    client, isolated_data_dir, monkeypatch
):
    """消息 DELETE 读-改-写窗口内启动的任务，running 状态不得被旧快照覆盖。

    注入方式：包装 app._message_mutation_busy_error，在其放行后立刻
    start_task + 持久化 running（旧代码的 busy 检查发生在读取快照之后、
    写回之前，正是覆盖窗口）。旧代码随后 persist 旧快照把状态恢复成
    idle；新代码没有这个读-改-写窗口，busy 检查与变更在同一锁内。
    """
    import app as appmod

    tid = _seed_thread(client, title=None)

    started = {"v": False}
    true_busy = getattr(appmod, "_message_mutation_busy_error", None)

    def busy_with_task_start(thread_id):
        resp = true_busy(thread_id) if true_busy is not None else None
        if resp is None and not started["v"]:
            started["v"] = True
            handle = thread_manager.start_task(tid)
            fresh = thread_manager.get(tid)
            fresh.status = "running"
            thread_manager.persist(fresh, handle)
        return resp

    monkeypatch.setattr(appmod, "_message_mutation_busy_error", busy_with_task_start, raising=False)

    r = client.delete(f"/api/threads/{tid}/messages/0")
    assert r.status_code == 200
    final = client.get(f"/api/threads/{tid}").json()
    if started["v"]:
        assert final["status"] == "running", (
            f"running 状态被消息 DELETE 的旧快照覆盖: {final['status']}"
        )
    else:
        # 新代码：注入点不存在（busy 检查在锁内），正常完成删除且无任务遗留
        assert all(m["content"] != "RAG survey" for m in final["messages"])
        assert thread_manager.get_task(tid) is None
