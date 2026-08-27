from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Protocol

from code_operator.client import ProviderError, ProviderProtocolError
from code_operator.models import AssistantTurn, RunResult, ToolResult
from code_operator.prompts import SYSTEM_PROMPT
from code_operator.tools.registry import ToolProtocolError, ToolRegistry


class ModelLike(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> AssistantTurn: ...


def _estimated_tokens(messages: Sequence[Mapping[str, object]]) -> int:
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(serialized.encode("utf-8")) / 3)


class AgentLoop:
    def __init__(
        self,
        client: ModelLike,
        registry: ToolRegistry,
        *,
        max_model_rounds: int = 16,
        max_tool_calls: int = 32,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._client = client
        self._registry = registry
        self._max_model_rounds = max_model_rounds
        self._max_tool_calls = max_tool_calls
        self._system_prompt = system_prompt

    def run(self, user_task: str) -> RunResult:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_task},
        ]
        model_rounds = 0
        tool_call_count = 0
        provider_tokens = 0
        provider_usage_complete = True

        def result(status: str, final_text: str = "") -> RunResult:
            return RunResult(
                status=status,
                final_text=final_text,
                model_rounds=model_rounds,
                tool_calls=tool_call_count,
                provider_total_tokens=(
                    provider_tokens if provider_usage_complete else None
                ),
                estimated_context_tokens=_estimated_tokens(messages),
            )

        for _ in range(self._max_model_rounds):
            try:
                turn = self._client.complete(messages, self._registry.tool_schemas())
            except ProviderProtocolError:
                return result("PROVIDER_PROTOCOL_ERROR")
            except ProviderError:
                return result("PROVIDER_ERROR")
            model_rounds += 1
            if turn.usage is None or turn.usage.total_tokens is None:
                provider_usage_complete = False
            else:
                provider_tokens += turn.usage.total_tokens
            messages.append(turn.to_replay_message())

            if turn.tool_calls:
                if tool_call_count + len(turn.tool_calls) > self._max_tool_calls:
                    failures = [
                        ToolResult(
                            tool_call_id=call.id,
                            name=call.name,
                            ok=False,
                            error_code="TOOL_CALL_LIMIT",
                            message="工具调用总数超过上限",
                            details={},
                        )
                        for call in turn.tool_calls
                    ]
                else:
                    try:
                        failures = self._registry.execute_calls(turn.tool_calls)
                    except ToolProtocolError:
                        return result("PROVIDER_PROTOCOL_ERROR")
                tool_call_count += len(turn.tool_calls)
                for tool_result in failures:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_result.tool_call_id,
                            "name": tool_result.name,
                            "content": tool_result.to_message_content(),
                        }
                    )
                    if tool_result.error_code == "USER_ABORTED":
                        return result("USER_ABORTED")
                if any(item.error_code == "TOOL_CALL_LIMIT" for item in failures):
                    return result("TOOL_CALL_LIMIT")
                continue

            if turn.finish_reason == "length":
                return result("OUTPUT_TRUNCATED", turn.content or "")
            if turn.finish_reason == "content_filter":
                return result("CONTENT_FILTERED", turn.content or "")
            if turn.content is None or not turn.content.strip():
                return result("EMPTY_RESPONSE")
            return result("COMPLETED", turn.content.strip())
        return result("MODEL_ROUND_LIMIT")
