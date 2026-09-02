"""Run the preregistered, provider-free reliability ablation.

The runner deliberately contains no model/provider integration.  It builds a
stable manifest from the frozen matrices, runs the two deterministic arms for
each case, and writes a privacy-bounded JSON report exactly once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .reliability.abort_study import frozen_abort_scenarios, run_abort_scenario
from .reliability.context_study import frozen_context_scenarios, run_context_scenario
from .reliability.error_study import (
    error_scenario_manifest_fields,
    frozen_error_scenarios,
    run_error_scenario,
)
from .reliability.schema import ArmResult, STUDY_ID, StudyReport, canonical_sha256


EXPECTED_RESULT_COUNT = 48

_FORBIDDEN_KEY_PARTS = (
    "provider",
    "model",
    "credential",
    "api_key",
    "authorization",
    "request_id",
    "reasoning",
    "prompt",
    "username",
    "started_at",
    "timestamp",
)
_CREDENTIAL_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_ABSOLUTE_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"^(?:\\\\|//)")
_ABSOLUTE_POSIX_PATH = re.compile(r"^/")


def scenario_manifest() -> dict[str, object]:
    """Return only the stable, non-sensitive description of the frozen cases."""

    context = [
        {
            "mechanism": "context",
            "scenario_id": item.scenario_id,
            "arms": ["message_level", "production_full_group"],
            "context_window": item.context_window,
            "max_output_tokens": item.max_output_tokens,
            "input_budget": item.context_window - item.max_output_tokens,
            "message_count": len(item.messages),
            "messages_sha256": canonical_sha256(item.messages),
            "tools_sha256": canonical_sha256(item.tools),
        }
        for item in frozen_context_scenarios()
    ]
    abort = [
        {
            "mechanism": "abort_ordering",
            "scenario_id": item.scenario_id,
            "arms": ["immediate_abort", "production_ordered"],
            "tool_count": item.tool_count,
            "abort_index": item.abort_index,
        }
        for item in frozen_abort_scenarios()
    ]
    error = [
        {
            "mechanism": "error_feedback",
            "scenario_id": item.scenario_id,
            "arms": ["vague_error", "structured_error"],
            **error_scenario_manifest_fields(item),
        }
        for item in frozen_error_scenarios()
    ]
    return {
        "study_id": STUDY_ID,
        "mechanisms": [*context, *abort, *error],
    }


def scenario_manifest_sha256() -> str:
    return canonical_sha256(scenario_manifest())


def _execute_arms(manifest: dict[str, object]) -> list[ArmResult]:
    """Execute exactly the frozen scenarios after the manifest is available."""

    if manifest != scenario_manifest():
        raise ValueError("scenario manifest changed during study")
    results: list[ArmResult] = []
    for scenario in frozen_context_scenarios():
        results.extend(run_context_scenario(scenario))
    for scenario in frozen_abort_scenarios():
        results.extend(run_abort_scenario(scenario))
    for scenario in frozen_error_scenarios():
        results.extend(run_error_scenario(scenario))
    return results


def _row_key(row: ArmResult) -> tuple[str, str, str]:
    return (
        row.mechanism,
        row.scenario_id,
        row.arm,
    )


def generate_report() -> dict[str, object]:
    """Generate a report in memory without timestamps or provider metadata."""

    manifest = scenario_manifest()
    # This assignment intentionally precedes arm execution: the hash commits
    # to the frozen inputs, not to an outcome-dependent reconstruction.
    manifest_hash = canonical_sha256(manifest)
    arms = _execute_arms(manifest)
    ordered_arms = sorted(arms, key=_row_key)
    report = StudyReport(
        schema_version=1,
        study_id=STUDY_ID,
        scenario_manifest_sha256=manifest_hash,
        results=tuple(ordered_arms),
    ).to_dict()
    context_production = [
        result
        for result in ordered_arms
        if result.mechanism == "context"
        and result.arm == "production_full_group"
    ]
    report["scenario_manifest"] = manifest
    report["context_production_summary"] = {
        "production_rows": len(context_production),
        "protocol_checked_rows": sum(
            result.metrics.get("protocol_checked") is True
            for result in context_production
        ),
        "safe_stop_rows": sum(
            result.metrics.get("safe_stop") is True
            for result in context_production
        ),
        "protocol_violation_rows": sum(
            bool(result.violations) for result in context_production
        ),
        "h1_all_scenarios_success": all(
            result.passed for result in context_production
        ),
    }
    if len(ordered_arms) != EXPECTED_RESULT_COUNT:
        raise ValueError("冻结消融行数不正确")
    _validate_report_payload(report)
    return report


def generate_report_bytes() -> bytes:
    """Serialize an in-memory report deterministically as UTF-8 JSON."""

    encoded = json.dumps(
        generate_report(), ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    return encoded.encode("utf-8")


def _iter_payload_items(value: object) -> Iterable[tuple[str | None, object]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _iter_payload_items(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield None, item
            yield from _iter_payload_items(item)


def _validate_report_payload(report: dict[str, object]) -> None:
    for key, value in _iter_payload_items(report):
        if key is not None and any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS):
            raise ValueError("报告包含禁止字段")
        if isinstance(value, str):
            if (
                _ABSOLUTE_WINDOWS_PATH.match(value)
                or _UNC_PATH.match(value)
                or _ABSOLUTE_POSIX_PATH.match(value)
            ):
                raise ValueError("报告包含绝对路径")
            if any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS):
                raise ValueError("报告包含凭据形态")


def write_report_exclusive(path: Path, report: dict[str, object]) -> None:
    """Write one report with exclusive creation and UTF-8 newline semantics."""

    target = Path(path)
    if not target.parent.is_dir():
        raise FileNotFoundError("报告父目录不存在")
    _validate_report_payload(report)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # ``x`` is an exclusive create and must remain independent from the
    # shared provider-report writer; this study has no secret input to redact.
    # If this invocation fails after creating the path, remove only its own
    # incomplete file so a later verified run is not permanently blocked.
    created = False
    try:
        stream = target.open("x", encoding="utf-8", newline="\n")
        created = True
        with stream:
            stream.write(encoded)
    except BaseException:
        if created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行固定的内部可靠性消融")
    parser.add_argument("--report", required=True, type=Path, help="新的 JSON 报告路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report.exists() or not args.report.parent.is_dir():
        print("报告路径必须尚不存在且父目录已经存在", file=sys.stderr)
        return 2
    try:
        report = generate_report()
        write_report_exclusive(args.report, report)
    except (OSError, TypeError, ValueError) as error:
        print(f"可靠性报告生成失败：{error}", file=sys.stderr)
        return 2
    report_rows = report.get("results")
    row_count = len(report_rows) if isinstance(report_rows, (list, tuple)) else 0
    print(json.dumps({"rows": row_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
