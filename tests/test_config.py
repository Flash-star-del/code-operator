from __future__ import annotations

import pytest

from code_operator.config import ConfigError, ProviderConfig, load_provider_config


REQUIRED = {
    "CODE_OPERATOR_API_KEY": "test-key-value",
    "CODE_OPERATOR_BASE_URL": "https://api.moonshot.cn/v1",
    "CODE_OPERATOR_MODEL": "test-model",
}


@pytest.mark.parametrize("missing_name", sorted(REQUIRED))
def test_required_provider_variable_must_be_present(missing_name: str) -> None:
    environment = dict(REQUIRED)
    environment.pop(missing_name)

    with pytest.raises(ConfigError, match=missing_name):
        load_provider_config(environment)


def test_provider_config_normalizes_root_url_and_builds_endpoint() -> None:
    environment = {
        "CODE_OPERATOR_API_KEY": "  test-key-value  ",
        "CODE_OPERATOR_BASE_URL": " https://api.moonshot.cn/v1/ ",
        "CODE_OPERATOR_MODEL": " test-model ",
    }

    config = load_provider_config(environment)

    assert config.api_key == "test-key-value"
    assert config.base_url == "https://api.moonshot.cn/v1"
    assert config.endpoint == "https://api.moonshot.cn/v1/chat/completions"
    assert config.model == "test-model"


def test_provider_config_rejects_full_chat_completions_endpoint() -> None:
    environment = dict(REQUIRED)
    environment["CODE_OPERATOR_BASE_URL"] = (
        "https://api.moonshot.cn/v1/chat/completions"
    )

    with pytest.raises(ConfigError, match="API 根地址"):
        load_provider_config(environment)


def test_provider_config_repr_never_contains_api_key() -> None:
    config = ProviderConfig(
        api_key="test-key-value",
        base_url="https://api.moonshot.cn/v1",
        model="test-model",
    )

    assert "test-key-value" not in repr(config)
