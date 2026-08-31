from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_operator.__main__ import build_registry, run_task
from code_operator.client import ProviderError, ProviderProtocolError
from code_operator.config import ProviderConfig
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, RunResult, ToolCall, ToolResult, Usage
from code_operator.policy import WorkspacePolicy
from code_operator.tools.filesystem import FileTools
from code_operator.tools.filesystem import MAX_FILE_BYTES
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def record_model_round(
        self, round_number: int, tool_call_count: int, usage_available: bool
    ) -> None:
        self.events.append(("model", round_number, tool_call_count, usage_available))

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        self.events.append(("tool", call.id, result.tool_call_id, result.ok))

    def record_run(self, result: RunResult) -> None:
        self.events.append(("run", result.status, result.provider_total_tokens is not None))


class BrokenTrace:
    def record_model_round(self, *_: object) -> None:
        raise OSError("trace model")

    def record_tool(self, *_: object) -> None:
        raise OSError("trace tool")

    def record_run(self, *_: object) -> None:
        raise OSError("trace run")


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        self.events.append(
            ("tool", call.id, result.tool_call_id, result.error_code)
        )

    def record_run(self, result: RunResult) -> None:
        self.events.append(("run", result.status))


def assert_one_run_event(
    trace: RecordingTrace, status: str, usage_available: bool
) -> None:
    assert [event for event in trace.events if event[0] == "run"] == [
        ("run", status, usage_available)
    ]


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


def test_trace_records_model_tool_model_run_with_usage_availability() -> None:
    call = ToolCall(
        "trace-call", "write_file", '{"path":"trace.py","content":"pass\\n"}'
    )
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls", total_tokens=5),
            turn(content="done", total_tokens=None),
        ]
    )
    trace = RecordingTrace()

    result = AgentLoop(
        model, ToolRegistry({"write_file": successful_handler}), trace=trace
    ).run("inspect")

    assert result.status == "COMPLETED"
    assert trace.events == [
        ("model", 1, 1, True),
        ("tool", "trace-call", "trace-call", True),
        ("model", 2, 0, False),
        ("run", "COMPLETED", False),
    ]
    assert result.provider_total_tokens is None


def test_broken_trace_does_not_change_successful_tool_loop() -> None:
    call = ToolCall(
        "broken-call", "write_file", '{"path":"broken.py","content":"pass\\n"}'
    )
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls"),
            turn(content="done"),
        ]
    )

    result = AgentLoop(
        model,
        ToolRegistry({"write_file": successful_handler}),
        trace=BrokenTrace(),
    ).run("inspect")

    assert result.status == "COMPLETED"
    assert result.model_rounds == 2
    assert result.tool_calls == 1
    second_messages = model.calls[1][0]
    tool_message = next(message for message in second_messages if message["role"] == "tool")
    assert json.loads(str(tool_message["content"]))["ok"] is True


def test_context_limit_records_only_one_run_event_without_model_round() -> None:
    model = FakeModelClient([AssertionError("不得请求模型")])
    trace = RecordingTrace()

    result = AgentLoop(
        model,
        ToolRegistry({}),
        context_window=400,
        max_output_tokens=200,
        system_prompt="short system",
        trace=trace,
    ).run("x" * 2_000)

    assert result.status == "CONTEXT_LIMIT"
    assert trace.events == [("run", "CONTEXT_LIMIT", False)]


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


