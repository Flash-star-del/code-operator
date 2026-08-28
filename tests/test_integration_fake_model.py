from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from code_operator.__main__ import build_registry, run_task
from code_operator.client import ModelClient, parse_assistant_turn
from code_operator.config import ProviderConfig
from code_operator.context import ContextManager
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, ToolCall, ToolResult
from code_operator.tools.filesystem import MAX_TOOL_OUTPUT_CHARS
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient


CONFIG = ProviderConfig(
    api_key="integration-private-key",
    base_url="https://provider.example/v1",
    model="scripted-model",
)


def provider_turn(
    *,
    response_id: str,
    content: str | None = None,
    calls: list[dict[str, object]] | None = None,
    finish_reason: str = "tool_calls",
    total_tokens: int = 7,
) -> AssistantTurn:
    message: dict[str, object] = {
        "role": "assistant",
        "content": content,
        "reasoning_content": "供应商内部字段不得回放",
        "vendor_unknown": "ignored",
    }
    if calls is not None:
        message["tool_calls"] = calls
    return parse_assistant_turn(
        {
            "id": response_id,
            "model": "scripted-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": max(0, total_tokens - 2),
                "completion_tokens": 2,
                "total_tokens": total_tokens,
            },
        }
    )


def raw_call(call_id: str, name: str, arguments: dict[str, object] | str) -> dict[str, object]:
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": encoded},
    }


def tool_results(messages: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        str(message["tool_call_id"]): json.loads(str(message["content"]))
        for message in messages
        if message.get("role") == "tool"
    }


def assert_replay_contract(messages: list[dict[str, object]]) -> None:
    assistant_messages = [
        message for message in messages if message.get("role") == "assistant"
    ]
    for message in assistant_messages:
        assert set(message) <= {"role", "content", "tool_calls"}
        assert not {"finish_reason", "usage", "request_id", "reasoning_content"}.intersection(
            message
        )

    call_ids = [
        str(call["id"])
        for message in assistant_messages
        for call in message.get("tool_calls", [])
    ]
    result_ids = [
        str(message["tool_call_id"])
        for message in messages
        if message.get("role") == "tool"
    ]
    assert result_ids == call_ids
    assert len(result_ids) == len(set(result_ids))


