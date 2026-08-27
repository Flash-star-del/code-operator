from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when required provider configuration is invalid."""


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str

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
    if parsed.scheme.lower() != "https" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        raise ConfigError("远程 CODE_OPERATOR_BASE_URL 必须使用 HTTPS")
    return base_url


def load_provider_config(
    environment: Mapping[str, str] | None = None,
) -> ProviderConfig:
    source = os.environ if environment is None else environment
    api_key = _required(source, "CODE_OPERATOR_API_KEY")
    base_url = _normalize_base_url(_required(source, "CODE_OPERATOR_BASE_URL"))
    model = _required(source, "CODE_OPERATOR_MODEL")
    return ProviderConfig(api_key=api_key, base_url=base_url, model=model)
