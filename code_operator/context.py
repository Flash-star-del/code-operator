from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class ContextLimitError(RuntimeError):
    """Raised when the required prompt and active tool round cannot fit."""


@dataclass(frozen=True)
class PreparedContext:
    messages: list[dict[str, object]]
    estimated_tokens: int
    trimmed_rounds: int
    trimmed_turns: int = 0


class ContextManager:
    def __init__(self, *, context_window: int, max_output_tokens: int) -> None:
        if context_window <= 0 or max_output_tokens <= 0:
            raise ValueError("上下文窗口和输出预留必须为正整数")
        if max_output_tokens >= context_window:
            raise ValueError("输出预留必须小于上下文窗口")
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens

    @property
    def input_budget(self) -> int:
        return self.context_window - self.max_output_tokens

    def estimate_tokens(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> int:
        serialized = json.dumps(
            {"messages": list(messages), "tools": list(tools)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return math.ceil(len(serialized.encode("utf-8")) / 3)

    def prepare(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> PreparedContext:
        copied = [dict(message) for message in messages]
        system, turns = self._parse_complete_turns(copied)
        kept_turns = list(turns)
        trimmed_turns = 0
        trimmed_rounds = 0

        prepared = self._flatten(system, kept_turns)
        estimated = self.estimate_tokens(prepared, tools)
        while estimated > self.input_budget and len(kept_turns) > 1:
            kept_turns.pop(0)
            trimmed_turns += 1
            prepared = self._flatten(system, kept_turns)
            estimated = self.estimate_tokens(prepared, tools)

        current_turn = kept_turns[0]
        while estimated > self.input_budget and len(current_turn) > 2:
            current_turn.pop(1)
            trimmed_rounds += 1
            prepared = self._flatten(system, kept_turns)
            estimated = self.estimate_tokens(prepared, tools)

        if estimated > self.input_budget:
            raise ContextLimitError("系统提示、当前任务和最新工具回合无法放入上下文")
        return PreparedContext(
            messages=prepared,
            estimated_tokens=estimated,
            trimmed_rounds=trimmed_rounds,
            trimmed_turns=trimmed_turns,
        )

    def _parse_complete_turns(
        self, messages: list[dict[str, object]]
    ) -> tuple[dict[str, object], list[list[list[dict[str, object]]]]]:
        if len(messages) < 2:
            raise ContextLimitError("上下文必须以 system 和当前 user 任务开始")
        if messages[0].get("role") != "system" or messages[1].get("role") != "user":
            raise ContextLimitError("上下文必须以 system 和当前 user 任务开始")

        system = messages[0]
        turns: list[list[list[dict[str, object]]]] = []
        index = 1
        while index < len(messages):
            user = messages[index]
            if user.get("role") != "user":
                raise ContextLimitError("每个上下文轮次必须以 user 消息开始")
            turn = [[user]]
            index += 1
            while index < len(messages) and messages[index].get("role") != "user":
                if messages[index].get("role") != "assistant":
                    raise ContextLimitError("工具结果与 assistant 调用无法配对")
                group, index = self._parse_assistant_group(messages, index)
                turn.append(group)
            turns.append(turn)
        return system, turns

    def _parse_assistant_group(
        self,
        messages: list[dict[str, object]],
        index: int,
    ) -> tuple[list[dict[str, object]], int]:
        assistant = messages[index]
        group = [assistant]
        index += 1
        raw_calls = assistant.get("tool_calls")
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ContextLimitError("assistant 工具调用结构无效")

        expected_ids: list[str] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                raise ContextLimitError("assistant 工具调用结构无效")
            call_id = raw_call.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise ContextLimitError("assistant 工具调用缺少合法 ID")
            expected_ids.append(call_id)
        if len(expected_ids) != len(set(expected_ids)):
            raise ContextLimitError("assistant 工具调用 ID 不唯一")

        actual_ids: list[str] = []
        while index < len(messages) and messages[index].get("role") == "tool":
            tool_message = messages[index]
            tool_call_id = tool_message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ContextLimitError("工具结果缺少合法配对 ID")
            actual_ids.append(tool_call_id)
            group.append(tool_message)
            index += 1
        if actual_ids != expected_ids:
            raise ContextLimitError("工具调用与结果必须按 ID 完整配对")
        return group, index

    @staticmethod
    def _flatten(
        system: dict[str, object],
        turns: Sequence[Sequence[Sequence[dict[str, object]]]],
    ) -> list[dict[str, object]]:
        return [
            system,
            *(
                item
                for turn in turns
                for group in turn
                for item in group
            ),
        ]