def test_scripted_model_completes_read_search_red_green_fix_chain(
    tmp_path: Path,
) -> None:
    source = tmp_path / "math_utils.py"
    source.write_text(
        "def add(left, right):\n"
        "    return left - right\n\n\n"
        "def multiply(left, right):\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    (tmp_path / "test_math_utils.py").write_text(
        "from math_utils import add, multiply\n\n\n"
        "def test_add():\n"
        "    assert add(7, 3) == 10\n\n\n"
        "def test_multiply():\n"
        "    assert multiply(7, 3) == 21\n",
        encoding="utf-8",
    )
    turns = [
        provider_turn(
            response_id="response-inspect",
            calls=[
                raw_call("read-source", "read_file", {"path": "math_utils.py"}),
                raw_call(
                    "search-returns",
                    "grep",
                    {"query": "return", "path": ".", "file_pattern": "*.py"},
                ),
            ],
        ),
        provider_turn(
            response_id="response-fix-add",
            calls=[
                raw_call(
                    "fix-add",
                    "edit_file",
                    {
                        "path": "math_utils.py",
                        "old_text": "return left - right",
                        "new_text": "return sum((left, right))",
                    },
                )
            ],
        ),
        provider_turn(
            response_id="response-red",
            calls=[
                raw_call(
                    "first-pytest",
                    "run_command",
                    {"argv": ["python", "-m", "pytest", "-q"], "timeout_seconds": 30},
                )
            ],
        ),
        provider_turn(
            response_id="response-fix-multiply",
            calls=[
                raw_call(
                    "fix-multiply",
                    "edit_file",
                    {
                        "path": "math_utils.py",
                        "old_text": "return left + right",
                        "new_text": "return left * right  # fixed multiply",
                    },
                )
            ],
        ),
        provider_turn(
            response_id="response-green",
            calls=[
                raw_call(
                    "second-pytest",
                    "run_command",
                    {"argv": ["python", "-m", "pytest", "-q"], "timeout_seconds": 30},
                )
            ],
        ),
        provider_turn(
            response_id="response-summary",
            content="已修复加法和乘法实现；最终测试 2 项通过。",
            calls=[],
            finish_reason="stop",
        ),
    ]
    model = FakeModelClient(turns)

    result = run_task(
        CONFIG,
        workspace=tmp_path,
        task="定位并修复 math_utils.py，运行测试确认后总结。",
        approve=lambda _argv, _cwd: False,
        client=model,
        environment=os.environ,
        auto_approve_tests=True,
    )

    assert result.status == "COMPLETED"
    assert result.model_rounds == 6
    assert result.tool_calls == 6
    assert result.provider_total_tokens == 42
    assert result.final_text == "已修复加法和乘法实现；最终测试 2 项通过。"
    assert "return sum((left, right))" in source.read_text(encoding="utf-8")
    assert "return left * right" in source.read_text(encoding="utf-8")

    final_history = model.calls[-1][0]
    results = tool_results(final_history)
    assert list(results) == [
        "read-source",
        "search-returns",
        "fix-add",
        "first-pytest",
        "fix-multiply",
        "second-pytest",
    ]
    assert results["first-pytest"]["ok"] is False
    assert results["first-pytest"]["error_code"] == "COMMAND_FAILED"
    assert results["first-pytest"]["details"]["exit_code"] != 0
    assert "1 failed" in results["first-pytest"]["details"]["stdout"]
    assert results["second-pytest"]["ok"] is True
    assert results["second-pytest"]["details"]["exit_code"] == 0
    assert "2 passed" in results["second-pytest"]["details"]["stdout"]
    for request_messages, _tools in model.calls:
        assert_replay_contract(request_messages)


def test_multi_call_failures_keep_order_ids_and_recover(tmp_path: Path) -> None:
    calls = [
        raw_call("unknown", "missing_tool", {}),
        raw_call("bad-arguments", "read_file", "{"),
        raw_call(
            "denied-shell",
            "run_command",
            {"argv": ["powershell", "-Command", "Write-Output forbidden"]},
        ),
    ]
    model = FakeModelClient(
        [
            provider_turn(response_id="response-errors", calls=calls),
            provider_turn(
                response_id="response-recovered",
                content="已识别并停止不合法操作。",
                calls=[],
                finish_reason="stop",
            ),
        ]
    )

    result = run_task(
        CONFIG,
        workspace=tmp_path,
        task="检查错误回灌。",
        approve=lambda _argv, _cwd: True,
        client=model,
        environment=os.environ,
    )

    results = tool_results(model.calls[1][0])
    assert result.status == "COMPLETED"
    assert list(results) == ["unknown", "bad-arguments", "denied-shell"]
    assert [item["error_code"] for item in results.values()] == [
        "UNKNOWN_TOOL",
        "INVALID_ARGUMENTS",
        "COMMAND_DENIED",
    ]
    assert_replay_contract(model.calls[1][0])


def test_successful_http_with_bad_json_stops_without_retry() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"{not-json")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ModelClient(CONFIG, http_client=http_client, sleep=lambda _: None)
        result = AgentLoop(client, ToolRegistry({})).run("inspect")

    assert result.status == "PROVIDER_PROTOCOL_ERROR"
    assert result.model_rounds == 0
    assert attempts == 1


