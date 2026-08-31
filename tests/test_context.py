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


def test_prepare_accepts_multiple_complete_user_turns_without_trimming() -> None:
    messages = [
        message("system", "system"),
        message("user", "first"),
        message("assistant", "first done"),
        message("user", "second"),
    ]
    manager = ContextManager(context_window=32_000, max_output_tokens=8_000)

    prepared = manager.prepare(messages, [])

    assert prepared.messages == messages
    assert prepared.trimmed_turns == 0
    assert prepared.trimmed_rounds == 0


def test_trim_removes_oldest_complete_user_turn_before_current_groups() -> None:
    system = message("system", "system")
    old_turn = [
        message("user", "old task"),
        message("assistant", "old answer" * 100),
    ]
    current_turn = [
        message("user", "current task"),
        tool_call_message("current"),
        tool_result_message("current", "current result"),
    ]
    expected = [system, *current_turn]
    sizing = ContextManager(context_window=10_000, max_output_tokens=10)
    minimum_estimate = sizing.estimate_tokens(expected, [])
    manager = ContextManager(
        context_window=minimum_estimate + 10,
        max_output_tokens=10,
    )

    prepared = manager.prepare([system, *old_turn, *current_turn], [])

    assert prepared.messages == expected
    assert prepared.trimmed_turns == 1
    assert prepared.trimmed_rounds == 0


def test_trim_current_turn_removes_oldest_complete_assistant_group() -> None:
    prefix = [message("system", "system"), message("user", "current task")]
    old_group = [
        tool_call_message("old"),
        tool_result_message("old", "old result" * 100),
    ]
    latest_group = [
        tool_call_message("latest"),
        tool_result_message("latest", "latest result"),
    ]
    expected = prefix + latest_group
    sizing = ContextManager(context_window=10_000, max_output_tokens=10)
    minimum_estimate = sizing.estimate_tokens(expected, [])
    manager = ContextManager(
        context_window=minimum_estimate + 10,
        max_output_tokens=10,
    )

    prepared = manager.prepare(prefix + old_group + latest_group, [])

    assert prepared.messages == expected
    assert prepared.trimmed_turns == 0
    assert prepared.trimmed_rounds == 1


@pytest.mark.parametrize(
    "messages",
    [
        [
            message("system", "system"),
            message("assistant", "before first user"),
            message("user", "task"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            message("system", "second system"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_result_message("before-assistant"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_call_message("expected"),
            tool_result_message("different"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            message(
                "assistant",
                None,
                tool_calls=[
                    {
                        "id": "duplicate",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                    {
                        "id": "duplicate",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    },
                ],
            ),
            tool_result_message("duplicate"),
            tool_result_message("duplicate"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_call_message("one"),
            tool_result_message("one"),
            tool_result_message("one"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_call_message("missing"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_call_message("interrupted-by-user"),
            message("user", "next"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            tool_call_message("interrupted-by-assistant"),
            message("assistant", "next"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            message("assistant", "ordinary answer"),
            tool_result_message("unexpected"),
        ],
        [
            message("system", "system"),
            message("user", "task"),
            message("developer", "unknown role"),
        ],
    ],
    ids=[
        "assistant-before-first-user",
        "second-system",
        "tool-before-assistant",
        "tool-result-id-mismatch",
        "duplicate-tool-call-id",
        "duplicate-extra-tool-result",
        "missing-tool-result",
        "tool-call-interrupted-by-user",
        "tool-call-interrupted-by-assistant",
        "ordinary-assistant-followed-by-tool",
        "unknown-role",
    ],
)
def test_prepare_rejects_malformed_history_before_trimming(
    messages: list[dict[str, object]],
) -> None:
    manager = ContextManager(context_window=32_000, max_output_tokens=8_000)

    with pytest.raises(ContextLimitError):
        manager.prepare(messages, [])
