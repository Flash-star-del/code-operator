"""Serial, non-retaining orchestration for the preregistered comparison study."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .adapters import AdapterResult, MINIMAL_OS_ENVIRONMENT_NAMES, run_adapter
from .grader import GradeResult, grade_workspace
from .manifest import canonical_json, canonical_sha256, validate_before_pilot
from .schema import FrozenManifest, RunCell, SystemConfig
from .workspace import TASK_ROOT, RunWorkspace, create_run_workspace


_USAGE_METRICS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost",
        "cost_usd",
        "latency_seconds",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "study_id",
        "seed",
        "timeout_seconds",
        "systems",
        "task_hashes",
        "pilot",
        "formal",
        "track_b_status",
    }
)
_SYSTEM_KEYS = frozenset(SystemConfig.__dataclass_fields__)
_CELL_KEYS = frozenset(RunCell.__dataclass_fields__)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _safe_usage(value: object) -> dict[str, int | float] | str:
    if not isinstance(value, dict):
        return "unavailable"
    result = {
        key: item
        for key, item in value.items()
        if key in _USAGE_METRICS
        and isinstance(item, (int, float))
        and not isinstance(item, bool)
    }
    return result or "unavailable"


def _adapter_failure(elapsed_seconds: float) -> AdapterResult:
    return AdapterResult(None, False, elapsed_seconds, False, "INFRA_ERROR", "unavailable")


def _grade_failure() -> GradeResult:
    return GradeResult(
        False,
        0,
        0,
        0,
        0,
        (),
        False,
        False,
        (),
        0,
        0,
        "0" * 64,
        "TOOL_OR_INFRA_FAILURE",
        ("GRADING_INFRA_FAILURE",),
    )


def _result_row(
    cell: RunCell,
    *,
    manifest_sha256: str,
    task_hashes: Mapping[str, str],
    adapter: AdapterResult,
    grade: GradeResult,
) -> dict[str, object]:
    """Extract only preregistered aggregate fields while the workspace exists."""
    return {
        "phase": cell.phase,
        "track": cell.track,
        "system_id": cell.system_id,
        "task_id": cell.task_id,
        "replicate": cell.replicate,
        "order_index": cell.order_index,
        "manifest_sha256": manifest_sha256,
        "task_hashes": dict(task_hashes),
        "adapter": {
            "returncode": adapter.returncode,
            "timed_out": adapter.timed_out,
            "elapsed_seconds": adapter.elapsed_seconds,
            "tests_observed": adapter.tests_observed,
            "stop_reason": adapter.stop_reason,
            "usage": _safe_usage(adapter.usage),
        },
        "grade": {
            "resolved": grade.resolved,
            "fail_to_pass_passed": grade.fail_to_pass_passed,
            "fail_to_pass_total": grade.fail_to_pass_total,
            "pass_to_pass_passed": grade.pass_to_pass_passed,
            "pass_to_pass_total": grade.pass_to_pass_total,
            "forbidden_changes": list(grade.forbidden_changes),
            "regression": grade.regression,
            "tests_observed": grade.tests_observed,
            "changed_files": list(grade.changed_files),
            "insertions": grade.insertions,
            "deletions": grade.deletions,
            "patch_sha256": grade.patch_sha256,
            "primary_failure": grade.primary_failure,
            "evidence_tags": list(grade.evidence_tags),
        },
    }


def _remove_readonly_and_retry(
    function: Callable[[str], object],
    path: str,
    error_info: tuple[type[BaseException], BaseException, object],
) -> None:
    error = error_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    function(path)


def _remove_workspace(path: Path) -> None:
    shutil.rmtree(path, onerror=_remove_readonly_and_retry)


def _mapping(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid manifest object shape")
    return value


def load_manifest(path: Path) -> FrozenManifest:
    """Load one byte-canonical manifest and reject every ambiguous shape."""
    try:
        raw = Path(path).read_bytes()
        if not raw or len(raw) > 1_000_000:
            raise ValueError("invalid manifest size")
        payload = _mapping(json.loads(raw.decode("utf-8")), _MANIFEST_KEYS)
        raw_systems = payload["systems"]
        raw_pilot = payload["pilot"]
        raw_formal = payload["formal"]
        raw_hashes = payload["task_hashes"]
        if not isinstance(raw_systems, list) or not isinstance(raw_pilot, list) or not isinstance(raw_formal, list):
            raise ValueError("invalid manifest sequence")
        if not isinstance(raw_hashes, dict) or any(not isinstance(value, dict) for value in raw_hashes.values()):
            raise ValueError("invalid manifest task hashes")

        systems: list[SystemConfig] = []
        for value in raw_systems:
            item = _mapping(value, _SYSTEM_KEYS)
            if not isinstance(item["argv_template"], list) or not isinstance(item["environment_names"], list):
                raise ValueError("invalid manifest system sequence")
            systems.append(
                SystemConfig(
                    system_id=item["system_id"],
                    cli_version=item["cli_version"],
                    executable_sha256=item["executable_sha256"],
                    model=item["model"],
                    auth_type=item["auth_type"],
                    argv_template=tuple(item["argv_template"]),
                    environment_names=tuple(item["environment_names"]),
                    permission_mode=item["permission_mode"],
                    output_mode=item["output_mode"],
                )
            )

        def cells(values: list[object]) -> tuple[RunCell, ...]:
            return tuple(RunCell(**_mapping(value, _CELL_KEYS)) for value in values)

        manifest = FrozenManifest(
            schema_version=payload["schema_version"],
            study_id=payload["study_id"],
            seed=payload["seed"],
            timeout_seconds=payload["timeout_seconds"],
            systems=tuple(systems),
            task_hashes={task_id: dict(values) for task_id, values in raw_hashes.items()},
            pilot=cells(raw_pilot),
            formal=cells(raw_formal),
            track_b_status=payload["track_b_status"],
        )
        if validate_before_pilot(manifest):
            raise ValueError("invalid manifest values")
        if raw != canonical_json(manifest):
            raise ValueError("manifest is not byte-canonical")
        return manifest
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid canonical manifest") from error


def _source_environment(manifest: FrozenManifest) -> dict[str, str]:
    result: dict[str, str] = {}
    approved_names = set(MINIMAL_OS_ENVIRONMENT_NAMES)
    approved_names.update(
        name.upper()
        for config in manifest.systems
        for name in config.environment_names
    )
    for name, value in os.environ.items():
        if name.upper() in approved_names:
            result[name] = value
    for config in manifest.systems:
        if config.system_id == "code-operator":
            pythonpath = next(
                (name for name in config.environment_names if name.upper() == "PYTHONPATH"),
                None,
            )
            if pythonpath is not None:
                result[pythonpath] = os.fspath(_REPOSITORY_ROOT)
    return result


def run_phase(
    manifest: FrozenManifest,
    *,
    phase: str = "pilot",
    report_path: Path,
    source_environment: Mapping[str, str],
    workspace_parent: Path | None = None,
    workspace_factory: Callable[[str, Path], RunWorkspace] = create_run_workspace,
    adapter_runner: Callable[..., AdapterResult] = run_adapter,
    grader: Callable[[str, RunWorkspace], GradeResult] = grade_workspace,
    cleanup_workspace: Callable[[Path], None] = _remove_workspace,
) -> dict[str, object]:
    """Run one frozen comparison phase serially and write canonical JSON."""
    target = Path(report_path)
    if target.exists():
        raise FileExistsError("report already exists")
    if phase not in {"pilot", "formal"}:
        raise ValueError("phase must be pilot or formal")
    violations = validate_before_pilot(manifest)
    if violations:
        raise ValueError("manifest is not valid before pilot: " + ",".join(violations))

    systems = {config.system_id: config for config in manifest.systems}
    cells = manifest.pilot if phase == "pilot" else manifest.formal
    if phase == "pilot":
        if len(cells) != 3 or any(cell.task_id != "T1" for cell in cells):
            raise ValueError("pilot must contain exactly three T1 cells")
    elif len(cells) != 9 or {
        (cell.system_id, cell.task_id, cell.replicate) for cell in cells
    } != {
        (system_id, task_id, 1)
        for system_id in systems
        for task_id in ("T1", "T2", "T3")
    }:
        raise ValueError("formal must contain exactly nine system-task cells")
    manifest_hash = canonical_sha256(manifest)
    parent = Path(workspace_parent) if workspace_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for cell in cells:
        destination = Path(
            tempfile.mkdtemp(
                prefix=f"agent-comparison-{phase}-{cell.order_index:02d}-",
                dir=parent,
            )
        )
        try:
            workspace = workspace_factory(cell.task_id, destination)
            task = (TASK_ROOT / cell.task_id / "task.txt").read_text(encoding="utf-8")
            started = time.monotonic()
            try:
                adapter = adapter_runner(
                    systems[cell.system_id],
                    workspace=workspace.root,
                    task=task,
                    timeout_seconds=manifest.timeout_seconds,
                    source_environment=source_environment,
                )
                if not isinstance(adapter, AdapterResult):
                    adapter = _adapter_failure(time.monotonic() - started)
            except Exception:
                adapter = _adapter_failure(time.monotonic() - started)

            try:
                grade = grader(cell.task_id, workspace)
                if not isinstance(grade, GradeResult):
                    grade = _grade_failure()
            except Exception:
                grade = _grade_failure()

            rows.append(
                _result_row(
                    cell,
                    manifest_sha256=manifest_hash,
                    task_hashes=manifest.task_hashes[cell.task_id],
                    adapter=adapter,
                    grade=grade,
                )
            )
        finally:
            cleanup_workspace(destination)

    report: dict[str, object] = {
        "schema_version": 1,
        "study_id": manifest.study_id,
        "phase": phase,
        "manifest_sha256": manifest_hash,
        "rows": rows,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return report


def main(
    argv: Sequence[str] | None = None,
    *,
    phase_runner: Callable[..., dict[str, object]] = run_phase,
) -> int:
    parser = argparse.ArgumentParser(description="Run a frozen agent-comparison phase")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("pilot", "formal"))
    parser.add_argument("--report", required=True, type=Path)
    arguments = parser.parse_args(argv)
    manifest = load_manifest(arguments.manifest)
    phase_runner(
        manifest,
        phase=arguments.phase,
        report_path=arguments.report,
        source_environment=_source_environment(manifest),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_manifest", "main", "run_phase"]
