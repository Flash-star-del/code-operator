from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_operator.__main__ import build_registry, run_task
from code_operator.config import ProviderConfig
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, ToolCall, ToolResult, Usage
from code_operator.policy import WorkspacePolicy
from code_operator.tools.filesystem import FileTools
from code_operator.tools.filesystem import MAX_FILE_BYTES
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient


def turn(
    *,
    content: str | None = None,
    calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
    total_tokens: int | None = 10,
) -> AssistantTurn:
    usage = (
        None
        if total_tokens is None
        else Usage(prompt_tokens=None, completion_tokens=None, total_tokens=total_tokens)
    )
    return AssistantTurn(
        content=content,
        tool_calls=[] if calls is None else calls,
        finish_reason=finish_reason,
        usage=usage,
    )


def successful_handler(*, tool_call_id: str, path: str, content: str) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        name="write_file",
        ok=True,
        error_code=None,
        message="created",
        details={"path": path, "chars": len(content)},
    )


def test_user_tool_result_and_final_text_form_minimal_loop() -> None:
    call = ToolCall(
        id="call-write",
        name="write_file",
        arguments_raw='{"path":"hello.py","content":"print(1)\\n"}',
    )
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls", total_tokens=5),
            turn(content="已创建并验证。", total_tokens=7),
        ]
    )
    registry = ToolRegistry({"write_file": successful_handler})

    result = AgentLoop(model, registry).run("创建 hello.py")

    assert result.status == "COMPLETED"
    assert result.final_text == "已创建并验证。"
    assert result.model_rounds == 2
    assert result.tool_calls == 1
    assert result.provider_total_tokens == 12
    assert len(model.calls) == 2
    second_messages = model.calls[1][0]
    assert second_messages[0]["role"] == "system"
    assert second_messages[1] == {"role": "user", "content": "创建 hello.py"}
    assert second_messages[2] == call_turn_message(call)
    assert second_messages[3]["role"] == "tool"
    assert second_messages[3]["tool_call_id"] == "call-write"
    assert json.loads(second_messages[3]["content"]) == {
        "ok": True,
        "error_code": None,
        "message": "created",
        "details": {"path": "hello.py", "chars": 9},
    }


def call_turn_message(call: ToolCall) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_raw,
                },
            }
        ],
    }


def test_bad_arguments_still_produce_one_matching_tool_result() -> None:
    bad_call = ToolCall(id="bad", name="write_file", arguments_raw="{")
    model = FakeModelClient(
        [
            turn(calls=[bad_call], finish_reason="tool_calls"),
            turn(content="参数已纠正。"),
        ]
    )

    result = AgentLoop(model, ToolRegistry({})).run("修改文件")

    assert result.status == "COMPLETED"
    tool_messages = [
        message for message in model.calls[1][0] if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "bad"
    assert json.loads(tool_messages[0]["content"])["error_code"] == "INVALID_ARGUMENTS"


def test_empty_or_truncated_final_response_is_not_completed() -> None:
    empty_model = FakeModelClient([turn(content="   ")])
    truncated_model = FakeModelClient([turn(content="partial", finish_reason="length")])

    empty = AgentLoop(empty_model, ToolRegistry({})).run("task")
    truncated = AgentLoop(truncated_model, ToolRegistry({})).run("task")

    assert empty.status == "EMPTY_RESPONSE"
    assert truncated.status == "OUTPUT_TRUNCATED"


def test_missing_usage_makes_provider_total_tokens_unknown() -> None:
    call = ToolCall(id="one", name="list_dir", arguments_raw="{}")
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls", total_tokens=3),
            turn(content="done", total_tokens=None),
        ]
    )

    result = AgentLoop(model, ToolRegistry({})).run("task")

    assert result.provider_total_tokens is None


def test_new_file_is_created_exclusively_with_diff_and_hash(tmp_path: Path) -> None:
    tools = FileTools(WorkspacePolicy(tmp_path))

    result = tools.write_file(
        tool_call_id="write-1", path="hello.py", content="print('hello')\n"
    )

    assert result.ok is True
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert result.details["before_hash"] is None
    assert isinstance(result.details["after_hash"], str)
    assert "+++" in result.details["diff"]
    assert result.details["diff_truncated"] is False


