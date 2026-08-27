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


@pytest.mark.parametrize("blank_value", ["", " ", "\t\r\n"])
@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_required_provider_variable_rejects_blank_values(
    name: str, blank_value: str
) -> None:
    environment = dict(REQUIRED)
    environment[name] = blank_value

    with pytest.raises(ConfigError, match=name):
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


@pytest.mark.parametrize(
    ("base_url", "accepted"),
    [
        ("http://api.example.test/v1", False),
        ("http://localhost:8080/v1", True),
        ("http://127.0.0.1:8080/v1", True),
        ("https://api.example.test/v1", True),
    ],
)
def test_provider_config_only_allows_plain_http_for_localhost(
    base_url: str, accepted: bool
) -> None:
    environment = dict(REQUIRED)
    environment["CODE_OPERATOR_BASE_URL"] = base_url

    if accepted:
        assert load_provider_config(environment).base_url == base_url
    else:
        with pytest.raises(ConfigError, match="HTTPS"):
            load_provider_config(environment)


@pytest.mark.parametrize("invalid_value", [True, False, 0, -1, 1.5, "not-an-int"])
def test_cli_positive_integer_limits_reject_invalid_values(invalid_value: object) -> None:
    with pytest.raises(ConfigError, match="CODE_OPERATOR_MAX_MODEL_ROUNDS"):
        load_provider_config(REQUIRED, max_model_rounds=invalid_value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CODE_OPERATOR_CONTEXT_WINDOW", "0"),
        ("CODE_OPERATOR_CONTEXT_WINDOW", "-1"),
        ("CODE_OPERATOR_CONTEXT_WINDOW", "1.5"),
        ("CODE_OPERATOR_MAX_OUTPUT_TOKENS", "false"),
        ("CODE_OPERATOR_MAX_TOOL_CALLS", ""),
    ],
)
def test_environment_positive_integer_limits_reject_invalid_values(
    name: str, value: str
) -> None:
    environment = dict(REQUIRED)
    environment[name] = value

    with pytest.raises(ConfigError, match=name):
        load_provider_config(environment)


def test_output_limit_must_be_smaller_than_context_window() -> None:
    environment = {
        **REQUIRED,
        "CODE_OPERATOR_CONTEXT_WINDOW": "8000",
        "CODE_OPERATOR_MAX_OUTPUT_TOKENS": "8000",
    }

    with pytest.raises(ConfigError, match="小于"):
        load_provider_config(environment)


def test_cli_then_environment_then_default_limit_precedence() -> None:
    environment = {
        **REQUIRED,
        "CODE_OPERATOR_CONTEXT_WINDOW": "64000",
        "CODE_OPERATOR_MAX_OUTPUT_TOKENS": "4000",
        "CODE_OPERATOR_MAX_MODEL_ROUNDS": "12",
        "CODE_OPERATOR_MAX_TOOL_CALLS": "24",
    }

    from_environment = load_provider_config(environment)
    from_cli = load_provider_config(
        environment,
        context_window=48000,
        max_output_tokens=3000,
        max_model_rounds=7,
        max_tool_calls=13,
    )
    from_defaults = load_provider_config(REQUIRED)

    assert (
        from_environment.context_window,
        from_environment.max_output_tokens,
        from_environment.max_model_rounds,
        from_environment.max_tool_calls,
    ) == (64000, 4000, 12, 24)
    assert (
        from_cli.context_window,
        from_cli.max_output_tokens,
        from_cli.max_model_rounds,
        from_cli.max_tool_calls,
    ) == (48000, 3000, 7, 13)
    assert (
        from_defaults.context_window,
        from_defaults.max_output_tokens,
        from_defaults.max_model_rounds,
        from_defaults.max_tool_calls,
    ) == (32000, 8000, 16, 32)


def test_provider_config_repr_never_contains_api_key() -> None:
    config = ProviderConfig(
        api_key="test-key-value",
        base_url="https://api.moonshot.cn/v1",
        model="test-model",
    )

    assert "test-key-value" not in repr(config)


def test_provider_config_exception_never_contains_api_key() -> None:
    environment = {
        **REQUIRED,
        "CODE_OPERATOR_API_KEY": "a-private-provider-key",
        "CODE_OPERATOR_BASE_URL": "not-a-url",
    }

    with pytest.raises(ConfigError) as captured:
        load_provider_config(environment)

    assert "a-private-provider-key" not in str(captured.value)
    assert "a-private-provider-key" not in repr(captured.value)
