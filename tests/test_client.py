from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from code_operator.client import ModelClient, ProviderHTTPError, ProviderProtocolError
from code_operator.config import ProviderConfig
from code_operator.models import AssistantTurn


CONFIG = ProviderConfig(
    api_key="private-test-key",
    base_url="https://provider.example/v1",
    model="test-model",
    max_output_tokens=321,
)


def response_payload(
    *,
    content: str | None = "完成",
    tool_calls: list[dict[str, object]] | None = None,
    finish_reason: str = "stop",
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "response-id",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "unknown_choice_field": "ignored",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
        "unknown_top_level": "ignored",
    }


def tool_call(call_id: str, name: str, arguments: str) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def client_for_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float] | None = None,
) -> ModelClient:
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return ModelClient(
        CONFIG,
        http_client=http_client,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        jitter=lambda: 0.0,
    )


def test_parses_plain_text_and_internal_metadata() -> None:
    client = client_for_handler(
        lambda _: httpx.Response(200, json=response_payload())
    )

    turn = client.complete([{"role": "user", "content": "task"}], [])

    assert turn.content == "完成"
    assert turn.tool_calls == []
    assert turn.finish_reason == "stop"
    assert turn.request_id == "response-id"
    assert turn.usage is not None
    assert turn.usage.total_tokens == 12


def test_parses_single_and_multiple_tool_calls_in_order() -> None:
    calls = [
        tool_call("call-1", "read_file", '{"path":"a.py"}'),
        tool_call("call-2", "run_command", '{"argv":["python","-V"]}'),
    ]
    client = client_for_handler(
        lambda _: httpx.Response(
            200, json=response_payload(content=None, tool_calls=calls, finish_reason="tool_calls")
        )
    )

    turn = client.complete([{"role": "user", "content": "task"}], [])

    assert [(item.id, item.name, item.arguments_raw) for item in turn.tool_calls] == [
        ("call-1", "read_file", '{"path":"a.py"}'),
        ("call-2", "run_command", '{"argv":["python","-V"]}'),
    ]


def test_bad_arguments_are_preserved_as_raw_text_for_matched_tool_failure() -> None:
    calls = [tool_call("call-1", "read_file", "{")]
    client = client_for_handler(
        lambda _: httpx.Response(200, json=response_payload(content=None, tool_calls=calls))
    )

    turn = client.complete([], [])

    assert turn.tool_calls[0].arguments_raw == "{"


@pytest.mark.parametrize("choices", [[], [{"message": {"role": "assistant", "content": "a"}}, {"message": {"role": "assistant", "content": "b"}}]])
def test_empty_or_multiple_choices_are_protocol_errors(
    choices: list[dict[str, object]],
) -> None:
    payload = response_payload()
    payload["choices"] = choices
    client = client_for_handler(lambda _: httpx.Response(200, json=payload))

    with pytest.raises(ProviderProtocolError, match="choices"):
        client.complete([], [])


@pytest.mark.parametrize(
    "broken_call",
    [
        {"type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "call-1", "type": "other", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "call-1", "type": "function", "function": {"name": "", "arguments": "{}"}},
        {"id": "call-1", "type": "function", "function": {"arguments": "{}"}},
        {"id": "call-1", "type": "function", "function": {"name": "read_file"}},
        {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": {}}},
    ],
)
def test_missing_tool_envelope_fields_are_protocol_errors(
    broken_call: dict[str, object],
) -> None:
    client = client_for_handler(
        lambda _: httpx.Response(
            200, json=response_payload(content=None, tool_calls=[broken_call])
        )
    )

    with pytest.raises(ProviderProtocolError, match="tool"):
        client.complete([], [])


def test_duplicate_tool_call_ids_are_protocol_errors() -> None:
    calls = [
        tool_call("same", "read_file", "{}"),
        tool_call("same", "list_dir", "{}"),
    ]
    client = client_for_handler(
        lambda _: httpx.Response(200, json=response_payload(content=None, tool_calls=calls))
    )

    with pytest.raises(ProviderProtocolError, match="唯一"):
        client.complete([], [])


def test_only_p0_whitelisted_replay_fields_are_preserved() -> None:
    payload = response_payload(content="answer")
    choice = payload["choices"][0]  # type: ignore[index]
    message = choice["message"]  # type: ignore[index]
    message["reasoning_content"] = "not verified for replay"  # type: ignore[index]
    message["vendor_unknown"] = "ignore me"  # type: ignore[index]
    client = client_for_handler(lambda _: httpx.Response(200, json=payload))

    turn = client.complete([], [])

    assert turn.replay_fields == {}
    assert turn.to_replay_message() == {"role": "assistant", "content": "answer"}


def test_replay_field_conflicts_with_standard_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="冲突"):
        AssistantTurn(content="x", tool_calls=[], replay_fields={"content": "other"})


def test_replay_message_excludes_internal_and_unknown_response_fields() -> None:
    calls = [tool_call("call-1", "read_file", '{"path":"a.py"}')]
    client = client_for_handler(
        lambda _: httpx.Response(
            200, json=response_payload(content=None, tool_calls=calls, finish_reason="tool_calls")
        )
    )

    replay = client.complete([], []).to_replay_message()

    assert replay == {
        "role": "assistant",
        "content": None,
        "tool_calls": calls,
    }
    assert "finish_reason" not in replay
    assert "usage" not in replay
    assert "request_id" not in replay
    assert "unknown_top_level" not in replay


def test_request_uses_verified_contract_and_never_serializes_key_in_body() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=response_payload())

    client = client_for_handler(handler)
    tools = [{"type": "function", "function": {"name": "demo", "parameters": {}}}]

    client.complete([{"role": "user", "content": "task"}], tools)

    assert len(captured) == 1
    request = captured[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://provider.example/v1/chat/completions"
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "task"}],
        "tools": tools,
        "tool_choice": "auto",
        "thinking": {"type": "disabled"},
        "max_tokens": 321,
        "n": 1,
    }
    assert request.headers["Authorization"] == "Bearer private-test-key"
    assert b"private-test-key" not in request.content


@pytest.mark.parametrize("retry_status", [429, 500, 503])
def test_retries_429_and_server_errors_at_most_twice(retry_status: int) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(retry_status, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json=response_payload())

    client = client_for_handler(handler, sleeps=sleeps)

    assert client.complete([], []).content == "完成"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_401_is_not_retried_and_error_never_contains_key_or_response_body() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            401,
            json={"error": {"message": "private-test-key must never leak"}},
        )

    client = client_for_handler(handler)

    with pytest.raises(ProviderHTTPError) as captured:
        client.complete([], [])

    assert attempts == 1
    assert captured.value.status_code == 401
    assert "private-test-key" not in str(captured.value)


def test_connection_error_retries_twice_then_returns_transport_error() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    client = client_for_handler(handler, sleeps=sleeps)

    with pytest.raises(ProviderHTTPError) as captured:
        client.complete([], [])

    assert attempts == 3
    assert sleeps == [0.5, 1.0]
    assert captured.value.status_code is None


def test_protocol_error_after_successful_http_is_never_retried() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"choices": []})

    client = client_for_handler(handler)

    with pytest.raises(ProviderProtocolError):
        client.complete([], [])

    assert attempts == 1
