from __future__ import annotations

import json
from pathlib import Path

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
