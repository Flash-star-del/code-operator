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
        prefix, rounds = self._split_complete_rounds(copied)
        kept_rounds = list(rounds)
        trimmed_rounds = 0

        prepared = prefix + [item for group in kept_rounds for item in group]
        estimated = self.estimate_tokens(prepared, tools)
        while estimated > self.input_budget and len(kept_rounds) > 1:
            kept_rounds.pop(0)
            trimmed_rounds += 1
            prepared = prefix + [item for group in kept_rounds for item in group]
            estimated = self.estimate_tokens(prepared, tools)

        if estimated > self.input_budget:
            raise ContextLimitError("系统提示、当前任务和最新工具回合无法放入上下文")
        return PreparedContext(
            messages=prepared,
            estimated_tokens=estimated,
            trimmed_rounds=trimmed_rounds,
        )

    def _split_complete_rounds(
        self, messages: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], list[list[dict[str, object]]]]:
        if len(messages) < 2:
            return messages, []
        if messages[0].get("role") != "system" or messages[1].get("role") != "user":
            raise ContextLimitError("上下文必须以 system 和当前 user 任务开始")

        prefix = messages[:2]
        rounds: list[list[dict[str, object]]] = []
        index = 2
        while index < len(messages):
            assistant = messages[index]
            if assistant.get("role") != "assistant":
                raise ContextLimitError("工具结果与 assistant 调用无法配对")
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
                if not isinstance(tool_call_id, str):
                    raise ContextLimitError("工具结果缺少合法配对 ID")
                actual_ids.append(tool_call_id)
                group.append(tool_message)
                index += 1
            if actual_ids != expected_ids:
                raise ContextLimitError("工具调用与结果必须按 ID 完整配对")
            rounds.append(group)
        return prefix, rounds
