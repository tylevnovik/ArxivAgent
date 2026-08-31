"""LLM provider catalog and runtime configuration resolution.

The desktop UI may offer friendly presets, but the backend is the source of
truth for credential names, endpoint defaults, and protocol support.  Keeping
this resolution in one place prevents the message path and the health-check
path from silently using different providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import config


class ProviderConfigError(ValueError):
    """Raised when a provider selection cannot produce a safe request config."""


@dataclass(frozen=True)
class ProviderSpec:
    """Non-secret provider capabilities and environment-variable bindings."""

    id: str
    label: str
    default_endpoint: str
    default_model: str
    api_key_env: Optional[str] = None
    api_base_env: tuple[str, ...] = ()
    model_env: tuple[str, ...] = ()
    requires_api_key: bool = True
    transport: str = "openai-compatible"
    supported: bool = True
    local_only: bool = False


@dataclass(frozen=True)
class ResolvedProviderConfig:
    """Concrete, request-scoped provider configuration without persistence."""

    provider: str
    model: str
    endpoint: str
    api_key: str
    api_key_source: str
    api_key_env: Optional[str]
    requires_api_key: bool
    transport: str


OPENCODE_GO_DEFAULT_ENDPOINT = "https://opencode.ai/zen/go/v1"
MIMO_DEFAULT_ENDPOINT = "https://token-plan-cn.xiaomimimo.com/v1"


# This is deliberately limited to transports ArxivAgent can actually execute
# today.  LocalAgent also knows native LiteLLM providers such as Gemini and
# Anthropic, but this project currently uses the OpenAI SDK directly.
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        default_endpoint="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        api_base_env=("DEEPSEEK_BASE_URL", "DEEPSEEK_API_BASE"),
        model_env=("DEEPSEEK_MODEL",),
    ),
    "zhipu": ProviderSpec(
        id="zhipu",
        label="智谱 GLM",
        default_endpoint="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4.6",
        api_key_env="ZHIPU_API_KEY",
        api_base_env=("ZHIPU_API_BASE", "ZHIPU_BASE_URL"),
        model_env=("ZHIPU_MODEL",),
    ),
    "moonshot": ProviderSpec(
        id="moonshot",
        label="Moonshot Kimi",
        default_endpoint="https://api.moonshot.cn/v1",
        default_model="kimi-k2.7-code",
        api_key_env="MOONSHOT_API_KEY",
        api_base_env=("MOONSHOT_API_BASE", "MOONSHOT_BASE_URL"),
        model_env=("MOONSHOT_MODEL",),
    ),
    "dashscope": ProviderSpec(
        id="dashscope",
        label="阿里通义千问（百炼）",
        default_endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        api_base_env=("DASHSCOPE_API_BASE", "DASHSCOPE_BASE_URL"),
        model_env=("DASHSCOPE_MODEL",),
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI / OpenAI-compatible",
        default_endpoint="https://api.openai.com/v1",
        default_model="gpt-5.5",
        api_key_env="OPENAI_API_KEY",
        api_base_env=("OPENAI_API_BASE", "OPENAI_BASE_URL"),
        model_env=("OPENAI_MODEL",),
    ),
    "gemini": ProviderSpec(
        id="gemini",
        label="Google Gemini",
        default_endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        api_base_env=("GEMINI_API_BASE", "GOOGLE_API_BASE"),
        model_env=("GEMINI_MODEL",),
        transport="litellm-native",
        supported=False,
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        default_endpoint="https://api.anthropic.com/v1",
        default_model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        api_base_env=("ANTHROPIC_BASE_URL",),
        model_env=("ANTHROPIC_MODEL",),
        transport="native-anthropic",
        supported=False,
    ),
    "mimo": ProviderSpec(
        id="mimo",
        label="小米 MiMo",
        default_endpoint=MIMO_DEFAULT_ENDPOINT,
        default_model="mimo-v2.5-pro",
        api_key_env="MIMO_API_KEY",
        api_base_env=("MIMO_API_BASE", "MIMO_BASE_URL"),
        model_env=("MIMO_MODEL",),
    ),
    "opencode-go": ProviderSpec(
        id="opencode-go",
        label="OpenCode Go",
        default_endpoint=OPENCODE_GO_DEFAULT_ENDPOINT,
        default_model="deepseek-v4-flash",
        api_key_env="OPENCODE_GO_API_KEY",
        api_base_env=("OPENCODE_GO_API_BASE",),
        model_env=("OPENCODE_GO_MODEL",),
    ),
    "ollama": ProviderSpec(
        id="ollama",
        label="Ollama（本地）",
        default_endpoint="http://localhost:11434/v1",
        default_model="llama3.2",
        api_key_env="OLLAMA_API_KEY",
        api_base_env=("OLLAMA_API_BASE",),
        model_env=("OLLAMA_MODEL",),
        requires_api_key=False,
        local_only=True,
    ),
    "vllm": ProviderSpec(
        id="vllm",
        label="vLLM（本地）",
        default_endpoint="http://localhost:8000/v1",
        default_model="",
        api_key_env="VLLM_API_KEY",
        api_base_env=("VLLM_API_BASE",),
        model_env=("VLLM_MODEL",),
        requires_api_key=False,
        local_only=True,
    ),
    "custom": ProviderSpec(
        id="custom",
        label="自定义 OpenAI-compatible",
        default_endpoint="",
        default_model="",
        api_key_env="CUSTOM_API_KEY",
        api_base_env=("CUSTOM_API_BASE",),
        model_env=("CUSTOM_MODEL",),
        requires_api_key=False,
    ),
}


_PROVIDER_ALIASES = {
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "openai_compat": "custom",
    "local-ollama": "ollama",
    "local-vllm": "vllm",
}


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _config_fallback(spec: ProviderSpec, field: str) -> str:
    """Preserve config.py's historical DeepSeek overrides for direct tests/use."""
    if spec.id != "deepseek":
        return ""
    if field == "api_key":
        return str(getattr(config, "DEEPSEEK_API_KEY", "") or "").strip()
    if field == "endpoint":
        return str(getattr(config, "DEEPSEEK_BASE_URL", "") or "").strip()
    if field == "model":
        return str(getattr(config, "DEEPSEEK_MODEL", "") or "").strip()
    return ""


