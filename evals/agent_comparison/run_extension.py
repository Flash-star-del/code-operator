"""One-off supplementary extension run: two systems on frozen tasks T4/T5.

This runner never touches the preregistered nine-cell manifest or report.  It
freezes its own extension manifest (task hashes first, run second) and writes
both artifacts exclusively so existing evidence can never be replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .adapters import AdapterResult
from .fixture_validator import _component_hashes, validate_task
from .grader import GradeResult, grade_workspace
from .manifest import SEED, canonical_sha256
from .run_study import (
    _adapter_failure,
    _grade_failure,
    _remove_workspace,
    _result_row,
    _source_environment,
    load_manifest,
)
from .adapters import run_adapter
from .schema import RunCell
from .workspace import TASK_ROOT, create_run_workspace

EXTENSION_SYSTEMS = ("code-operator", "kimi-code")
EXTENSION_TASKS = ("T4", "T5")


def _extension_cells(
    systems: tuple[str, ...] = EXTENSION_SYSTEMS,
    tasks: tuple[str, ...] = EXTENSION_TASKS,
) -> tuple[RunCell, ...]:
    """Seeded per-task system order, mirroring the formal blocked schedule."""
    rng = random.Random(SEED)
    cells: list[RunCell] = []
    order_index = 0
    for task_id in tasks:
        shuffled = list(systems)
        rng.shuffle(shuffled)
        for system_id in shuffled:
            cells.append(RunCell("extension", "A", system_id, task_id, 1, order_index))
            order_index += 1
    return tuple(cells)


def build_extension_manifest(
    base_manifest,
    *,
    systems: tuple[str, ...] = EXTENSION_SYSTEMS,
    tasks: tuple[str, ...] = EXTENSION_TASKS,
    study_suffix: str = "-ext-t4t5",
) -> dict[str, object]:
    for task_id in tasks:
        validation = validate_task(task_id)
        if not validation.valid:
            raise ValueError(f"fixture invalid: {task_id}: {validation.violations}")
    configs = [
        asdict(config)
        for config in base_manifest.systems
        if config.system_id in systems
    ]
    if len(configs) != len(systems):
        raise ValueError("base manifest is missing an extension system")
    return {
        "schema_version": 1,
        "study_id": base_manifest.study_id + study_suffix,
        "base_manifest_sha256": canonical_sha256(base_manifest),
        "seed": SEED,
        "timeout_seconds": base_manifest.timeout_seconds,
        "systems": configs,
        "task_hashes": {task_id: dict(_component_hashes(task_id)) for task_id in tasks},
        "cells": [asdict(cell) for cell in _extension_cells(systems, tasks)],
    }


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def run_extension(
    *,
    base_manifest_path: Path,
    manifest_out: Path,
    report_path: Path,
    workspace_parent: Path | None = None,
    systems: tuple[str, ...] = EXTENSION_SYSTEMS,
    tasks: tuple[str, ...] = EXTENSION_TASKS,
    study_suffix: str = "-ext-t4t5",
) -> dict[str, object]:
    if Path(report_path).exists():
        raise FileExistsError("report already exists")
    base_manifest = load_manifest(base_manifest_path)
    extension = build_extension_manifest(
        base_manifest, systems=systems, tasks=tasks, study_suffix=study_suffix
    )
    manifest_bytes = _canonical_bytes(extension)
    with Path(manifest_out).open("xb") as stream:
        stream.write(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    configs = {config.system_id: config for config in base_manifest.systems}
    source_environment = _source_environment(base_manifest)
    parent = Path(workspace_parent) if workspace_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for cell in _extension_cells(systems, tasks):
        destination = Path(
            tempfile.mkdtemp(
                prefix=f"agent-comparison-extension-{cell.order_index:02d}-",
                dir=parent,
            )
        )
        try:
            workspace = create_run_workspace(cell.task_id, destination)
            task = (TASK_ROOT / cell.task_id / "task.txt").read_text(encoding="utf-8")
            started = time.monotonic()
            try:
                adapter = run_adapter(
                    configs[cell.system_id],
                    workspace=workspace.root,
                    task=task,
                    timeout_seconds=base_manifest.timeout_seconds,
                    source_environment=source_environment,
                )
                if not isinstance(adapter, AdapterResult):
                    adapter = _adapter_failure(time.monotonic() - started)
            except Exception:
                adapter = _adapter_failure(time.monotonic() - started)
            try:
                grade = grade_workspace(cell.task_id, workspace)
                if not isinstance(grade, GradeResult):
                    grade = _grade_failure()
            except Exception:
                grade = _grade_failure()
            rows.append(
                _result_row(
                    cell,
                    manifest_sha256=manifest_hash,
                    task_hashes=extension["task_hashes"][cell.task_id],
                    adapter=adapter,
                    grade=grade,
                )
            )
        finally:
            _remove_workspace(destination)

    report: dict[str, object] = {
        "schema_version": 1,
        "study_id": extension["study_id"],
        "phase": "extension",
        "manifest_sha256": manifest_hash,
        "rows": rows,
    }
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a supplementary extension or rerun phase")
    parser.add_argument("--base-manifest", required=True, type=Path)
    parser.add_argument("--manifest-out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--systems", nargs="+", default=list(EXTENSION_SYSTEMS))
    parser.add_argument("--tasks", nargs="+", default=list(EXTENSION_TASKS))
    parser.add_argument("--study-suffix", default="-ext-t4t5")
    arguments = parser.parse_args(argv)
    run_extension(
        base_manifest_path=arguments.base_manifest,
        manifest_out=arguments.manifest_out,
        report_path=arguments.report,
        systems=tuple(arguments.systems),
        tasks=tuple(arguments.tasks),
        study_suffix=arguments.study_suffix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
