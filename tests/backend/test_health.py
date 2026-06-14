"""健康检查端点。"""
import config


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["version"] == config.APP_VERSION


def test_config_health_no_key(client, monkeypatch):
    # 确保进程无 env key
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    r = client.get("/api/config/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["api_key_configured"] is False
    assert data["api_key_source"] == "none"
    assert data["endpoint"]


def test_config_health_with_key(client, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    r = client.get("/api/config/health")
    assert r.status_code == 200
    data = r.json()
    assert data["api_key_configured"] is True
    assert data["api_key_source"] == "env"