def test_retryable_http_errors_recover_inside_agent_loop() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(
            200,
            json={
                "id": "response-after-retry",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "recovered"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 5},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ModelClient(
            CONFIG,
            http_client=http_client,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
        )
        result = AgentLoop(client, ToolRegistry({})).run("inspect")

    assert result.status == "COMPLETED"
    assert result.final_text == "recovered"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_command_timeout_and_output_truncation_are_replayed(
    tmp_path: Path,
) -> None:
    calls = [
        ToolCall(
            "timeout",
            "run_command",
            json.dumps(
                {
                    "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
                    "timeout_seconds": 1,
                }
            ),
        ),
        ToolCall(
            "large-output",
            "run_command",
            json.dumps(
                {
                    "argv": [sys.executable, "-c", "print('HEAD-' + 'x'*13000 + '-TAIL')"],
                    "timeout_seconds": 10,
                }
            ),
        ),
    ]
    model = FakeModelClient(
        [
            AssistantTurn(content=None, tool_calls=calls, finish_reason="tool_calls"),
            AssistantTurn(content="handled", tool_calls=[], finish_reason="stop"),
        ]
    )
    registry = build_registry(
        CONFIG,
        tmp_path,
        approve=lambda _argv, _cwd: True,
        environment=os.environ,
    )

    result = AgentLoop(model, registry).run("run bounded commands")

    results = tool_results(model.calls[1][0])
    assert result.status == "COMPLETED"
    assert list(results) == ["timeout", "large-output"]
    assert results["timeout"]["error_code"] == "COMMAND_TIMEOUT"
    assert results["timeout"]["details"]["timed_out"] is True
    assert results["large-output"]["ok"] is True
    assert results["large-output"]["details"]["stdout_truncated"] is True
    output = str(results["large-output"]["details"]["stdout"])
    assert output.startswith("HEAD-")
    assert output.rstrip().endswith("-TAIL")
    assert len(output) <= MAX_TOOL_OUTPUT_CHARS


def test_agent_loop_crops_only_oldest_complete_tool_round() -> None:
    old_call = ToolCall("old-round", "read_file", '{"path":"old"}')
    current_call = ToolCall("current-round", "read_file", '{"path":"current"}')

    def read_handler(*, tool_call_id: str, path: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="read_file",
            ok=True,
            error_code=None,
            message="read",
            details={"path": path, "content": path * 4_000},
        )

    registry = ToolRegistry({"read_file": read_handler})
    old_result = read_handler(tool_call_id=old_call.id, path="old")
    current_result = read_handler(tool_call_id=current_call.id, path="current")
    prefix = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "inspect"},
    ]
    old_round = [
        AssistantTurn(None, [old_call]).to_replay_message(),
        {
            "role": "tool",
            "tool_call_id": old_call.id,
            "name": old_call.name,
            "content": old_result.to_message_content(),
        },
    ]
    current_round = [
        AssistantTurn(None, [current_call]).to_replay_message(),
        {
            "role": "tool",
            "tool_call_id": current_call.id,
            "name": current_call.name,
            "content": current_result.to_message_content(),
        },
    ]
    schemas = registry.tool_schemas()
    sizing = ContextManager(context_window=100_000, max_output_tokens=100)
    single_round_budget = max(
        sizing.estimate_tokens(prefix + old_round, schemas),
        sizing.estimate_tokens(prefix + current_round, schemas),
    )
    assert sizing.estimate_tokens(prefix + old_round + current_round, schemas) > (
        single_round_budget + 1
    )
    model = FakeModelClient(
        [
            AssistantTurn(None, [old_call], finish_reason="tool_calls"),
            AssistantTurn(None, [current_call], finish_reason="tool_calls"),
            AssistantTurn("done", [], finish_reason="stop"),
        ]
    )

    result = AgentLoop(
        model,
        registry,
        context_window=single_round_budget + 101,
        max_output_tokens=100,
        system_prompt="system",
    ).run("inspect")

    third_request = json.dumps(model.calls[2][0], ensure_ascii=False)
    assert result.status == "COMPLETED"
    assert "old-round" not in third_request
    assert "current-round" in third_request
    assert_replay_contract(model.calls[2][0])


def test_empty_final_response_remains_non_success_in_integrated_loop() -> None:
    model = FakeModelClient([AssistantTurn("   ", [], finish_reason="stop")])

    result = AgentLoop(model, ToolRegistry({})).run("inspect")

    assert result.status == "EMPTY_RESPONSE"
    assert result.final_text == ""