def test_deep_json_tool_arguments_keep_session_history_replayable() -> None:
    deeply_nested = "[" * 2_000 + "0" + "]" * 2_000
    call = ToolCall("deep", "list_dir", deeply_nested)
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls"),
            turn(content="first recovered"),
            turn(content="second recovered"),
        ]
    )
    audit = RecordingAudit()
    trace = RecordingTrace()
    loop = AgentLoop(
        model,
        ToolRegistry({}),
        system_prompt="sys",
        audit=audit,
        trace=trace,
    )

    first = loop.run("deep input")
    second = loop.run("next input")

    assert first.status == second.status == "COMPLETED"
    assert len(model.calls) == 3
    first_tool_message = model.calls[1][0][3]
    assert first_tool_message["role"] == "tool"
    assert first_tool_message["tool_call_id"] == "deep"
    assert json.loads(str(first_tool_message["content"])) == {
        "ok": False,
        "error_code": "INVALID_ARGUMENTS",
        "message": "arguments 必须是合法 JSON 对象",
        "details": {},
    }
    assert [message["role"] for message in model.calls[2][0]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert [event for event in audit.events if event[0] == "run"] == [
        ("run", "COMPLETED"),
        ("run", "COMPLETED"),
    ]
    assert [event for event in trace.events if event[0] == "run"] == [
        ("run", "COMPLETED", True),
        ("run", "COMPLETED", True),
    ]


def test_unserializable_tool_details_keep_session_history_replayable() -> None:
    call = ToolCall("unsafe", "list_dir", "{}")
    model = FakeModelClient(
        [
            turn(calls=[call], finish_reason="tool_calls"),
            turn(content="first recovered"),
            turn(content="second recovered"),
        ]
    )

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="unsafe",
            details={"value": object()},
        )

    audit = RecordingAudit()
    trace = RecordingTrace()
    loop = AgentLoop(
        model,
        ToolRegistry({"list_dir": handler}),
        system_prompt="sys",
        audit=audit,
        trace=trace,
    )

    first = loop.run("unsafe result")
    second = loop.run("next input")

    assert first.status == second.status == "COMPLETED"
    assert len(model.calls) == 3
    first_tool_message = model.calls[1][0][3]
    assert first_tool_message["role"] == "tool"
    assert first_tool_message["tool_call_id"] == "unsafe"
    content = str(first_tool_message["content"])
    assert "object at" not in content
    assert json.loads(content) == {
        "ok": False,
        "error_code": "TOOL_EXECUTION_ERROR",
        "message": "工具返回了无效结果",
        "details": {},
    }
    assert [message["role"] for message in model.calls[2][0]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert [event for event in audit.events if event[0] == "run"] == [
        ("run", "COMPLETED"),
        ("run", "COMPLETED"),
    ]
    assert [event for event in trace.events if event[0] == "run"] == [
        ("run", "COMPLETED", True),
        ("run", "COMPLETED", True),
    ]


def test_invalid_tool_call_ids_do_not_poison_later_session_history() -> None:
    invalid_calls = [
        ToolCall("same", "list_dir", "{}"),
        ToolCall("same", "list_dir", "{}"),
    ]
    model = FakeModelClient(
        [
            turn(calls=invalid_calls, finish_reason="tool_calls"),
            turn(content="recovered"),
        ]
    )
    loop = AgentLoop(model, ToolRegistry({}), system_prompt="sys")

    invalid = loop.run("invalid turn")
    recovered = loop.run("next turn")

    assert invalid.status == "PROVIDER_PROTOCOL_ERROR"
    assert recovered.status == "COMPLETED"
    assert model.calls[1][0] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "invalid turn"},
        {"role": "user", "content": "next turn"},
    ]


def test_empty_or_truncated_final_response_is_not_completed() -> None:
    empty_model = FakeModelClient([turn(content="   ")])
    truncated_model = FakeModelClient([turn(content="partial", finish_reason="length")])
    empty_trace = RecordingTrace()
    truncated_trace = RecordingTrace()

    empty = AgentLoop(empty_model, ToolRegistry({}), trace=empty_trace).run("task")
    truncated = AgentLoop(
        truncated_model, ToolRegistry({}), trace=truncated_trace
    ).run("task")

    assert empty.status == "EMPTY_RESPONSE"
    assert truncated.status == "OUTPUT_TRUNCATED"
    assert_one_run_event(empty_trace, "EMPTY_RESPONSE", True)
    assert_one_run_event(truncated_trace, "OUTPUT_TRUNCATED", True)


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


