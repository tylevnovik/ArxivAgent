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