def test_existing_file_requires_complete_unchanged_read_before_overwrite(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("old\n", encoding="utf-8")
    tools = FileTools(WorkspacePolicy(tmp_path))

    unread = tools.write_file(
        tool_call_id="unread", path="sample.py", content="new\n"
    )
    partial = tools.read_file(
        tool_call_id="partial", path="sample.py", start_line=1, end_line=1
    )
    after_partial = tools.write_file(
        tool_call_id="after-partial", path="sample.py", content="new\n"
    )
    complete = tools.read_file(tool_call_id="complete", path="sample.py")
    target.write_text("external\n", encoding="utf-8")
    stale = tools.write_file(
        tool_call_id="stale", path="sample.py", content="new\n"
    )

    assert unread.error_code == "READ_REQUIRED"
    assert partial.ok is True
    assert partial.details["complete"] is False
    assert after_partial.error_code == "READ_REQUIRED"
    assert complete.details["complete"] is True
    assert stale.error_code == "STALE_FILE"
    assert target.read_text(encoding="utf-8") == "external\n"


def test_complete_read_then_overwrite_returns_core_contract(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("old\n", encoding="utf-8")
    tools = FileTools(WorkspacePolicy(tmp_path))
    read = tools.read_file(tool_call_id="read", path="sample.py")

    written = tools.write_file(
        tool_call_id="write", path="sample.py", content="new\n"
    )

    assert read.details == {
        "path": "sample.py",
        "content": "1: old",
        "start_line": 1,
        "end_line": 1,
        "total_lines": 1,
        "truncated": False,
        "complete": True,
    }
    assert written.ok is True
    assert set(written.details) == {
        "path",
        "before_hash",
        "after_hash",
        "diff",
        "diff_truncated",
    }
    assert isinstance(written.details["before_hash"], str)
    assert target.read_text(encoding="utf-8") == "new\n"


def test_overwrite_rejects_oversized_existing_file_before_read_gate(
    tmp_path: Path,
) -> None:
    target = tmp_path / "large.txt"
    with target.open("wb") as stream:
        stream.seek(MAX_FILE_BYTES)
        stream.write(b"x")
    tools = FileTools(WorkspacePolicy(tmp_path))

    result = tools.write_file(
        tool_call_id="large", path="large.txt", content="small"
    )

    assert result.error_code == "FILE_TOO_LARGE"


def test_cli_assembly_connects_model_loop_and_workspace_tools(tmp_path: Path) -> None:
    write_call = ToolCall(
        id="create",
        name="write_file",
        arguments_raw=json.dumps(
            {"path": "hello.py", "content": 'print("hello")\n'},
            ensure_ascii=False,
        ),
    )
    model = FakeModelClient(
        [
            turn(calls=[write_call], finish_reason="tool_calls"),
            turn(content="created"),
        ]
    )
    config = ProviderConfig(
        api_key="private",
        base_url="https://provider.example/v1",
        model="test-model",
    )

    result = run_task(
        config,
        workspace=tmp_path,
        task="create hello.py",
        approve=lambda _argv, _cwd: False,
        client=model,
        environment={},
    )

    assert result.status == "COMPLETED"
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hello")\n'
    assert len(model.calls[0][1]) == 6


def test_cli_registry_configures_all_six_tool_handlers(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    config = ProviderConfig(
        api_key="private",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    registry = build_registry(
        config,
        tmp_path,
        approve=lambda _argv, _cwd: False,
        environment={},
    )
    calls = [
        ToolCall("list", "list_dir", json.dumps({})),
        ToolCall("grep", "grep", json.dumps({"query": "value"})),
        ToolCall("read", "read_file", json.dumps({"path": "sample.py"})),
        ToolCall(
            "edit",
            "edit_file",
            json.dumps(
                {
                    "path": "sample.py",
                    "old_text": "value = 1",
                    "new_text": "value = 2",
                }
            ),
        ),
    ]

    results = registry.execute_calls(calls)

    assert [result.ok for result in results] == [True, True, True, True]
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_repeated_call_stops_after_third_equal_result() -> None:
    calls = [
        ToolCall(
            id=f"repeat-{index}",
            name="list_dir",
            arguments_raw='{"path":"."}',
        )
        for index in range(3)
    ]
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls")
            for call in calls
        ]
        + [AssertionError("第三次相同调用结果后不得再次请求模型")]
    )

    def repeated_handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="same",
            details={"text": "same"},
        )

    result = AgentLoop(
        model, ToolRegistry({"list_dir": repeated_handler})
    ).run("inspect")

    assert result.status == "REPEATED_CALL"
    assert result.model_rounds == 3
    assert result.tool_calls == 3
    assert len(model.calls) == 3


def test_five_consecutive_tool_failures_stop_before_next_model_request() -> None:
    calls = [
        ToolCall(
            id=f"failure-{index}",
            name="read_file",
            arguments_raw=json.dumps({"path": f"missing-{index}.txt"}),
        )
        for index in range(5)
    ]
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls")
            for call in calls
        ]
        + [AssertionError("连续五次失败后不得再次请求模型")]
    )

    def failing_handler(*, tool_call_id: str, path: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="read_file",
            ok=False,
            error_code="FILE_NOT_FOUND",
            message="missing",
            details={"path": path},
        )

    result = AgentLoop(
        model, ToolRegistry({"read_file": failing_handler})
    ).run("inspect")

    assert result.status == "CONSECUTIVE_TOOL_FAILURES"
    assert result.model_rounds == 5
    assert result.tool_calls == 5
    assert len(model.calls) == 5