def test_reentrant_runs_keep_history_but_reset_rounds_and_usage() -> None:
    model = FakeModelClient(
        [
            turn(content="first answer", total_tokens=3),
            turn(content="second answer", total_tokens=5),
        ]
    )
    loop = AgentLoop(model, ToolRegistry({}), system_prompt="custom system")

    first = loop.run("first user")
    second = loop.run("second user")

    assert first.model_rounds == second.model_rounds == 1
    assert first.provider_total_tokens == 3
    assert second.provider_total_tokens == 5
    assert model.calls[1][0] == [
        {"role": "system", "content": "custom system"},
        {"role": "user", "content": "first user"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second user"},
    ]


def test_reentrant_runs_reset_tool_failure_repeat_and_usage_counters() -> None:
    first_calls = [
        ToolCall(f"first-{index}", "list_dir", "{}") for index in range(2)
    ]
    second_calls = [
        ToolCall(f"second-{index}", "list_dir", "{}") for index in range(2)
    ]
    model = FakeModelClient(
        [
            turn(calls=first_calls, finish_reason="tool_calls", total_tokens=1),
            turn(content="first done", total_tokens=2),
            turn(calls=second_calls, finish_reason="tool_calls", total_tokens=2),
            turn(content="second done", total_tokens=3),
        ]
    )

    def repeated_failure(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=False,
            error_code="TEST_FAILURE",
            message="same",
            details={},
        )

    loop = AgentLoop(
        model,
        ToolRegistry({"list_dir": repeated_failure}),
        max_tool_calls=2,
    )

    first = loop.run("first")
    second = loop.run("second")

    assert (first.status, first.model_rounds, first.tool_calls) == (
        "COMPLETED",
        2,
        2,
    )
    assert (second.status, second.model_rounds, second.tool_calls) == (
        "COMPLETED",
        2,
        2,
    )
    assert first.provider_total_tokens == 3
    assert second.provider_total_tokens == 5
    assert len(model.calls) == 4


def test_reset_keeps_only_custom_system_prompt_for_next_run() -> None:
    model = FakeModelClient(
        [turn(content="first answer"), turn(content="second answer")]
    )
    loop = AgentLoop(model, ToolRegistry({}), system_prompt="custom system")

    loop.run("first user")
    loop.reset()
    result = loop.run("second user")

    assert result.status == "COMPLETED"
    assert model.calls[1][0] == [
        {"role": "system", "content": "custom system"},
        {"role": "user", "content": "second user"},
    ]


def test_trimmed_history_is_removed_from_persistent_messages() -> None:
    model = FakeModelClient(
        [
            turn(content="x" * 2_500),
            turn(content="second answer"),
            turn(content="third answer"),
        ]
    )
    loop = AgentLoop(
        model,
        ToolRegistry({}),
        context_window=1_600,
        max_output_tokens=200,
        system_prompt="sys",
    )

    loop.run("first user")
    second = loop.run("second user")
    third = loop.run("third user")

    assert second.status == third.status == "COMPLETED"
    assert [message["content"] for message in model.calls[1][0]] == [
        "sys",
        "second user",
    ]
    assert [message["content"] for message in model.calls[2][0]] == [
        "sys",
        "second user",
        "second answer",
        "third user",
    ]


def test_oversized_new_user_is_rolled_back_without_damaging_history() -> None:
    model = FakeModelClient(
        [turn(content="first answer"), turn(content="second answer")]
    )
    loop = AgentLoop(
        model,
        ToolRegistry({}),
        context_window=4_000,
        max_output_tokens=200,
        system_prompt="sys",
    )

    first = loop.run("first user")
    oversized = loop.run("x" * 20_000)
    second = loop.run("second user")

    assert first.status == second.status == "COMPLETED"
    assert oversized.status == "CONTEXT_LIMIT"
    assert len(model.calls) == 2
    assert model.calls[1][0] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first user"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second user"},
    ]


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        (ProviderError, "PROVIDER_ERROR"),
        (ProviderProtocolError, "PROVIDER_PROTOCOL_ERROR"),
    ],
)
def test_provider_exceptions_record_one_terminal_run_event(
    error_type: type[ProviderError], expected_status: str
) -> None:
    trace = RecordingTrace()
    model = FakeModelClient([error_type("provider failure")])

    result = AgentLoop(model, ToolRegistry({}), trace=trace).run("inspect")

    assert result.status == expected_status
    assert result.model_rounds == 0
    assert_one_run_event(trace, expected_status, False)


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


