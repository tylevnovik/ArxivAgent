"""配置环境变量覆盖回归测试。"""

import importlib


def test_deepseek_endpoint_and_model_are_env_overridable(monkeypatch):
    import config

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "local-model")
    importlib.reload(config)
    try:
        assert config.DEEPSEEK_BASE_URL == "http://localhost:9000/v1"
        assert config.DEEPSEEK_MODEL == "local-model"
    finally:
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
        importlib.reload(config)