def _validate_endpoint(value: str) -> str:
    """Validate a base URL without allowing embedded credentials or fragments."""
    endpoint = str(value or "").strip()
    if not endpoint:
        raise ProviderConfigError("API endpoint 不能为空")
    try:
        parsed = urlsplit(endpoint)
        # Accessing port validates malformed values such as ``:abc``.
        _ = parsed.port
    except ValueError as exc:
        raise ProviderConfigError(f"API endpoint 无效: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ProviderConfigError("API endpoint 只允许使用 http 或 https")
    if not parsed.hostname:
        raise ProviderConfigError("API endpoint 必须包含主机名")
    if parsed.username or parsed.password:
        raise ProviderConfigError("API endpoint 不允许包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ProviderConfigError("API endpoint 不允许包含 query 或 fragment")
    return endpoint.rstrip("/")


def normalize_provider(provider: Optional[str], model: Optional[str] = None) -> str:
    """Normalize an explicit provider, or infer one from a model prefix."""
    raw = str(provider or "").strip().lower()
    if raw:
        normalized = _PROVIDER_ALIASES.get(raw, raw)
        if normalized not in PROVIDER_SPECS:
            raise ProviderConfigError(f"不支持的 LLM provider: {raw}")
        return normalized

    model_name = str(model or "").strip().lower()
    prefix = model_name.split("/", 1)[0] if "/" in model_name else ""
    if prefix in PROVIDER_SPECS:
        return prefix
    if model_name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if "mimo" in model_name:
        return "mimo"
    return "deepseek"


def get_provider_spec(provider: Optional[str], model: Optional[str] = None) -> ProviderSpec:
    return PROVIDER_SPECS[normalize_provider(provider, model)]


def resolve_provider_config(
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> ResolvedProviderConfig:
    """Resolve one concrete request config used by both API paths.

    Explicit request values win over provider-specific environment variables;
    provider defaults are used last.  No secret is persisted or included in
    the returned representation except the in-memory request-scoped key.
    """
    spec = get_provider_spec(provider, model)
    if not spec.supported or spec.transport != "openai-compatible":
        raise ProviderConfigError(
            f"provider {spec.id} 当前需要 {spec.transport} transport，ArxivAgent 尚未接入"
        )

    request_key = str(api_key or "").strip()
    env_key = _first_env((spec.api_key_env,) if spec.api_key_env else ())
    resolved_key = request_key or env_key or _config_fallback(spec, "api_key")
    key_source = "request" if request_key else "env" if (env_key or _config_fallback(spec, "api_key")) else "none"

    env_endpoint = _first_env(spec.api_base_env)
    resolved_endpoint = str(base_url or "").strip() or env_endpoint or _config_fallback(spec, "endpoint") or spec.default_endpoint
    if not resolved_endpoint:
        raise ProviderConfigError(f"provider {spec.id} 必须配置 API endpoint")
    endpoint = _validate_endpoint(resolved_endpoint)

    env_model = _first_env(spec.model_env)
    resolved_model = str(model or "").strip() or env_model or _config_fallback(spec, "model") or spec.default_model
    if not resolved_model:
        raise ProviderConfigError(f"provider {spec.id} 必须配置模型名称")

    return ResolvedProviderConfig(
        provider=spec.id,
        model=resolved_model,
        endpoint=endpoint,
        api_key=resolved_key,
        api_key_source=key_source,
        api_key_env=spec.api_key_env,
        requires_api_key=spec.requires_api_key,
        transport=spec.transport,
    )


def provider_catalog() -> list[dict[str, object]]:
    """Return safe provider metadata for diagnostics or a future UI catalog."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "default_endpoint": spec.default_endpoint,
            "default_model": spec.default_model,
            "api_key_env": spec.api_key_env,
            "api_base_env": list(spec.api_base_env),
            "requires_api_key": spec.requires_api_key,
            "transport": spec.transport,
            "local_only": spec.local_only,
        }
        for spec in PROVIDER_SPECS.values()
    ]
