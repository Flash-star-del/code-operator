from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when required provider configuration is invalid."""


DEFAULT_CONTEXT_WINDOW = 32_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_000
DEFAULT_MAX_MODEL_ROUNDS = 16
DEFAULT_MAX_TOOL_CALLS = 32


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_model_rounds: int = DEFAULT_MAX_MODEL_ROUNDS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"


def _required(environment: Mapping[str, str], name: str) -> str:
    raw_value = environment.get(name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ConfigError(f"缺少必要环境变量 {name}")
    return raw_value.strip()


def _normalize_base_url(raw_value: str) -> str:
    base_url = raw_value.rstrip("/")
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ConfigError("CODE_OPERATOR_BASE_URL 必须是有效的 API 根地址")
    if parsed.query or parsed.fragment:
        raise ConfigError("CODE_OPERATOR_BASE_URL API 根地址不能包含 query 或 fragment")
    if parsed.username or parsed.password:
        raise ConfigError("CODE_OPERATOR_BASE_URL API 根地址不能包含用户凭据")
    if parsed.path.rstrip("/").lower().endswith("/chat/completions"):
        raise ConfigError(
            "CODE_OPERATOR_BASE_URL 必须是 API 根地址，不能是完整 chat/completions 端点"
        )
    scheme = parsed.scheme.lower()
    is_local = parsed.hostname in {"localhost", "127.0.0.1"}
    if scheme not in {"http", "https"} or (scheme == "http" and not is_local):
        raise ConfigError("远程 CODE_OPERATOR_BASE_URL 必须使用 HTTPS")
    return base_url


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} 必须是正整数")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isdecimal():
            raise ConfigError(f"{name} 必须是正整数")
        parsed = int(stripped)
    else:
        raise ConfigError(f"{name} 必须是正整数")
    if parsed <= 0:
        raise ConfigError(f"{name} 必须是正整数")
    return parsed


def _limit_value(
    environment: Mapping[str, str],
    name: str,
    cli_value: object | None,
    default: int,
) -> int:
    if cli_value is not None:
        return _positive_int(cli_value, name)
    if name in environment:
        return _positive_int(environment[name], name)
    return default


def load_provider_config(
    environment: Mapping[str, str] | None = None,
    *,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    max_model_rounds: int | None = None,
    max_tool_calls: int | None = None,
) -> ProviderConfig:
    source = os.environ if environment is None else environment
    api_key = _required(source, "CODE_OPERATOR_API_KEY")
    base_url = _normalize_base_url(_required(source, "CODE_OPERATOR_BASE_URL"))
    model = _required(source, "CODE_OPERATOR_MODEL")
    resolved_context_window = _limit_value(
        source,
        "CODE_OPERATOR_CONTEXT_WINDOW",
        context_window,
        DEFAULT_CONTEXT_WINDOW,
    )
    resolved_max_output_tokens = _limit_value(
        source,
        "CODE_OPERATOR_MAX_OUTPUT_TOKENS",
        max_output_tokens,
        DEFAULT_MAX_OUTPUT_TOKENS,
    )
    resolved_max_model_rounds = _limit_value(
        source,
        "CODE_OPERATOR_MAX_MODEL_ROUNDS",
        max_model_rounds,
        DEFAULT_MAX_MODEL_ROUNDS,
    )
    resolved_max_tool_calls = _limit_value(
        source,
        "CODE_OPERATOR_MAX_TOOL_CALLS",
        max_tool_calls,
        DEFAULT_MAX_TOOL_CALLS,
    )
    if resolved_max_output_tokens >= resolved_context_window:
        raise ConfigError(
            "CODE_OPERATOR_MAX_OUTPUT_TOKENS 必须小于 CODE_OPERATOR_CONTEXT_WINDOW"
        )
    return ProviderConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        context_window=resolved_context_window,
        max_output_tokens=resolved_max_output_tokens,
        max_model_rounds=resolved_max_model_rounds,
        max_tool_calls=resolved_max_tool_calls,
    )
