"""Focused manifest checks for names-only credential environments."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys

import pytest

from evals.agent_comparison.manifest import (
    build_manifest,
    canonical_json,
    validate_before_pilot,
    validate_manifest,
    write_manifest_exclusive,
)
from evals.agent_comparison.schema import SystemConfig


def _systems() -> tuple[SystemConfig, ...]:
    executable = str(Path(sys.executable).resolve())
    executable_hash = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    return tuple(
        SystemConfig(
            system_id=system_id,
            cli_version="1.2.3",
            executable_sha256=executable_hash,
            model=f"model-{index}",
            auth_type="none",
            argv_template=(executable, "{{task}}"),
            environment_names=("SAFE_ENV",),
            permission_mode="workspace-only-v1",
            output_mode="jsonl-v1",
        )
        for index, system_id in enumerate(
            ("code-operator", "claude-code", "kimi-code"), start=1
        )
    )


def test_manifest_accepts_names_only_code_operator_environment_config() -> None:
    manifest = build_manifest(systems=_systems())
    code_operator = replace(
        manifest.systems[0],
        auth_type="environment",
        environment_names=(
            "CODE_OPERATOR_API_KEY",
            "CODE_OPERATOR_BASE_URL",
            "CODE_OPERATOR_MODEL",
            "PYTHONPATH",
        ),
    )

    candidate = replace(
        manifest,
        systems=(code_operator,) + manifest.systems[1:],
    )

    assert validate_manifest(candidate) == ()


def test_manifest_accepts_redundant_track_b_status_after_unified_track_a() -> None:
    manifest = build_manifest(
        systems=_systems(),
        track_b_status="NOT_RUN_REDUNDANT_UNIFIED_TRACK_A",
    )

    assert validate_before_pilot(manifest) == ()


def test_manifest_is_written_once_as_exact_canonical_bytes(tmp_path: Path) -> None:
    manifest = build_manifest(
        systems=_systems(),
        track_b_status="NOT_RUN_REDUNDANT_UNIFIED_TRACK_A",
    )
    target = tmp_path / "manifest.json"

    write_manifest_exclusive(target, manifest)

    assert target.read_bytes() == canonical_json(manifest)
    with pytest.raises(FileExistsError):
        write_manifest_exclusive(target, manifest)
    assert target.read_bytes() == canonical_json(manifest)


@pytest.mark.parametrize(
    "environment_names, expected_violation",
    (
        (("NAME=synthetic-value",), "invalid-environment-name"),
        (("synthetic value",), "invalid-environment-name"),
        (("Bearer synthetic-token",), "invalid-environment-name"),
        (("sk-synthetic123",), "invalid-environment-name"),
        (("bad-name",), "invalid-environment-name"),
        (["SAFE_ENV"], "environment-names-must-be-tuple"),
    ),
)
def test_manifest_rejects_values_and_non_names_in_environment_config(
    environment_names: object,
    expected_violation: str,
) -> None:
    manifest = build_manifest(systems=_systems())
    code_operator = replace(
        manifest.systems[0],
        auth_type="environment",
        environment_names=environment_names,
    )

    candidate = replace(
        manifest,
        systems=(code_operator,) + manifest.systems[1:],
    )

    assert expected_violation in validate_manifest(candidate)