def test_run_task_passes_trace_and_keeps_jsonl_audit(tmp_path: Path) -> None:
    model = FakeModelClient([turn(content="done", total_tokens=4)])
    trace = RecordingTrace()
    config = ProviderConfig(
        api_key="private",
        base_url="https://provider.example/v1",
        model="test-model",
    )

    result = run_task(
        config,
        workspace=tmp_path,
        task="inspect",
        approve=lambda _argv, _cwd: False,
        client=model,
        environment={},
        trace=trace,
    )

    assert result.status == "COMPLETED"
    assert (tmp_path / ".code-operator" / "audit.jsonl").exists()
    assert trace.events == [("model", 1, 0, True), ("run", "COMPLETED", True)]


def test_run_task_closes_only_the_client_it_constructs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ClosingFakeModel(FakeModelClient):
        def __init__(self) -> None:
            super().__init__([turn(content="done")])
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    config = ProviderConfig(
        api_key="private",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    owned = ClosingFakeModel()
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: owned)

    owned_result = run_task(
        config,
        workspace=tmp_path,
        task="owned",
        approve=lambda _argv, _cwd: False,
        environment={},
    )

    assert owned_result.status == "COMPLETED"
    assert owned.close_calls == 1

    external = ClosingFakeModel()
    external_result = run_task(
        config,
        workspace=tmp_path,
        task="external",
        approve=lambda _argv, _cwd: False,
        client=external,
        environment={},
    )

    assert external_result.status == "COMPLETED"
    assert external.close_calls == 0


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

    trace = RecordingTrace()
    result = AgentLoop(
        model, ToolRegistry({"list_dir": repeated_handler}), trace=trace
    ).run("inspect")

    assert result.status == "REPEATED_CALL"
    assert result.model_rounds == 3
    assert result.tool_calls == 3
    assert len(model.calls) == 3
    assert_one_run_event(trace, "REPEATED_CALL", True)


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

    trace = RecordingTrace()
    result = AgentLoop(
        model, ToolRegistry({"read_file": failing_handler}), trace=trace
    ).run("inspect")

    assert result.status == "CONSECUTIVE_TOOL_FAILURES"
    assert result.model_rounds == 5
    assert result.tool_calls == 5
    assert len(model.calls) == 5
    assert_one_run_event(trace, "CONSECUTIVE_TOOL_FAILURES", True)


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

    trace = RecordingTrace()
    result = AgentLoop(
        model,
        ToolRegistry({}),
        max_tool_calls=1,
        trace=trace,
    ).run("inspect")

    assert result.status == "TOOL_CALL_LIMIT"
    assert result.tool_calls == 3
    assert paired_ids == ["limit-0", "limit-1", "limit-2"]
    assert_one_run_event(trace, "TOOL_CALL_LIMIT", True)


class InterruptingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *_: object) -> AssistantTurn:
        self.calls += 1
        raise KeyboardInterrupt


