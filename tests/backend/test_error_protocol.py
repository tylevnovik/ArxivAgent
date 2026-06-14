"""错误协议：统一 ErrorResponse 形状 + ErrorCode。"""


def test_message_without_api_key_returns_structured_error(client):
    # 先建线程
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(f"/api/threads/{t['id']}/messages", json={"query": "test"})
    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "no_api_key"
    assert data["error"]["recoverable"] is True
    assert "API Key" in data["error"]["message"]


def test_empty_query_rejected(client):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(
        f"/api/threads/{t['id']}/messages",
        json={"query": "   ", "api_key": "sk-test"},
    )
    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "validation"


def test_thread_not_found(client):
    r = client.get("/api/threads/does-not-exist")
    assert r.status_code == 404
    data = r.json()
    assert data["error"]["code"] == "not_found"
    assert data["error"]["recoverable"] is False


def test_export_empty_thread(client):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(
        f"/api/threads/{t['id']}/export",
        json={"type": "md"},
    )
    assert r.status_code == 400
    data = r.json()
    assert data["error"]["code"] == "export_empty"
    assert data["error"]["recoverable"] is True


def test_rename_empty_title_rejected(client):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.patch(f"/api/threads/{t['id']}", json={"title": "   "})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation"


def test_invalid_provider_returns_structured_error(client):
    """codex 要求：未知检索源应返回 invalid_provider 结构化错误，而非 500。"""
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(
        f"/api/threads/{t['id']}/messages",
        json={"query": "test", "api_key": "sk-test", "providers": ["bogus"]},
    )
    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_provider"
    assert data["error"]["recoverable"] is True
    # 错误信息应提示支持的源
    assert "arxiv" in data["error"]["message"]


def test_invalid_provider_among_valid_still_rejected(client):
    """只要含一个未知名，整批拒绝（不静默忽略）。"""
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(
        f"/api/threads/{t['id']}/messages",
        json={"query": "test", "api_key": "sk-test", "providers": ["arxiv", "nope"]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_provider"
