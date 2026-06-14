"""系统依赖探测端点。"""
import sys


def test_deps_all_present(client):
    """测试环境装齐了依赖，应返回 ok=True 且 missing 为空。"""
    r = client.get("/api/system/deps")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["missing_modules"] == []
    assert data["python_version"]
    assert "fastapi" in data["installed_versions"]
    assert data["uv_commands"]["setup"]


def test_deps_reports_missing(client, monkeypatch):
    """模拟缺失模块：让 import_module 对 qdrant_client 抛错。"""
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "qdrant_client":
            raise ImportError("No module named 'qdrant_client'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    r = client.get("/api/system/deps")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    missing_names = [m["module"] for m in data["missing_modules"]]
    assert "qdrant_client" in missing_names
    assert data["uv_commands"]["sync_only"] == "uv sync"