def test_ctrl_c_during_model_request_returns_user_aborted() -> None:
    model = InterruptingModel()
    trace = RecordingTrace()

    result = AgentLoop(model, ToolRegistry({}), trace=trace).run("inspect")

    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 0
    assert model.calls == 1
    assert_one_run_event(trace, "USER_ABORTED", False)


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

    trace = RecordingTrace()
    result = AgentLoop(
        model,
        ToolRegistry({"run_command": interrupting_handler}),
        trace=trace,
    ).run("run")

    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 1
    assert len(model.calls) == 1
    assert_one_run_event(trace, "USER_ABORTED", True)


@pytest.mark.parametrize("interrupt_index", [0, 1, 2])
def test_ctrl_c_mid_tool_turn_pairs_every_call_before_return(
    interrupt_index: int,
) -> None:
    calls = [
        ToolCall("one", "list_dir", "{}"),
        ToolCall("two", "list_dir", "{}"),
        ToolCall("three", "list_dir", "{}"),
    ]
    model = FakeModelClient(
        [
            turn(calls=calls, finish_reason="tool_calls", total_tokens=7),
            AssertionError("工具执行中止后不得再次请求模型"),
        ]
    )
    executed: list[str] = []

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        executed.append(tool_call_id)
        if tool_call_id == calls[interrupt_index].id:
            raise KeyboardInterrupt
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="ok",
            details={},
        )

    audit = RecordingAudit()
    trace = RecordingTrace()
    loop = AgentLoop(
        model,
        ToolRegistry({"list_dir": handler}),
        audit=audit,
        trace=trace,
    )

    result = loop.run("inspect")

    expected_codes = [None] * interrupt_index + ["USER_ABORTED"] + [
        "NOT_EXECUTED_AFTER_ABORT"
    ] * (2 - interrupt_index)
    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 1
    assert result.tool_calls == 3
    assert result.provider_total_tokens == 7
    assert len(model.calls) == 1
    assert executed == [call.id for call in calls[: interrupt_index + 1]]

    assistant_and_tools = loop._messages[-4:]
    assert assistant_and_tools[0] == turn(
        calls=calls, finish_reason="tool_calls", total_tokens=7
    ).to_replay_message()
    assert [message["tool_call_id"] for message in assistant_and_tools[1:]] == [
        "one",
        "two",
        "three",
    ]
    contents = [
        json.loads(str(message["content"]))
        for message in assistant_and_tools[1:]
    ]
    assert [content["error_code"] for content in contents] == expected_codes
    assert contents[interrupt_index] == {
        "ok": False,
        "error_code": "USER_ABORTED",
        "message": "工具执行被用户中止",
        "details": {},
    }
    for content in contents[interrupt_index + 1 :]:
        assert content == {
            "ok": False,
            "error_code": "NOT_EXECUTED_AFTER_ABORT",
            "message": "同轮较早的工具调用被中止，本调用未执行",
            "details": {},
        }
    assert [event[:3] for event in audit.events if event[0] == "tool"] == [
        ("tool", "one", "one"),
        ("tool", "two", "two"),
        ("tool", "three", "three"),
    ]
    assert [event[3] for event in audit.events if event[0] == "tool"] == expected_codes
    assert [event for event in audit.events if event[0] == "run"] == [
        ("run", "USER_ABORTED")
    ]
    assert [event[1:3] for event in trace.events if event[0] == "tool"] == [
        ("one", "one"),
        ("two", "two"),
        ("three", "three"),
    ]
    assert_one_run_event(trace, "USER_ABORTED", True)


