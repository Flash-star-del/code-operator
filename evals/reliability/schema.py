from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


STUDY_ID = "reliability-2026-09-01-preregistered"


@dataclass(frozen=True)
class PairingViolation:
    kind: str
    call_id: str | None
    message_index: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArmResult:
    scenario_id: str
    mechanism: str
    arm: str
    passed: bool
    metrics: dict[str, int | float | str | bool | None]
    violations: tuple[PairingViolation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StudyReport:
    schema_version: int
    study_id: str
    scenario_manifest_sha256: str
    results: tuple[ArmResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_tool_pairing(messages: Sequence[Mapping[str, Any]]) -> tuple[PairingViolation, ...]:
    violations: list[PairingViolation] = []
    seen_results: set[str] = set()
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            expected = [
                None if call.get("id") is None else str(call.get("id"))
                for call in message["tool_calls"]
            ]
            satisfied: set[str | None] = set()
            window_end = min(len(messages), index + 1 + len(expected))
            for slot, result_index in enumerate(range(index + 1, window_end)):
                result = messages[result_index]
                if result.get("role") != "tool":
                    continue
                raw_call_id = result.get("tool_call_id")
                call_id = None if raw_call_id is None else str(raw_call_id)
                if call_id in seen_results:
                    violations.append(PairingViolation("DUPLICATE_RESULT", call_id, result_index))
                elif call_id in expected:
                    if call_id != expected[slot]:
                        violations.append(PairingViolation("OUT_OF_ORDER_RESULT", call_id, result_index))
                    satisfied.add(call_id)
                    if call_id is not None:
                        seen_results.add(call_id)
                else:
                    violations.append(PairingViolation("ORPHAN_RESULT", call_id, result_index))
            for call_id in expected:
                if call_id not in satisfied:
                    violations.append(PairingViolation("MISSING_RESULT", call_id, index))
            index = window_end
            continue
        if role != "tool":
            index += 1
            continue

        raw_call_id = message.get("tool_call_id")
        call_id = None if raw_call_id is None else str(raw_call_id)
        if call_id in seen_results:
            violations.append(PairingViolation("DUPLICATE_RESULT", call_id, index))
        else:
            violations.append(PairingViolation("ORPHAN_RESULT", call_id, index))
        index += 1
    return tuple(violations)
