from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from code_operator.config import ProviderConfig
from code_operator.models import AssistantTurn, ToolCall, Usage


ASSISTANT_REPLAY_FIELD_WHITELIST: frozenset[str] = frozenset()
MAX_RETRIES = 2
MAX_RETRY_WAIT_SECONDS = 5.0


class ProviderError(RuntimeError):
    error_code = "PROVIDER_ERROR"


class ProviderProtocolError(ProviderError):
    error_code = "PROVIDER_PROTOCOL_ERROR"


class ProviderHTTPError(ProviderError):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _protocol_error(message: str) -> ProviderProtocolError:
    return ProviderProtocolError(f"供应商响应协议错误：{message}")


def _optional_token_count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def parse_assistant_turn(payload: object) -> AssistantTurn:
    if not isinstance(payload, Mapping):
        raise _protocol_error("响应必须是 JSON 对象")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise _protocol_error("choices 必须恰好包含一项")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise _protocol_error("choice 必须是对象")
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise _protocol_error("assistant message 缺失或无效")

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise _protocol_error("assistant content 必须是字符串或 null")

    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise _protocol_error("tool_calls 必须是数组")
    tool_calls: list[ToolCall] = []
    ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            raise _protocol_error("tool call 必须是对象")
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id.strip():
            raise _protocol_error("tool_call_id 缺失或为空")
        if call_id in ids:
            raise _protocol_error("tool_call_id 在同一轮中必须唯一")
        if raw_call.get("type") != "function":
            raise _protocol_error("tool call type 必须是 function")
        if not isinstance(function, Mapping):
            raise _protocol_error("tool function 缺失或无效")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name.strip():
            raise _protocol_error("tool function name 缺失或为空")
        if not isinstance(arguments, str):
            raise _protocol_error("tool function arguments 缺失或不是字符串")
        ids.add(call_id)
        tool_calls.append(
            ToolCall(id=call_id, name=name, arguments_raw=arguments)
        )

    replay_fields = {
        key: message[key]
        for key in ASSISTANT_REPLAY_FIELD_WHITELIST
        if key in message
    }
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise _protocol_error("finish_reason 必须是字符串或 null")

    usage: Usage | None = None
    raw_usage = payload.get("usage")
    if isinstance(raw_usage, Mapping):
        usage = Usage(
            prompt_tokens=_optional_token_count(raw_usage.get("prompt_tokens")),
            completion_tokens=_optional_token_count(
                raw_usage.get("completion_tokens")
            ),
            total_tokens=_optional_token_count(raw_usage.get("total_tokens")),
        )
    request_id = payload.get("id")
    if not isinstance(request_id, str):
        request_id = None
    return AssistantTurn(
        content=content,
        tool_calls=tool_calls,
        replay_fields=replay_fields,
        finish_reason=finish_reason,
        usage=usage,
        request_id=request_id,
    )


class ModelClient:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], object] = time.sleep,
        jitter: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self._config = config
        self._http_client = http_client or httpx.Client()
        self._owns_http_client = http_client is None
        self._sleep = sleep
        self._jitter = jitter
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=30.0,
            pool=10.0,
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> ModelClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def complete(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> AssistantTurn:
        request_payload: dict[str, object] = {
            "model": self._config.model,
            "messages": list(messages),
            "thinking": {"type": "disabled"},
            "max_tokens": self._config.max_output_tokens,
            "n": 1,
        }
        if tools:
            request_payload["tools"] = list(tools)
            request_payload["tool_choice"] = "auto"
        response = self._post_with_retries(request_payload)
        try:
            payload: Any = response.json()
        except ValueError as error:
            raise _protocol_error("响应正文不是合法 JSON") from error
        return parse_assistant_turn(payload)

    def _post_with_retries(self, payload: Mapping[str, object]) -> httpx.Response:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._http_client.post(
                    self._config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self._timeout,
                )
            except (httpx.TransportError, httpx.TimeoutException) as error:
                if attempt >= MAX_RETRIES:
                    raise ProviderHTTPError(None, "供应商连接或读取失败") from error
                self._wait_before_retry(attempt, None)
                continue

            if 200 <= response.status_code < 300:
                return response
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < MAX_RETRIES:
                self._wait_before_retry(attempt, response.headers.get("Retry-After"))
                continue
            raise ProviderHTTPError(
                response.status_code,
                f"供应商请求失败（HTTP {response.status_code}）",
            )
        raise ProviderHTTPError(None, "供应商请求失败")

    def _wait_before_retry(self, attempt: int, retry_after: str | None) -> None:
        delay = 0.5 * (2**attempt)
        if retry_after is not None:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                pass
        delay = min(MAX_RETRY_WAIT_SECONDS, delay + max(0.0, self._jitter()))
        self._sleep(delay)
