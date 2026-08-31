from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator, Mapping, Sequence

from code_operator.models import ToolCall, ToolResult


ToolHandler = Callable[..., ToolResult]


class ToolProtocolError(ValueError):
    """Raised when calls cannot be paired with unique provider IDs."""


_TOOL_SCHEMAS: list[dict[str, object]] = [
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


def _failure(call: ToolCall, code: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        name=call.name,
        ok=False,
        error_code=code,
        message=message,
        details={},
    )


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _matches(value: object, schema: Mapping[str, object]) -> bool:
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        return any(
            isinstance(option, dict) and _matches(value, option)
            for option in alternatives
        )

    expected_type = schema.get("type")
    if expected_type == "null":
        return value is None
    if expected_type == "string":
        if not isinstance(value, str):
            return False
        minimum_length = schema.get("minLength")
        return not isinstance(minimum_length, int) or len(value) >= minimum_length
    if expected_type == "integer":
        if not _is_integer(value):
            return False
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            return False
        if isinstance(maximum, int) and value > maximum:
            return False
        return True
    if expected_type == "array":
        if not isinstance(value, list):
            return False
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            return False
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            return False
        item_schema = schema.get("items")
        return not isinstance(item_schema, dict) or all(
            _matches(item, item_schema) for item in value
        )
    return False


def _validated_arguments(
    call: ToolCall, parameters: Mapping[str, object]
) -> dict[str, object] | ToolResult:
    try:
        decoded = json.loads(call.arguments_raw)
    except (json.JSONDecodeError, RecursionError, TypeError):
        return _failure(call, "INVALID_ARGUMENTS", "arguments 必须是合法 JSON 对象")
    if not isinstance(decoded, dict):
        return _failure(call, "INVALID_ARGUMENTS", "arguments 必须是 JSON 对象")

    properties = parameters.get("properties")
    required = parameters.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return _failure(call, "INVALID_ARGUMENTS", "工具参数定义无效")
    if any(name not in decoded for name in required):
        return _failure(call, "INVALID_ARGUMENTS", "缺少必要工具参数")
    if any(name not in properties for name in decoded):
        return _failure(call, "INVALID_ARGUMENTS", "包含未允许的额外参数")

    normalized: dict[str, object] = {}
    for name, property_schema in properties.items():
        if name in decoded:
            value = decoded[name]
        elif isinstance(property_schema, dict) and "default" in property_schema:
            value = copy.deepcopy(property_schema["default"])
        else:
            continue
        if not isinstance(property_schema, dict) or not _matches(value, property_schema):
            return _failure(call, "INVALID_ARGUMENTS", f"工具参数 {name} 类型或范围无效")
        normalized[name] = value

    if call.name == "read_file":
        start_line = normalized.get("start_line")
        end_line = normalized.get("end_line")
        if isinstance(end_line, int) and isinstance(start_line, int) and end_line < start_line:
            return _failure(call, "INVALID_ARGUMENTS", "end_line 不得小于 start_line")
    if call.name == "edit_file" and normalized.get("old_text") == normalized.get("new_text"):
        return _failure(call, "INVALID_ARGUMENTS", "old_text 与 new_text 不得相同")
    return normalized


class ToolRegistry:
    def __init__(self, handlers: Mapping[str, ToolHandler]) -> None:
        self._handlers = dict(handlers)
        self._schema_by_name = {
            str(schema["function"]["name"]): schema
            for schema in _TOOL_SCHEMAS
            if isinstance(schema.get("function"), dict)
        }

    def tool_schemas(self) -> list[dict[str, object]]:
        return copy.deepcopy(_TOOL_SCHEMAS)

    @staticmethod
    def _validate_call_ids(calls: Sequence[ToolCall]) -> None:
        ids = [call.id for call in calls]
        if any(not isinstance(call_id, str) or not call_id.strip() for call_id in ids):
            raise ToolProtocolError("tool_call_id 不能为空")
        if len(ids) != len(set(ids)):
            raise ToolProtocolError("tool_call_id 在同一轮中必须唯一")

    def iter_results(self, calls: Sequence[ToolCall]) -> Iterator[ToolResult]:
        self._validate_call_ids(calls)
        return (self._execute(call) for call in calls)

    def execute_calls(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
        return list(self.iter_results(calls))

    def _execute(self, call: ToolCall) -> ToolResult:
        schema = self._schema_by_name.get(call.name)
        if schema is None:
            return _failure(call, "UNKNOWN_TOOL", "未知工具")
        function = schema.get("function")
        parameters = function.get("parameters") if isinstance(function, dict) else None
        if not isinstance(parameters, dict):
            return _failure(call, "INVALID_ARGUMENTS", "工具参数定义无效")
        arguments = _validated_arguments(call, parameters)
        if isinstance(arguments, ToolResult):
            return arguments

        handler = self._handlers.get(call.name)
        if handler is None:
            return _failure(call, "TOOL_NOT_AVAILABLE", "工具尚未配置执行器")
        try:
            result = handler(tool_call_id=call.id, **arguments)
        except Exception:
            return _failure(call, "TOOL_EXECUTION_ERROR", "工具执行失败")
        if not isinstance(result, ToolResult):
            return _failure(call, "TOOL_EXECUTION_ERROR", "工具返回了无效结果")
        if result.tool_call_id != call.id or result.name != call.name:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                ok=result.ok,
                error_code=result.error_code,
                message=result.message,
                details=result.details,
            )
        try:
            result.to_message_content()
        except (RecursionError, TypeError, ValueError):
            return _failure(call, "TOOL_EXECUTION_ERROR", "工具返回了无效结果")
        return result
