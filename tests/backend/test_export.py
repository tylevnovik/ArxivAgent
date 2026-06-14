"""导出端点：空数据 vs 有数据。"""
import os

import config


def test_export_all_types_on_empty_thread(client):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    for typ in ("md", "csv", "json", "report"):
        r = client.post(f"/api/threads/{t['id']}/export", json={"type": typ})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "export_empty"


def test_export_chat_empty(client):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    r = client.post(f"/api/threads/{t['id']}/export", json={"type": "chat"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "export_empty"


def test_export_with_data(client, mock_full_search):
    # 先跑一次检索填充数据
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG", "api_key": "sk-test"},
    ):
        pass

    # 各类型导出应成功并返回文件名
    for typ in ("md", "csv", "json", "report"):
        r = client.post(f"/api/threads/{t['id']}/export", json={"type": typ})
        assert r.status_code == 200, f"{typ} 导出失败: {r.text}"
        data = r.json()
        assert data["ok"] is True
        assert data["filename"]
        # 文件应真实落盘
        path = os.path.join(config.EXPORT_DIR, data["filename"])
        assert os.path.exists(path), f"{typ} 文件未落盘: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 0
