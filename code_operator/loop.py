from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from code_operator.client import ProviderError, ProviderProtocolError
from code_operator.config import DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_OUTPUT_TOKENS
from code_operator.context import ContextLimitError, ContextManager
from code_operator.models import AssistantTurn, RunResult, ToolCall, ToolResult
from code_operator.prompts import SYSTEM_PROMPT
from code_operator.tools.registry import ToolProtocolError, ToolRegistry


MAX_CONSECUTIVE_TOOL_FAILURES = 5
MAX_REPEATED_CALL_RESULTS = 3


class ModelLike(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> AssistantTurn: ...


class AuditLike(Protocol):
    def record_tool(self, call: ToolCall, result: ToolResult) -> None: ...

    def record_run(self, result: RunResult) -> None: ...


def _call_result_signature(
    call_name: str,
    arguments_raw: str,
    result_content: str,
) -> tuple[str, str, str]:
    try:
        arguments = json.loads(arguments_raw)
        normalized_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, TypeError):
        normalized_arguments = arguments_raw
    return call_name, normalized_arguments, result_content


class AgentLoop:
    def __init__(
        self,
        client: ModelLike,
        registry: ToolRegistry,
        *,
        max_model_rounds: int = 16,
        max_tool_calls: int = 32,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        system_prompt: str = SYSTEM_PROMPT,
        audit: AuditLike | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._max_model_rounds = max_model_rounds
        self._max_tool_calls = max_tool_calls
        self._system_prompt = system_prompt
        self._context_manager = ContextManager(
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        )
        self._audit = audit

    def run(self, user_task: str) -> RunResult:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_task},
        ]
        model_rounds = 0
        tool_call_count = 0
        provider_tokens = 0
        provider_usage_complete = True
        consecutive_tool_failures = 0
        previous_call_result: tuple[str, str, str] | None = None
        repeated_call_results = 0
        tool_schemas = self._registry.tool_schemas()

        def result(status: str, final_text: str = "") -> RunResult:
            run_result = RunResult(
                status=status,
                final_text=final_text,
                model_rounds=model_rounds,
                tool_calls=tool_call_count,
                provider_total_tokens=(
                    provider_tokens
                    if provider_usage_complete and model_rounds > 0
                    else None
                ),
                estimated_context_tokens=self._context_manager.estimate_tokens(
                    messages, tool_schemas
                ),
            )
            if self._audit is not None:
                try:
                    self._audit.record_run(run_result)
                except Exception:
                    pass
            return run_result

        for _ in range(self._max_model_rounds):
            try:
                prepared = self._context_manager.prepare(messages, tool_schemas)
            except ContextLimitError:
                return result("CONTEXT_LIMIT")
            messages = prepared.messages
            try:
                turn = self._client.complete(messages, tool_schemas)
            except KeyboardInterrupt:
                return result("USER_ABORTED")
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
                    except KeyboardInterrupt:
                        return result("USER_ABORTED")
                    except ToolProtocolError:
                        return result("PROVIDER_PROTOCOL_ERROR")
                tool_call_count += len(turn.tool_calls)
                repeated_limit_reached = False
                failure_limit_reached = False
                for call, tool_result in zip(turn.tool_calls, failures, strict=True):
                    result_content = tool_result.to_message_content()
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_result.tool_call_id,
                            "name": tool_result.name,
                            "content": result_content,
                        }
                    )
                    if self._audit is not None:
                        try:
                            self._audit.record_tool(call, tool_result)
                        except Exception:
                            pass
                    if tool_result.error_code == "USER_ABORTED":
                        return result("USER_ABORTED")
                    if tool_result.ok:
                        consecutive_tool_failures = 0
                    else:
                        consecutive_tool_failures += 1
                        if (
                            consecutive_tool_failures
                            >= MAX_CONSECUTIVE_TOOL_FAILURES
                        ):
                            failure_limit_reached = True

                    signature = _call_result_signature(
                        call.name,
                        call.arguments_raw,
                        result_content,
                    )
                    if signature == previous_call_result:
                        repeated_call_results += 1
                    else:
                        previous_call_result = signature
                        repeated_call_results = 1
                    if repeated_call_results >= MAX_REPEATED_CALL_RESULTS:
                        repeated_limit_reached = True
                if any(item.error_code == "TOOL_CALL_LIMIT" for item in failures):
                    return result("TOOL_CALL_LIMIT")
                if repeated_limit_reached:
                    return result("REPEATED_CALL")
                if failure_limit_reached:
                    return result("CONSECUTIVE_TOOL_FAILURES")
                continue

            if turn.finish_reason == "length":
                return result("OUTPUT_TRUNCATED", turn.content or "")
            if turn.finish_reason == "content_filter":
                return result("CONTENT_FILTERED", turn.content or "")
            if turn.content is None or not turn.content.strip():
                return result("EMPTY_RESPONSE")
            return result("COMPLETED", turn.content.strip())
        return result("MODEL_ROUND_LIMIT")
