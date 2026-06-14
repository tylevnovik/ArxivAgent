"""线程消息编辑/删除端点。"""


def _run_mock_search(client, thread_id: str):
    with client.stream(
        "POST",
        f"/api/threads/{thread_id}/messages",
        json={"query": "RAG survey", "api_key": "sk-test"},
    ):
        pass


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
