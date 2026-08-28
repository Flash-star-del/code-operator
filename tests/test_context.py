from __future__ import annotations

import json
import math

import pytest

from code_operator.context import ContextLimitError, ContextManager


def message(role: str, content: str | None, **extra: object) -> dict[str, object]:
    return {"role": role, "content": content, **extra}


def tool_call_message(call_id: str, arguments: str = "{}") -> dict[str, object]:
    return message(
        "assistant",
        None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": arguments},
            }
        ],
    )


def tool_result_message(call_id: str, content: str = "ok") -> dict[str, object]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": "read_file",
        "content": content,
    }


def test_estimate_counts_serialized_messages_and_tool_schemas() -> None:
    messages = [message("user", "hello")]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo",
                "description": "x" * 90,
                "parameters": {"type": "object"},
            },
        }
    ]
    manager = ContextManager(context_window=32_000, max_output_tokens=8_000)
    serialized = json.dumps(
        {"messages": messages, "tools": tools},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    estimated = manager.estimate_tokens(messages, tools)

    assert estimated == math.ceil(len(serialized.encode("utf-8")) / 3)
    assert estimated > manager.estimate_tokens(messages, [])


def test_output_reservation_reduces_available_input_budget() -> None:
    messages = [message("system", "s"), message("user", "x" * 120)]
    manager = ContextManager(context_window=100, max_output_tokens=80)

    with pytest.raises(ContextLimitError):
        manager.prepare(messages, [])


def test_trim_removes_oldest_complete_round_without_splitting_tool_pair() -> None:
    prefix = [message("system", "system"), message("user", "task")]
    old_round = [
        message(
            "assistant",
            None,
            tool_calls=[
                {
                    "id": "old-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
                {
                    "id": "old-2",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
            ],
        ),
        tool_result_message("old-1", "old-a" * 50),
        {
            **tool_result_message("old-2", "old-b" * 50),
            "name": "list_dir",
        },
    ]
    current_round = [
        tool_call_message("current"),
        tool_result_message("current", "current-result"),
    ]
    sizing = ContextManager(context_window=10_000, max_output_tokens=10)
    minimum_estimate = sizing.estimate_tokens(prefix + current_round, [])
    manager = ContextManager(
        context_window=minimum_estimate + 10,
        max_output_tokens=10,
    )

    prepared = manager.prepare(prefix + old_round + current_round, [])

    assert prepared.messages == prefix + current_round
    assert prepared.trimmed_rounds == 1
    serialized = json.dumps(prepared.messages, ensure_ascii=False)
    assert "old-1" not in serialized
    assert "old-2" not in serialized
    assert "current" in serialized


def test_context_limit_preserves_current_execution_chain_or_stops() -> None:
    messages = [
        message("system", "system"),
        message("user", "task"),
        tool_call_message("current"),
        tool_result_message("current", "x" * 300),
    ]
    manager = ContextManager(context_window=100, max_output_tokens=50)

    with pytest.raises(ContextLimitError):
        manager.prepare(messages, [])


def test_prepare_rejects_orphan_tool_result_instead_of_splitting_history() -> None:
    messages = [
        message("system", "system"),
        message("user", "task"),
        tool_result_message("orphan"),
    ]
    manager = ContextManager(context_window=1_000, max_output_tokens=100)

    with pytest.raises(ContextLimitError, match="配对"):
        manager.prepare(messages, [])
