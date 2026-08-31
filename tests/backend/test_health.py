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


def test_config_health_uses_selected_provider_environment(client, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-test-key")
    monkeypatch.setenv("OPENCODE_GO_API_BASE", "https://go.example/v1")
    r = client.post(
        "/api/config/health",
        json={
            "provider": "opencode-go",
            "model": "deepseek-v4-flash",
            "ping_llm": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["api_key_configured"] is True
    assert data["api_key_source"] == "env"
    assert data["endpoint"] == "https://go.example/v1"


def test_config_health_allows_keyless_local_provider(client, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    r = client.post(
        "/api/config/health",
        json={
            "provider": "ollama",
            "model": "llama3.2",
            "ping_llm": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["api_key_configured"] is False
    assert data["api_key_source"] == "none"
    assert data["endpoint"] == "http://localhost:11434/v1"


def test_config_health_reports_invalid_endpoint_then_recovers(client):
    bad = client.post(
        "/api/config/health",
        json={
            "provider": "custom",
            "base_url": "javascript:alert(1)",
            "model": "local-model",
            "providers": ["not-a-network-check"],
            "ping_llm": False,
        },
    )
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    assert "endpoint" in bad.json()["llm_detail"]

    good = client.post(
        "/api/config/health",
        json={
            "provider": "custom",
            "base_url": "http://localhost:9000/v1",
            "model": "local-model",
            "providers": ["not-a-network-check"],
            "ping_llm": False,
        },
    )
    assert good.status_code == 200
    assert good.json()["ok"] is True
    assert good.json()["endpoint"] == "http://localhost:9000/v1"
