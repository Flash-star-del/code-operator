"""Deterministic RQ3 error-feedback ablation.

This module deliberately does not call a model.  It compares the observable
payloads that a model-facing tool result would contain and classifies retry
actions from the tool name and arguments only.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from code_operator.models import ToolResult

from .schema import ArmResult


ERROR_CLASSES = (
    "PATH_OUTSIDE_WORKSPACE",
    "COMMAND_DENIED",
    "INVALID_ARGUMENTS",
)
RETRY_SHAPES = ("same", "corrected", "unrelated")
_MAX_MESSAGE_CHARS = 200
MetricValue = int | float | str | bool | None


@dataclass(frozen=True)
class ErrorScenario:
    scenario_id: str
    error_code: str
    failed_tool: str
    first_arguments_sha256: str
    retry_shape: str


_CANONICAL_FAILURES: dict[str, tuple[str, dict[str, object]]] = {
    "PATH_OUTSIDE_WORKSPACE": (
        "read_file",
        {"path": "../outside.txt", "token": "synthetic-secret-value"},
    ),
    "COMMAND_DENIED": (
        "run_command",
        {"argv": ["python", "-c", "print('denied')"], "token": "synthetic-secret-value"},
    ),
    "INVALID_ARGUMENTS": (
        "read_file",
        {"path": "README.md", "start_line": "not-an-integer", "token": "synthetic-secret-value"},
    ),
}


def _canonical_argument_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _argument_observation(value: object) -> tuple[str, int]:
    encoded = _canonical_argument_bytes(value)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def frozen_error_scenarios() -> tuple[ErrorScenario, ...]:
    scenarios: list[ErrorScenario] = []
    for error_code in ERROR_CLASSES:
        tool, arguments = _CANONICAL_FAILURES[error_code]
        arguments_hash, _ = _argument_observation(arguments)
        for retry_shape in RETRY_SHAPES:
            scenarios.append(
                ErrorScenario(
                    scenario_id=f"E3_{error_code}_{retry_shape}",
                    error_code=error_code,
                    failed_tool=tool,
                    first_arguments_sha256=arguments_hash,
                    retry_shape=retry_shape,
                )
            )
    return tuple(scenarios)


def classify_retry(
    *,
    first_tool: str,
    first_arguments: object,
    retry_tool: str,
    retry_arguments: object,
) -> str:
    """Classify a retry using only observable tool names and arguments."""

    if first_tool != retry_tool:
        return "UNRELATED_ACTION"
    first_hash, _ = _argument_observation(first_arguments)
    retry_hash, _ = _argument_observation(retry_arguments)
    if first_hash == retry_hash:
        return "SAME_FAILURE_RETRY"
    return "CORRECTED_RETRY"


def vague_error_payload() -> dict[str, object]:
    return {"ok": False, "message": "工具执行失败"}


def structured_error_payload(result: ToolResult) -> dict[str, object]:
    message = str(result.message)
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[:_MAX_MESSAGE_CHARS]
    return {
        "ok": False,
        "error_code": result.error_code or "TOOL_EXECUTION_ERROR",
        "message": message,
    }


def _attributable_failure(
    payload: dict[str, object], *, expected_error_code: str
) -> bool:
    return (
        payload.get("ok") is False
        and payload.get("error_code") == expected_error_code
    )


def _arguments_for(scenario: ErrorScenario) -> dict[str, object]:
    try:
        _, arguments = _CANONICAL_FAILURES[scenario.error_code]
    except KeyError as exc:
        raise ValueError(f"unknown frozen error code: {scenario.error_code}") from exc
    observed_hash, _ = _argument_observation(arguments)
    if observed_hash != scenario.first_arguments_sha256:
        raise ValueError("scenario first argument hash does not match frozen input")
    return copy.deepcopy(arguments)


def _retry_arguments(first_arguments: dict[str, object], retry_shape: str) -> object:
    if retry_shape == "same":
        return copy.deepcopy(first_arguments)
    if retry_shape == "corrected":
        corrected = copy.deepcopy(first_arguments)
        if "path" in corrected:
            corrected["path"] = "inside.txt"
        elif "argv" in corrected:
            corrected["argv"] = ["python", "-c", "print('allowed')"]
        corrected.pop("token", None)
        corrected["corrected"] = True
        return corrected
    if retry_shape == "unrelated":
        return {"path": "."}
    raise ValueError(f"unknown retry shape: {retry_shape}")


def _scenario_inputs(
    scenario: ErrorScenario,
) -> tuple[dict[str, object], str, object]:
    first_arguments = _arguments_for(scenario)
    retry_arguments = _retry_arguments(first_arguments, scenario.retry_shape)
    retry_tool = (
        scenario.failed_tool if scenario.retry_shape != "unrelated" else "list_dir"
    )
    return first_arguments, retry_tool, retry_arguments


def error_scenario_manifest_fields(scenario: ErrorScenario) -> dict[str, object]:
    """Return non-sensitive fields that commit to the actual retry input."""

    _, retry_tool, retry_arguments = _scenario_inputs(scenario)
    retry_sha256, retry_utf8_bytes = _argument_observation(retry_arguments)
    return {
        "error_code": scenario.error_code,
        "failed_tool": scenario.failed_tool,
        "first_arguments_sha256": scenario.first_arguments_sha256,
        "retry_shape": scenario.retry_shape,
        "retry_tool": retry_tool,
        "retry_arguments_sha256": retry_sha256,
        "retry_arguments_utf8_bytes": retry_utf8_bytes,
    }


def run_error_scenario(
    scenario: ErrorScenario,
) -> tuple[ArmResult, ArmResult]:
    first_arguments, retry_tool, retry_arguments = _scenario_inputs(scenario)
    retry_classification = classify_retry(
        first_tool=scenario.failed_tool,
        first_arguments=first_arguments,
        retry_tool=retry_tool,
        retry_arguments=retry_arguments,
    )

    failure = ToolResult(
        tool_call_id=f"{scenario.scenario_id}-failure",
        name=scenario.failed_tool,
        ok=False,
        error_code=scenario.error_code,
        message=f"注入错误：{scenario.error_code}",
        details={},
    )
    structured = structured_error_payload(failure)
    first_arguments_sha256, first_arguments_utf8_bytes = _argument_observation(
        first_arguments
    )
    vague = vague_error_payload()
    vague_attributable = _attributable_failure(
        vague, expected_error_code=scenario.error_code
    )
    structured_attributable = _attributable_failure(
        structured, expected_error_code=scenario.error_code
    )
    vague_metrics: dict[str, MetricValue] = {
        "attributable_failure": vague_attributable,
        "payload_fields": len(vague),
        "retry_classification": retry_classification,
        "first_arguments_sha256": first_arguments_sha256,
        "first_arguments_utf8_bytes": first_arguments_utf8_bytes,
    }
    structured_metrics: dict[str, MetricValue] = {
        "attributable_failure": structured_attributable,
        "payload_fields": len(structured),
        "retry_classification": retry_classification,
        "first_arguments_sha256": first_arguments_sha256,
        "first_arguments_utf8_bytes": first_arguments_utf8_bytes,
        "message_length": len(str(structured["message"])),
    }
    return (
        ArmResult(
            scenario_id=scenario.scenario_id,
            mechanism="error_feedback",
            arm="vague_error",
            passed=vague_attributable,
            metrics=vague_metrics,
        ),
        ArmResult(
            scenario_id=scenario.scenario_id,
            mechanism="error_feedback",
            arm="structured_error",
            passed=structured_attributable,
            metrics=structured_metrics,
        ),
    )


__all__ = [
    "ERROR_CLASSES",
    "RETRY_SHAPES",
    "ErrorScenario",
    "classify_retry",
    "error_scenario_manifest_fields",
    "frozen_error_scenarios",
    "run_error_scenario",
    "structured_error_payload",
    "vague_error_payload",
]
