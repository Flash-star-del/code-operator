from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code_operator.config import ConfigError, ProviderConfig, load_provider_config


TEXT_MAX_TOKENS = 8
TOOL_MAX_TOKENS = 256
PROBE_TOOL_NAME = "return_probe_token"
PROBE_TOKEN = "PROBE_OK"
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": PROBE_TOOL_NAME,
        "description": "Return the fixed protocol probe token requested by the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "probe_token": {
                    "type": "string",
                    "enum": [PROBE_TOKEN],
                }
            },
            "required": ["probe_token"],
            "additionalProperties": False,
        },
    },
}


class ProbeError(RuntimeError):
    """Raised when the provider probe cannot establish a protocol contract."""


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "<REDACTED>") if api_key else text


def _safe_error(response: httpx.Response, api_key: str) -> str:
    details: dict[str, object] = {"http_status": response.status_code}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        for key in ("type", "code", "message"):
            value = error.get(key)
            if isinstance(value, (str, int)):
                details[key] = _redact(str(value), api_key)[:500]
    return json.dumps(details, ensure_ascii=False, sort_keys=True)


def _post(
    client: httpx.Client,
    config: ProviderConfig,
    payload: dict[str, object],
) -> tuple[httpx.Response, dict[str, Any]]:
    try:
        response = client.post(
            config.endpoint,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.HTTPError as exc:
        raise ProbeError(_redact(str(exc), config.api_key)) from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ProbeError(_safe_error(response, config.api_key))
    try:
        body = response.json()
    except ValueError as exc:
        raise ProbeError("供应商返回的成功响应不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise ProbeError("供应商响应顶层必须是 JSON object")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ProbeError("供应商响应必须恰好包含一个 choice")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ProbeError("供应商 choice 缺少合法 message")
    return response, body


def _step_evidence(
    name: str,
    response: httpx.Response,
    body: dict[str, Any],
) -> dict[str, object]:
    choice = body["choices"][0]
    message = choice["message"]
    usage = body.get("usage")
    return {
        "name": name,
        "http_status": response.status_code,
        "response_id": body.get("id"),
        "response_model": body.get("model"),
        "top_level_fields": sorted(body),
        "message_fields": sorted(message),
        "finish_reason": choice.get("finish_reason"),
        "usage_present": isinstance(usage, dict),
        "usage_fields": sorted(usage) if isinstance(usage, dict) else [],
        "request_id_present": bool(response.headers.get("x-request-id")),
    }


def completion_limit_evidence(
    body: dict[str, Any], maximum: int
) -> dict[str, object]:
    usage = body.get("usage")
    completion_tokens = (
        usage.get("completion_tokens") if isinstance(usage, dict) else None
    )
    if isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int):
        return {
            "usage_available": False,
            "completion_tokens": None,
            "maximum": maximum,
            "verified": False,
        }
    if completion_tokens > maximum:
        raise ProbeError(
            f"供应商 completion_tokens={completion_tokens} 超过请求上限 {maximum}"
        )
    return {
        "usage_available": True,
        "completion_tokens": completion_tokens,
        "maximum": maximum,
        "verified": True,
    }


def _assistant_replay_message(message: dict[str, Any]) -> dict[str, object]:
    return {
        "role": message.get("role", "assistant"),
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls"),
    }


def _sanitize_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_structure(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_structure(item) for item in value]
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    if value is None:
        return None
    return "<REDACTED>"


def _sanitized_fixture(tool_body: dict[str, Any]) -> dict[str, object]:
    choice = tool_body["choices"][0]
    message = choice["message"]
    sanitized_message: dict[str, object] = {
        "role": message.get("role", "assistant"),
        "content": None if message.get("content") is None else "<REDACTED>",
        "tool_calls": [
            {
                "id": "probe_call_1",
                "type": "function",
                "function": {
                    "name": PROBE_TOOL_NAME,
                    "arguments": json.dumps(
                        {"probe_token": PROBE_TOKEN},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
        ],
    }
    if "reasoning_content" in message:
        sanitized_message["reasoning_content"] = "<REDACTED>"
    usage = tool_body.get("usage")
    sanitized_usage = _sanitize_structure(usage) if isinstance(usage, dict) else None
    return {
        "id": "probe_response",
        "model": "configured-model",
        "choices": [
            {
                "message": sanitized_message,
                "finish_reason": choice.get("finish_reason"),
            }
        ],
        "usage": sanitized_usage,
    }


def run_protocol_probe(
    config: ProviderConfig,
    client: httpx.Client,
    fixture_path: Path,
) -> dict[str, object]:
    text_payload: dict[str, object] = {
        "model": config.model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "n": 1,
        "max_tokens": TEXT_MAX_TOKENS,
    }
    text_response, text_body = _post(client, config, text_payload)
    output_limit = completion_limit_evidence(text_body, TEXT_MAX_TOKENS)

    tool_messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": (
                "Call return_probe_token exactly once with probe_token set to PROBE_OK."
            ),
        }
    ]
    tool_payload: dict[str, object] = {
        "model": config.model,
        "messages": tool_messages,
        "tools": [PROBE_TOOL],
        "tool_choice": {
            "type": "function",
            "function": {"name": PROBE_TOOL_NAME},
        },
        "thinking": {"type": "disabled"},
        "n": 1,
        "max_tokens": TOOL_MAX_TOKENS,
    }
    tool_response, tool_body = _post(client, config, tool_payload)
    tool_message = tool_body["choices"][0]["message"]
    tool_calls = tool_message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise ProbeError("命名 tool_choice 未返回恰好一个原生 tool_call")
    tool_call = tool_calls[0]
    if not isinstance(tool_call, dict):
        raise ProbeError("tool_call 必须是 object")
    tool_call_id = tool_call.get("id")
    function = tool_call.get("function")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ProbeError("tool_call 缺少非空 id")
    if not isinstance(function, dict) or function.get("name") != PROBE_TOOL_NAME:
        raise ProbeError("tool_call 函数名与指定探针工具不匹配")
    arguments_raw = function.get("arguments")
    if not isinstance(arguments_raw, str):
        raise ProbeError("tool_call arguments 必须是 JSON 字符串")
    try:
        arguments = json.loads(arguments_raw)
    except ValueError as exc:
        raise ProbeError("tool_call arguments 不是合法 JSON") from exc
    if arguments != {"probe_token": PROBE_TOKEN}:
        raise ProbeError("tool_call arguments 与固定探针参数不匹配")

    replay_messages = [
        *tool_messages,
        _assistant_replay_message(tool_message),
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": PROBE_TOOL_NAME,
            "content": json.dumps(
                {"ok": True, "probe_token": PROBE_TOKEN},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    replay_payload: dict[str, object] = {
        "model": config.model,
        "messages": replay_messages,
        "tools": [PROBE_TOOL],
        "thinking": {"type": "disabled"},
        "n": 1,
        "max_tokens": TOOL_MAX_TOKENS,
    }
    replay_response, replay_body = _post(client, config, replay_payload)

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        json.dumps(_sanitized_fixture(tool_body), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "endpoint": config.endpoint,
        "configured_model": config.model,
        "n": 1,
        "text_max_tokens": TEXT_MAX_TOKENS,
        "tool_max_tokens": TOOL_MAX_TOKENS,
        "tool_choice": "named_function",
        "output_limit": output_limit,
        "steps": [
            _step_evidence("text", text_response, text_body),
            _step_evidence("tool_call", tool_response, tool_body),
            _step_evidence("tool_result_replay", replay_response, replay_body),
        ],
        "tool_call": {
            "name": PROBE_TOOL_NAME,
            "id_present": True,
            "arguments_valid": True,
            "result_id_matched": True,
            "reasoning_content_present": "reasoning_content" in tool_message,
        },
        "fixture": fixture_path.as_posix(),
    }


def main() -> int:
    try:
        config = load_provider_config()
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "provider_tool_call.json"
        )
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(timeout=timeout) as client:
            evidence = run_protocol_probe(config, client, fixture_path)
    except (ConfigError, ProbeError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True, **evidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