def test_multi_call_turn_returns_exactly_one_result_for_each_failure_kind() -> None:
    calls = [
        ToolCall("ok", "list_dir", "{}"),
        ToolCall("bad", "write_file", "{"),
        ToolCall("denied", "run_command", '{"argv":["python","script.py"]}'),
        ToolCall("unknown", "not_registered", "{}"),
    ]
    model = FakeModelClient(
        [
            turn(calls=calls, finish_reason="tool_calls"),
            turn(content="handled"),
        ]
    )

    def list_handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="listed",
            details={},
        )

    def denied_handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="run_command",
            ok=False,
            error_code="COMMAND_DENIED",
            message="denied",
            details={},
        )

    result = AgentLoop(
        model,
        ToolRegistry(
            {"list_dir": list_handler, "run_command": denied_handler}
        ),
    ).run("inspect")

    tool_messages = [
        message for message in model.calls[1][0] if message.get("role") == "tool"
    ]
    assert result.status == "COMPLETED"
    assert [message["tool_call_id"] for message in tool_messages] == [
        "ok",
        "bad",
        "denied",
        "unknown",
    ]
    assert [
        json.loads(str(message["content"]))["error_code"]
        for message in tool_messages
    ] == [None, "INVALID_ARGUMENTS", "COMMAND_DENIED", "UNKNOWN_TOOL"]


def test_tool_budget_exhaustion_still_pairs_every_call_in_turn(
    monkeypatch,
) -> None:
    calls = [
        ToolCall(f"limit-{index}", "list_dir", "{}")
        for index in range(3)
    ]
    model = FakeModelClient([turn(calls=calls, finish_reason="tool_calls")])
    paired_ids: list[str] = []
    original_to_message_content = ToolResult.to_message_content

    def recording_content(tool_result: ToolResult) -> str:
        paired_ids.append(tool_result.tool_call_id)
        return original_to_message_content(tool_result)

    monkeypatch.setattr(ToolResult, "to_message_content", recording_content)

    result = AgentLoop(
        model,
        ToolRegistry({}),
        max_tool_calls=1,
    ).run("inspect")

    assert result.status == "TOOL_CALL_LIMIT"
    assert result.tool_calls == 3
    assert paired_ids == ["limit-0", "limit-1", "limit-2"]


class InterruptingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *_: object) -> AssistantTurn:
        self.calls += 1
        raise KeyboardInterrupt


def test_ctrl_c_during_model_request_returns_user_aborted() -> None:
    model = InterruptingModel()

    result = AgentLoop(model, ToolRegistry({})).run("inspect")

    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 0
    assert model.calls == 1


def test_ctrl_c_during_tool_approval_returns_user_aborted() -> None:
    call = ToolCall("approval", "run_command", '{"argv":["python","script.py"]}')
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls"),
            AssertionError("审批中止后不得再次请求模型"),
        ]
    )

    def interrupting_handler(**_: object) -> ToolResult:
        raise KeyboardInterrupt

    result = AgentLoop(
        model,
        ToolRegistry({"run_command": interrupting_handler}),
    ).run("run")

    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 1
    assert len(model.calls) == 1


def test_context_limit_stops_before_any_model_request() -> None:
    model = FakeModelClient([AssertionError("上下文超限时不得请求模型")])

    result = AgentLoop(
        model,
        ToolRegistry({}),
        context_window=400,
        max_output_tokens=200,
        system_prompt="short system",
    ).run("x" * 2_000)

    assert result.status == "CONTEXT_LIMIT"
    assert result.model_rounds == 0
    assert result.provider_total_tokens is None
    assert len(model.calls) == 0


def test_run_task_passes_provider_context_limits_to_loop(tmp_path: Path) -> None:
    model = FakeModelClient([AssertionError("配置的上下文上限必须在请求前生效")])
    config = ProviderConfig(
        api_key="private",
        base_url="https://provider.example/v1",
        model="test-model",
        context_window=400,
        max_output_tokens=200,
    )

    result = run_task(
        config,
        workspace=tmp_path,
        task="x" * 2_000,
        approve=lambda _argv, _cwd: False,
        client=model,
        environment={},
    )

    assert result.status == "CONTEXT_LIMIT"
    assert len(model.calls) == 0


def test_default_model_round_limit_is_sixteen() -> None:
    calls = [
        ToolCall(
            f"round-{index}",
            "read_file",
            json.dumps({"path": f"file-{index}.txt"}),
        )
        for index in range(16)
    ]
    model = FakeModelClient(
        [turn(calls=[call], finish_reason="tool_calls") for call in calls]
        + [AssertionError("默认 16 轮结束后不得继续请求模型")]
    )

    def handler(*, tool_call_id: str, path: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="read_file",
            ok=True,
            error_code=None,
            message="read",
            details={"path": path},
        )

    result = AgentLoop(
        model, ToolRegistry({"read_file": handler})
    ).run("inspect")

    assert result.status == "MODEL_ROUND_LIMIT"
    assert result.model_rounds == 16
    assert len(model.calls) == 16


def test_content_filter_is_a_terminal_non_success_state() -> None:
    model = FakeModelClient(
        [turn(content="partial", finish_reason="content_filter")]
    )

    result = AgentLoop(model, ToolRegistry({})).run("task")

    assert result.status == "CONTENT_FILTERED"
    assert result.final_text == "partial"


@pytest.mark.parametrize(
    "error_code",
    ["PATH_DENIED", "COMMAND_FAILED", "COMMAND_TIMEOUT"],
)
def test_local_execution_errors_are_replayed_with_original_call_id(
    error_code: str,
) -> None:
    call = ToolCall("local-error", "read_file", '{"path":"sample.txt"}')
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls"),
            turn(content="corrected"),
        ]
    )

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="read_file",
            ok=False,
            error_code=error_code,
            message="recoverable",
            details={},
        )

    result = AgentLoop(
        model, ToolRegistry({"read_file": handler})
    ).run("inspect")

    tool_messages = [
        item for item in model.calls[1][0] if item.get("role") == "tool"
    ]
    assert result.status == "COMPLETED"
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "local-error"
    assert json.loads(str(tool_messages[0]["content"]))["error_code"] == error_code
