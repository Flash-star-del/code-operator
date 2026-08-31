from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from code_operator.models import ToolCall, ToolResult
from code_operator.tools.registry import ToolProtocolError, ToolRegistry


EXPECTED_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出工作区内目录，忽略内部状态目录并限制深度和条目数。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1, "default": "."},
                    "max_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                        "default": 2,
                    },
                    "max_entries": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 200,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内 UTF-8 文本；完整读取才授权后续覆盖。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                    },
                    "end_line": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "null"},
                        ],
                        "default": None,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "在工作区文本文件中执行区分大小写的字面量搜索。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1, "default": "."},
                    "file_pattern": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ],
                        "default": None,
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "新建文件，或在完整读取且内容未变化后原子覆盖已有文件。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "在已完整读取的文件中唯一替换一段文本并返回差异。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "以参数数组、固定工作目录和超时运行本地命令，不经过 Shell。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 64,
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "default": 30,
                    },
                },
                "required": ["argv"],
            },
        },
    },
]


def call(name: str, arguments: object, *, call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments_raw=json.dumps(arguments, ensure_ascii=False),
    )


def result_handler(
    captured: list[dict[str, object]], name: str
) -> Callable[..., ToolResult]:
    def handler(*, tool_call_id: str, **arguments: object) -> ToolResult:
        captured.append(arguments)
        return ToolResult(
            tool_call_id=tool_call_id,
            name=name,
            ok=True,
            error_code=None,
            message="ok",
            details={"arguments": arguments},
        )

    return handler


def test_six_tool_schemas_are_exact_and_stable() -> None:
    registry = ToolRegistry({})

    assert registry.tool_schemas() == EXPECTED_SCHEMAS
    assert registry.tool_schemas() == EXPECTED_SCHEMAS


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("list_dir", {}, {"path": ".", "max_depth": 2, "max_entries": 200}),
        (
            "read_file",
            {"path": "a.py"},
            {"path": "a.py", "start_line": 1, "end_line": None},
        ),
        (
            "grep",
            {"query": "needle"},
            {
                "query": "needle",
                "path": ".",
                "file_pattern": None,
                "max_results": 100,
            },
        ),
        ("run_command", {"argv": ["python", "-V"]}, {"argv": ["python", "-V"], "timeout_seconds": 30}),
    ],
)
def test_registry_applies_defaults_before_calling_handler(
    name: str, arguments: object, expected: dict[str, object]
) -> None:
    captured: list[dict[str, object]] = []
    registry = ToolRegistry({name: result_handler(captured, name)})

    [result] = registry.execute_calls([call(name, arguments)])

    assert result.ok is True
    assert captured == [expected]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("read_file", {}),
        ("read_file", {"path": "a.py", "extra": 1}),
        ("read_file", {"path": None}),
        ("read_file", {"path": ""}),
        ("read_file", {"path": "a.py", "start_line": True}),
        ("read_file", {"path": "a.py", "start_line": 1.5}),
        ("read_file", {"path": "a.py", "start_line": 2, "end_line": 1}),
        ("list_dir", {"max_depth": -1}),
        ("list_dir", {"max_depth": 6}),
        ("list_dir", {"max_entries": 0}),
        ("grep", {"query": "x", "file_pattern": ""}),
        ("grep", {"query": "x", "max_results": 201}),
        ("write_file", {"path": "a.py"}),
        ("write_file", {"path": "a.py", "content": None}),
        ("edit_file", {"path": "a.py", "old_text": "x", "new_text": "x"}),
        ("run_command", {"argv": []}),
        ("run_command", {"argv": ["python", 3]}),
        ("run_command", {"argv": ["python"], "timeout_seconds": False}),
        ("run_command", {"argv": ["python"], "timeout_seconds": 121}),
    ],
)
def test_invalid_arguments_return_one_matched_failure(
    name: str, arguments: object
) -> None:
    registry = ToolRegistry({})

    [result] = registry.execute_calls([call(name, arguments)])

    assert result.tool_call_id == "call-1"
    assert result.name == name
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENTS"


