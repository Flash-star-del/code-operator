"""Deterministic context-trimming ablation used by the reliability study."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from code_operator.context import ContextLimitError, ContextManager

from .schema import ArmResult, validate_tool_pairing

MetricValue = int | float | str | bool | None
Metrics = dict[str, MetricValue]


@dataclass(frozen=True)
class ContextScenario:
    scenario_id: str
    messages: tuple[dict[str, object], ...]
    tools: tuple[dict[str, object], ...]
    context_window: int
    max_output_tokens: int


CONTEXT_MATRIX = (
    ("C1_ONE_CALL_BOUNDARY", 0, 1, 1, "x" * 180, 1),
    ("C2_TWO_CALL_BOUNDARY", 0, 1, 2, "y" * 180, 1),
    ("C3_OLD_TURN_DROP", 1, 1, 1, "old" * 80, 3),
    ("C4_MULTI_TURN_MULTI_CALL", 2, 2, 2, "payload" * 40, 4),
    ("C5_NO_TOOL_OLD_TURN", 2, 0, 0, "plain" * 60, 2),
    ("C6_UTF8_BOUNDARY", 1, 1, 2, "中文路径" * 60, 3),
)


def _tool_schema(scenario_id: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"fixture_{scenario_id.lower()}",
            "description": "deterministic reliability fixture",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _assistant_group(
    scenario_id: str,
    group: int,
    calls_per_group: int,
    payload: str,
) -> list[dict[str, object]]:
    call_ids = [f"{scenario_id}-g{group}-c{call}" for call in range(calls_per_group)]
    tool_calls: list[dict[str, object]] = [
        {
            "id": call_id,
            "type": "function",
            "function": {"name": "fixture", "arguments": "{}"},
        }
        for call_id in call_ids
    ]
    assistant: dict[str, object] = {
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls,
    }
    if not call_ids:
        return [
            {
                "role": "assistant",
                "content": f"assistant completion {scenario_id} {payload}",
            }
        ]
    tool_results: list[dict[str, object]] = [
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "fixture",
            "content": '{"ok":true}' + payload,
        }
        for call_id in call_ids
    ]
    return [assistant, *tool_results]


def context_scenario(
    scenario_id: str,
    old_turns: int,
    current_groups: int,
    calls_per_group: int,
    payload: str,
    baseline_drop_count: int,
) -> ContextScenario:
    """Build one deterministic scenario and calibrate its input budget."""

    if old_turns < 0 or current_groups < 0 or calls_per_group < 0:
        raise ValueError("scenario counts must be non-negative")
    if baseline_drop_count < 0:
        raise ValueError("baseline_drop_count must be non-negative")

    messages: list[dict[str, object]] = [
        {"role": "system", "content": f"{scenario_id} system"}
    ]
    group = 0
    for turn in range(old_turns):
        messages.append(
            {"role": "user", "content": f"{scenario_id} old user turn {turn}"}
        )
        messages.extend(_assistant_group(scenario_id, group, calls_per_group, payload))
        group += 1

    messages.append({"role": "user", "content": f"{scenario_id} current user"})
    for _ in range(current_groups):
        messages.extend(_assistant_group(scenario_id, group, calls_per_group, payload))
        group += 1

    tools: tuple[dict[str, object], ...] = (
        (_tool_schema(scenario_id),) if calls_per_group else ()
    )
    calibration_manager = ContextManager(context_window=10_000, max_output_tokens=64)
    after_exact_removals = [dict(message) for message in messages]
    if baseline_drop_count > len(after_exact_removals) - 1:
        raise ValueError("baseline_drop_count exceeds non-system message count")
    for _ in range(baseline_drop_count):
        del after_exact_removals[1]
    estimate = calibration_manager.estimate_tokens(after_exact_removals, tools)
    return ContextScenario(
        scenario_id=scenario_id,
        messages=tuple(dict(message) for message in messages),
        tools=tuple(dict(tool) for tool in tools),
        context_window=64 + estimate,
        max_output_tokens=64,
    )


def frozen_context_scenarios() -> tuple[ContextScenario, ...]:
    return tuple(context_scenario(*row) for row in CONTEXT_MATRIX)


def _metrics(
    messages: Sequence[Mapping[str, object]],
    *,
    estimated_tokens: int,
    trimmed_messages: int,
    trimmed_turns: int,
    trimmed_rounds: int | None,
) -> Metrics:
    return {
        "estimated_tokens": estimated_tokens,
        "kept_messages": len(messages),
        "trimmed_messages": trimmed_messages,
        "trimmed_turns": trimmed_turns,
        "trimmed_rounds": trimmed_rounds,
    }


def message_level_trim(
    scenario: ContextScenario,
) -> tuple[list[dict[str, object]], Metrics]:
    """Apply the intentionally weak Eval-only one-message-at-a-time baseline."""

    manager = ContextManager(
        context_window=scenario.context_window,
        max_output_tokens=scenario.max_output_tokens,
    )
    kept = [dict(message) for message in scenario.messages]
    newest_user = max(
        (index for index, message in enumerate(kept) if message.get("role") == "user"),
        default=None,
    )
    trimmed = 0
    estimated = manager.estimate_tokens(kept, scenario.tools)
    while estimated > manager.input_budget:
        removable = next(
            (
                index
                for index in range(1, len(kept))
                if index != newest_user
            ),
            None,
        )
        if removable is None:
            raise ContextLimitError("system and newest user cannot fit in context")
        del kept[removable]
        trimmed += 1
        if newest_user is not None and removable < newest_user:
            newest_user -= 1
        estimated = manager.estimate_tokens(kept, scenario.tools)
    return kept, _metrics(
        kept,
        estimated_tokens=estimated,
        trimmed_messages=trimmed,
        trimmed_turns=0,
        trimmed_rounds=0,
    )


def _baseline_result(scenario: ContextScenario) -> ArmResult:
    try:
        messages, metrics = message_level_trim(scenario)
        violations = validate_tool_pairing(messages)
        metrics.update(
            {"outcome": "OK", "safe_stop": False, "protocol_checked": True}
        )
        return ArmResult(
            scenario.scenario_id,
            "context",
            "message_level",
            not violations,
            metrics,
            violations,
        )
    except ContextLimitError:
        violations = validate_tool_pairing(())
        return ArmResult(
            scenario.scenario_id,
            "context",
            "message_level",
            False,
            {
                **_metrics(
                (),
                estimated_tokens=0,
                trimmed_messages=max(0, len(scenario.messages) - 2),
                trimmed_turns=0,
                trimmed_rounds=0,
                ),
                "outcome": "CONTEXT_LIMIT",
                "safe_stop": True,
                "protocol_checked": False,
            },
            violations,
        )


def _production_result(scenario: ContextScenario) -> ArmResult:
    manager = ContextManager(
        context_window=scenario.context_window,
        max_output_tokens=scenario.max_output_tokens,
    )
    try:
        prepared = manager.prepare(scenario.messages, scenario.tools)
    except ContextLimitError:
        required_minimum = manager.estimate_tokens(scenario.messages, scenario.tools)
        if required_minimum > manager.input_budget:
            violations = validate_tool_pairing(())
            return ArmResult(
                scenario.scenario_id,
                "context",
                "production_full_group",
                False,
                {
                    "estimated_tokens": required_minimum,
                    "kept_messages": None,
                    "trimmed_messages": None,
                    "trimmed_turns": None,
                    "trimmed_rounds": None,
                    "input_budget": manager.input_budget,
                    "required_minimum_tokens": required_minimum,
                    "shortfall_tokens": required_minimum - manager.input_budget,
                    "outcome": "CONTEXT_LIMIT",
                    "safe_stop": True,
                    "protocol_checked": False,
                },
                violations,
            )
        violations = validate_tool_pairing(scenario.messages)
        return ArmResult(
            scenario.scenario_id,
            "context",
            "production_full_group",
            False,
            {
                "estimated_tokens": required_minimum,
                "kept_messages": None,
                "trimmed_messages": None,
                "trimmed_turns": None,
                "trimmed_rounds": None,
                "input_budget": manager.input_budget,
                "required_minimum_tokens": None,
                "shortfall_tokens": 0,
                "outcome": "INVALID_CONTEXT",
                "safe_stop": False,
                "protocol_checked": True,
            },
            violations,
        )
    violations = validate_tool_pairing(prepared.messages)
    latest_user_index = max(
        index
        for index, message in enumerate(scenario.messages)
        if message.get("role") == "user"
    )
    latest_tail = scenario.messages[latest_user_index + 1 :]
    latest_group_preserved = (
        not latest_tail
        and prepared.messages[-1].get("role") == "user"
        or bool(latest_tail)
        and tuple(prepared.messages[-len(latest_tail) :]) == latest_tail
    )
    metrics = _metrics(
        prepared.messages,
        estimated_tokens=prepared.estimated_tokens,
        trimmed_messages=len(scenario.messages) - len(prepared.messages),
        trimmed_turns=prepared.trimmed_turns,
        trimmed_rounds=prepared.trimmed_rounds,
    )
    metrics.update(
        {
            "input_budget": manager.input_budget,
            "required_minimum_tokens": prepared.estimated_tokens,
            "shortfall_tokens": 0,
            "outcome": "OK",
            "safe_stop": False,
            "protocol_checked": True,
            "latest_group_preserved": latest_group_preserved,
        }
    )
    return ArmResult(
        scenario.scenario_id,
        "context",
        "production_full_group",
        not violations and latest_group_preserved,
        metrics,
        violations,
    )


def run_context_scenario(
    scenario: ContextScenario,
) -> tuple[ArmResult, ArmResult]:
    return _baseline_result(scenario), _production_result(scenario)