@pytest.mark.parametrize("abort_index", [0, 1, 2])
def test_returned_user_abort_stops_consuming_and_pairs_remaining_calls(
    abort_index: int,
) -> None:
    calls = [
        ToolCall("one", "list_dir", "{}"),
        ToolCall("two", "list_dir", "{}"),
        ToolCall("three", "list_dir", "{}"),
    ]
    model = FakeModelClient(
        [
            turn(calls=calls, finish_reason="tool_calls", total_tokens=11),
            turn(content="next completed", total_tokens=13),
        ]
    )
    executed: list[str] = []
    abort_details = {"phase": "approval", "position": abort_index}

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        executed.append(tool_call_id)
        if tool_call_id == calls[abort_index].id:
            return ToolResult(
                tool_call_id=tool_call_id,
                name="list_dir",
                ok=False,
                error_code="USER_ABORTED",
                message="保留真实中止结果",
                details=abort_details,
            )
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message=f"done-{tool_call_id}",
            details={"value": tool_call_id},
        )

    audit = RecordingAudit()
    trace = RecordingTrace()
    loop = AgentLoop(
        model,
        ToolRegistry({"list_dir": handler}),
        audit=audit,
        trace=trace,
    )

    result = loop.run("inspect")

    assert result.status == "USER_ABORTED"
    assert result.model_rounds == 1
    assert result.tool_calls == 3
    assert result.provider_total_tokens == 11
    assert len(model.calls) == 1
    assert executed == [call.id for call in calls[: abort_index + 1]]

    assistant_and_tools = loop._messages[-4:]
    assert assistant_and_tools[0] == turn(
        calls=calls, finish_reason="tool_calls", total_tokens=11
    ).to_replay_message()
    assert [message["tool_call_id"] for message in assistant_and_tools[1:]] == [
        "one",
        "two",
        "three",
    ]
    contents = [
        json.loads(str(message["content"]))
        for message in assistant_and_tools[1:]
    ]
    for index, content in enumerate(contents[:abort_index]):
        assert content == {
            "ok": True,
            "error_code": None,
            "message": f"done-{calls[index].id}",
            "details": {"value": calls[index].id},
        }
    assert contents[abort_index] == {
        "ok": False,
        "error_code": "USER_ABORTED",
        "message": "保留真实中止结果",
        "details": abort_details,
    }
    for content in contents[abort_index + 1 :]:
        assert content == {
            "ok": False,
            "error_code": "NOT_EXECUTED_AFTER_ABORT",
            "message": "同轮较早的工具调用被中止，本调用未执行",
            "details": {},
        }

    expected_codes = [None] * abort_index + ["USER_ABORTED"] + [
        "NOT_EXECUTED_AFTER_ABORT"
    ] * (2 - abort_index)
    assert [event[:3] for event in audit.events if event[0] == "tool"] == [
        ("tool", "one", "one"),
        ("tool", "two", "two"),
        ("tool", "three", "three"),
    ]
    assert [event[3] for event in audit.events if event[0] == "tool"] == expected_codes
    assert [event for event in audit.events if event[0] == "run"] == [
        ("run", "USER_ABORTED")
    ]
    assert [event[1:3] for event in trace.events if event[0] == "tool"] == [
        ("one", "one"),
        ("two", "two"),
        ("three", "three"),
    ]
    assert_one_run_event(trace, "USER_ABORTED", True)

    recovered = loop.run("continue")

    assert recovered.status == "COMPLETED"
    assert len(model.calls) == 2
    assert [message["role"] for message in model.calls[1][0]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
        "tool",
        "user",
    ]
    assert [
        message["tool_call_id"]
        for message in model.calls[1][0]
        if message["role"] == "tool"
    ] == ["one", "two", "three"]


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

    trace = RecordingTrace()
    result = AgentLoop(
        model, ToolRegistry({"read_file": handler}), trace=trace
    ).run("inspect")

    assert result.status == "MODEL_ROUND_LIMIT"
    assert result.model_rounds == 16
    assert len(model.calls) == 16
    assert_one_run_event(trace, "MODEL_ROUND_LIMIT", True)


def test_content_filter_is_a_terminal_non_success_state() -> None:
    model = FakeModelClient(
        [turn(content="partial", finish_reason="content_filter")]
    )

    trace = RecordingTrace()
    result = AgentLoop(model, ToolRegistry({}), trace=trace).run("task")

    assert result.status == "CONTENT_FILTERED"
    assert result.final_text == "partial"
    assert_one_run_event(trace, "CONTENT_FILTERED", True)


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