def test_bad_json_and_unknown_tool_return_matched_failures() -> None:
    registry = ToolRegistry({})
    calls = [
        ToolCall(id="bad-json", name="read_file", arguments_raw="{"),
        call("not_a_tool", {}, call_id="unknown"),
    ]

    results = registry.execute_calls(calls)

    assert [(item.tool_call_id, item.error_code) for item in results] == [
        ("bad-json", "INVALID_ARGUMENTS"),
        ("unknown", "UNKNOWN_TOOL"),
    ]


def test_deeply_nested_json_returns_matched_invalid_arguments() -> None:
    deeply_nested = "[" * 2_000 + "0" + "]" * 2_000
    registry = ToolRegistry({})

    [result] = registry.execute_calls(
        [ToolCall("deep", "list_dir", deeply_nested)]
    )

    assert result == ToolResult(
        tool_call_id="deep",
        name="list_dir",
        ok=False,
        error_code="INVALID_ARGUMENTS",
        message="arguments 必须是合法 JSON 对象",
        details={},
    )
    assert json.loads(result.to_message_content())["error_code"] == "INVALID_ARGUMENTS"


def test_unserializable_handler_result_becomes_safe_execution_failure() -> None:
    private_value = object()

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="unsafe",
            details={"value": private_value},
        )

    registry = ToolRegistry({"list_dir": handler})

    [result] = registry.execute_calls([call("list_dir", {}, call_id="unsafe")])

    assert result == ToolResult(
        tool_call_id="unsafe",
        name="list_dir",
        ok=False,
        error_code="TOOL_EXECUTION_ERROR",
        message="工具返回了无效结果",
        details={},
    )
    content = result.to_message_content()
    assert "object at" not in content
    assert json.loads(content) == {
        "ok": False,
        "error_code": "TOOL_EXECUTION_ERROR",
        "message": "工具返回了无效结果",
        "details": {},
    }


@pytest.mark.parametrize(
    "calls",
    [
        [call("list_dir", {}, call_id="")],
        [call("list_dir", {}, call_id="same"), call("read_file", {"path": "a"}, call_id="same")],
    ],
)
def test_empty_or_duplicate_call_ids_are_protocol_errors(calls: list[ToolCall]) -> None:
    registry = ToolRegistry({})

    with pytest.raises(ToolProtocolError, match="tool_call_id"):
        registry.execute_calls(calls)


def test_iter_results_validates_all_ids_before_yielding_in_order() -> None:
    executed: list[str] = []

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        executed.append(tool_call_id)
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="ok",
            details={},
        )

    registry = ToolRegistry({"list_dir": handler})
    results = list(
        registry.iter_results(
            [
                call("list_dir", {}, call_id="one"),
                call("list_dir", {}, call_id="two"),
            ]
        )
    )

    assert executed == ["one", "two"]
    assert [item.tool_call_id for item in results] == ["one", "two"]


@pytest.mark.parametrize(
    "invalid_calls",
    [
        [
            call("list_dir", {}, call_id="one"),
            call("list_dir", {}, call_id=""),
        ],
        [
            call("list_dir", {}, call_id="same"),
            call("list_dir", {}, call_id="same"),
        ],
    ],
)
def test_iter_results_rejects_late_bad_id_before_any_handler_runs(
    invalid_calls: list[ToolCall],
) -> None:
    executed: list[str] = []

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        executed.append(tool_call_id)
        raise AssertionError("整体 ID 校验前不得执行 handler")

    registry = ToolRegistry({"list_dir": handler})

    with pytest.raises(ToolProtocolError, match="tool_call_id"):
        list(registry.iter_results(invalid_calls))

    assert executed == []


def test_tool_result_serializes_once_with_stable_content() -> None:
    result = ToolResult(
        tool_call_id="call-1",
        name="read_file",
        ok=False,
        error_code="PATH_DENIED",
        message="拒绝访问",
        details={"path": ".env"},
    )

    content = result.to_message_content()

    assert content == (
        '{"details":{"path":".env"},"error_code":"PATH_DENIED",'
        '"message":"拒绝访问","ok":false}'
    )
    assert json.loads(content) == {
        "ok": False,
        "error_code": "PATH_DENIED",
        "message": "拒绝访问",
        "details": {"path": ".env"},
    }
    assert result.to_message_content() == content
