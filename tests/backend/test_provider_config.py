"""LLM provider catalog/resolution contract tests."""

import pytest

from core.agent import ArxivAgent
from core.providers import (
    ProviderConfigError,
    resolve_provider_config,
)


def test_opencode_go_uses_provider_specific_environment(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-test-key")
    monkeypatch.setenv("OPENCODE_GO_API_BASE", "https://go.example/v1")
    monkeypatch.setenv("OPENCODE_GO_MODEL", "deepseek-v4-pro")

    resolved = resolve_provider_config(provider="opencode-go")

    assert resolved.provider == "opencode-go"
    assert resolved.api_key == "go-test-key"
    assert resolved.api_key_source == "env"
    assert resolved.endpoint == "https://go.example/v1"
    assert resolved.model == "deepseek-v4-pro"


def test_openai_compatible_request_values_override_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://env.example/v1")

    resolved = resolve_provider_config(
        provider="openai",
        api_key="request-key",
        base_url="https://request.example/v1/",
        model="custom-model",
    )

    assert resolved.api_key == "request-key"
    assert resolved.api_key_source == "request"
    assert resolved.endpoint == "https://request.example/v1"
    assert resolved.model == "custom-model"


def test_local_ollama_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    resolved = resolve_provider_config(provider="ollama", model="llama3.2")

    assert resolved.requires_api_key is False
    assert resolved.api_key == ""
    assert resolved.endpoint == "http://localhost:11434/v1"


def test_provider_is_inferred_from_model_prefix(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "mimo-key")

    resolved = resolve_provider_config(model="mimo/mimo-v2.5")

    assert resolved.provider == "mimo"
    assert resolved.api_key == "mimo-key"


def test_custom_endpoint_allows_anonymous_local_service():
    resolved = resolve_provider_config(
        provider="custom",
        base_url="http://localhost:9000/v1",
        model="local-model",
    )

    assert resolved.requires_api_key is False
    assert resolved.api_key == ""
    assert resolved.endpoint == "http://localhost:9000/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "javascript:alert(1)",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
        "https://example.com:bad/v1",
    ],
)
def test_endpoint_rejects_unsafe_or_malformed_values(base_url):
    with pytest.raises(ProviderConfigError):
        resolve_provider_config(
            provider="custom",
            base_url=base_url,
            model="custom-model",
        )


def test_native_provider_is_not_advertised_as_openai_compatible():
    with pytest.raises(ProviderConfigError, match="transport"):
        resolve_provider_config(
            provider="anthropic",
            api_key="anthropic-key",
            model="claude-sonnet-4-6",
        )


def test_agent_provider_switch_uses_new_provider_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://open.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "open-model")

    agent = ArxivAgent(
        provider="deepseek",
        api_key="deepseek-key",
        base_url="https://deep.example/v1",
        model="deep-model",
    )
    agent.update_config(provider="openai")

    assert agent.provider == "openai"
    assert agent.api_key == "openai-key"
    assert agent.base_url == "https://open.example/v1"
    assert agent.model == "open-model"
