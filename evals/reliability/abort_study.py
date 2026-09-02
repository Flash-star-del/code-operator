"""Deterministic abort-pairing ablation for the reliability study.

The baseline is an Eval-only counterfactual.  The production arm drives a
fresh AgentLoop and observes only the public FakeModelClient capture, which
keeps the comparison independent from AgentLoop's private message store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, ToolCall, ToolResult, Usage
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient

from .schema import ArmResult


MetricValue = int | float | str | bool | None
Metrics = dict[str, MetricValue]

EXPECTED_SUCCESS_PAYLOAD: dict[str, object] = {
    "ok": True,
    "error_code": None,
    "message": "ok",
    "details": {},
}


@dataclass(frozen=True)
class AbortScenario:
    scenario_id: str
    tool_count: int
    abort_index: int


def frozen_abort_scenarios() -> tuple[AbortScenario, ...]:
    return tuple(
        AbortScenario(f"A{count}_{index}", count, index)
        for count in (2, 3, 4)
        for index in range(count)
    )


def _turn(*, calls: list[ToolCall] | None = None, content: str | None = None) -> AssistantTurn:
    return AssistantTurn(
        content=content,
        tool_calls=[] if calls is None else calls,
        finish_reason="tool_calls" if calls else "stop",
        usage=Usage(prompt_tokens=None, completion_tokens=None, total_tokens=1),
    )


def _calls(scenario: AbortScenario) -> list[ToolCall]:
    return [
        ToolCall(f"call-{index}", "list_dir", "{}")
        for index in range(scenario.tool_count)
    ]


def immediate_abort_baseline(scenario: AbortScenario) -> ArmResult:
    """Model the weak immediate-return counterfactual without production code."""

    result_count = scenario.abort_index
    return ArmResult(
        scenario_id=scenario.scenario_id,
        mechanism="abort_ordering",
        arm="immediate_abort",
        passed=False,
        metrics={
            "declared_calls": scenario.tool_count,
            "result_count": result_count,
            "ordered_result_count": result_count,
            "next_round_accepted": False,
            "completed_before_abort": scenario.abort_index,
            "synthetic_after_abort": 0,
        },
    )


def _run_production(
    scenario: AbortScenario,
) -> tuple[str, str, list[dict[str, object]]]:
    """Run one fresh loop and return statuses plus captured second-round tools."""

    calls = _calls(scenario)

    def handler(*, tool_call_id: str, **_: Any) -> ToolResult:
        index = int(tool_call_id.removeprefix("call-"))
        if index == scenario.abort_index:
            return ToolResult(
                tool_call_id=tool_call_id,
                name="list_dir",
                ok=False,
                error_code="USER_ABORTED",
                message="工具执行被用户中止",
                details={},
            )
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="ok",
            details={},
        )

    model = FakeModelClient(
        [
            _turn(calls=calls),
            _turn(content="next completed"),
        ]
    )
    loop = AgentLoop(model, ToolRegistry({"list_dir": handler}))
    first = loop.run("abort fixture")
    second = loop.run("continue fixture")

    captured_second_messages = model.calls[1][0]
    tool_messages = [
        dict(message)
        for message in captured_second_messages
        if message.get("role") == "tool"
    ]
    return first.status, second.status, tool_messages


def _captured_abort_tool_messages(scenario: AbortScenario) -> list[dict[str, object]]:
    """Return the second request's captured tool messages for exact assertions."""

    return _run_production(scenario)[2]


def _count_completed_success_payloads(
    payloads: list[dict[str, object]],
) -> int:
    return sum(payload == EXPECTED_SUCCESS_PAYLOAD for payload in payloads)


def production_abort_result(scenario: AbortScenario) -> ArmResult:
    """Run one fresh production loop and validate its captured replay turn."""

    try:
        first_status, second_status, tool_messages = _run_production(scenario)
    except (IndexError, KeyError, TypeError, ValueError):
        return ArmResult(
            scenario_id=scenario.scenario_id,
            mechanism="abort_ordering",
            arm="production_ordered",
            passed=False,
            metrics={
                "declared_calls": scenario.tool_count,
                "result_count": None,
                "ordered_result_count": None,
                "next_round_accepted": False,
                "completed_before_abort": None,
                "synthetic_after_abort": None,
            },
        )

    result_ids = [
        str(message["tool_call_id"])
        for message in tool_messages
        if "tool_call_id" in message
    ]
    expected_ids = [call.id for call in _calls(scenario)]
    ordered = result_ids == expected_ids and len(result_ids) == len(set(result_ids))

    payloads: list[dict[str, object]] = []
    payload_parse_failed = False
    for message in tool_messages:
        try:
            decoded = json.loads(str(message.get("content")))
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            payload_parse_failed = True
            break
        if not isinstance(decoded, dict):
            payload_parse_failed = True
            break
        payloads.append(decoded)

    if payload_parse_failed:
        failure_metrics: Metrics = {
            "declared_calls": scenario.tool_count,
            "result_count": None,
            "ordered_result_count": None,
            "next_round_accepted": second_status == "COMPLETED",
            "completed_before_abort": None,
            "synthetic_after_abort": None,
        }
        return ArmResult(
            scenario_id=scenario.scenario_id,
            mechanism="abort_ordering",
            arm="production_ordered",
            passed=False,
            metrics=failure_metrics,
        )

    completed_before_abort = _count_completed_success_payloads(
        payloads[: scenario.abort_index]
    )
    synthetic_after_abort = sum(
        payload.get("error_code") == "NOT_EXECUTED_AFTER_ABORT"
        for payload in payloads[scenario.abort_index + 1 :]
    )
    expected_abort = {
        "ok": False,
        "error_code": "USER_ABORTED",
        "message": "工具执行被用户中止",
        "details": {},
    }
    expected_synthetic = {
        "ok": False,
        "error_code": "NOT_EXECUTED_AFTER_ABORT",
        "message": "同轮较早的工具调用被中止，本调用未执行",
        "details": {},
    }
    payloads_exact = (
        len(payloads) == scenario.tool_count
        and all(
            payload == EXPECTED_SUCCESS_PAYLOAD
            for payload in payloads[: scenario.abort_index]
        )
        and payloads[scenario.abort_index] == expected_abort
        and all(
            payload == expected_synthetic
            for payload in payloads[scenario.abort_index + 1 :]
        )
    )
    metrics: Metrics = {
        "declared_calls": scenario.tool_count,
        "result_count": len(result_ids),
        "ordered_result_count": len(result_ids) if ordered else 0,
        "next_round_accepted": second_status == "COMPLETED",
        "completed_before_abort": completed_before_abort,
        "synthetic_after_abort": synthetic_after_abort,
    }
    return ArmResult(
        scenario_id=scenario.scenario_id,
        mechanism="abort_ordering",
        arm="production_ordered",
        passed=(
            first_status == "USER_ABORTED"
            and len(result_ids) == scenario.tool_count
            and ordered
            and second_status == "COMPLETED"
            and completed_before_abort == scenario.abort_index
            and synthetic_after_abort
            == scenario.tool_count - scenario.abort_index - 1
            and payloads_exact
        ),
        metrics=metrics,
    )


def run_abort_scenario(
    scenario: AbortScenario,
) -> tuple[ArmResult, ArmResult]:
    return immediate_abort_baseline(scenario), production_abort_result(scenario)
