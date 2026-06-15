"""线程 CRUD + 持久化。"""

from core.threads import thread_manager


def test_create_thread(client):
    r = client.post("/api/threads", json={"title": "我的检索"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    t = data["thread"]
    assert t["id"]
    assert t["title"] == "我的检索"
    assert t["status"] == "idle"
    assert t["created_at"]


def test_list_threads(client):
    a = client.post("/api/threads", json={"title": "A"}).json()["thread"]
    b = client.post("/api/threads", json={"title": "B"}).json()["thread"]
    r = client.get("/api/threads")
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["threads"]]
    assert a["id"] in ids and b["id"] in ids


def test_get_thread_detail(client):
    t = client.post("/api/threads", json={"title": "X"}).json()["thread"]
    r = client.get(f"/api/threads/{t['id']}")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == t["id"]
    assert d["messages"] == []
    assert d["papers"] == []
    assert d["report"] == ""


def test_rename_thread(client):
    t = client.post("/api/threads", json={"title": "old"}).json()["thread"]
    r = client.patch(f"/api/threads/{t['id']}", json={"title": "new name"})
    assert r.status_code == 200
    assert r.json()["title"] == "new name"
    # 再读一次确认持久化
    assert client.get(f"/api/threads/{t['id']}").json()["title"] == "new name"


def test_delete_thread(client):
    t = client.post("/api/threads", json={"title": "del"}).json()["thread"]
    r = client.delete(f"/api/threads/{t['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 删除后查不到
    assert client.get(f"/api/threads/{t['id']}").status_code == 404


def test_persistence_roundtrip(isolated_data_dir):
    # 直接测 ThreadManager，绕过 client，验证磁盘往返
    t = thread_manager.create(title="持久化测试")
    t.memory.add_conversation("user", "hello")
    t.memory.set_user_query("hello")
    t.save()

    # 重新加载
    from core.threads import ThreadManager
    fresh = ThreadManager()
    loaded = fresh.get(t.id)
    assert loaded is not None
    assert loaded.title == "持久化测试"
    assert len(loaded.memory.conversation) == 1
    assert loaded.memory.conversation[0]["content"] == "hello"

    # 清理
    thread_manager.delete(t.id)


def test_thread_path_traversal_rejected(client, isolated_data_dir):
    """非法 thread_id（含路径分隔符、点号、特殊字符）不能读到线程目录外的文件。"""
    import os
    bait = os.path.join(os.path.dirname(isolated_data_dir), "bait.json")
    with open(bait, "w", encoding="utf-8") as f:
        f.write('{"bait": true}')

    # 这些 thread_id 含路径分隔符或遍历序列，必须被拒绝（404），
    # 绝不能拼进文件路径读到/删到线程目录外的文件。
    dangerous_ids = [
        "..",                    # 向上遍历
        "../bait",               # 读上级目录诱饵
        "../../bait",
        "foo/bar",               # 含路径分隔符
        "foo\\bar",              # Windows 风格分隔符
        "id/../../../bait",
        "a b",                   # 含空格
        "<script>",              # 特殊字符
        "id%2e%2e%2fbait",       # URL 编码的 ../bait
    ]
    for bad in dangerous_ids:
        r = client.get(f"/api/threads/{bad}")
        assert r.status_code == 404, f"GET thread_id={bad!r} 应被拒绝，实际 {r.status_code}"
        r = client.delete(f"/api/threads/{bad}")
        assert r.status_code == 404, f"DELETE thread_id={bad!r} 应被拒绝，实际 {r.status_code}"

    # 诱饵文件仍在（没被读出也没被删）
    assert os.path.exists(bait), "诱饵文件被删除了 —— 路径穿越防御失败"
    os.remove(bait)


def test_thread_id_with_safe_chars_accepted(client):
    """合法字符（字母、数字、下划线、连字符）应正常工作。"""
    # 通过正常创建拿一个合法 id，再 GET 确认能读回
    created = client.post("/api/threads", json={"title": "safe"}).json()["thread"]
    tid = created["id"]
    # 正常 id 一定只含 [A-Za-z0-9-]
    assert all(c.isalnum() or c in "-_" for c in tid)
    assert client.get(f"/api/threads/{tid}").status_code == 200
