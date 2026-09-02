"""Construction and validation of the immutable comparison-study manifest."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .fixture_validator import _component_hashes
from .schema import FrozenManifest, RunCell, SystemConfig


STUDY_ID = "reliability-2026-09-01-preregistered"
TRACK = "A"
SEED = 20260901
SYSTEMS = ("code-operator", "claude-code", "kimi-code")
TASKS = ("T1", "T2", "T3")
REPLICATES = (1,)
TIMEOUT_SECONDS = 360
FORMAL_RUN_COUNT = 9
TRACK_B_STATUSES = (
    "PENDING_MODEL_CHECK",
    "READY",
    "NOT_RUN_MODEL_MISMATCH",
    "NOT_RUN_REDUNDANT_UNIFIED_TRACK_A",
)
TASK_HASH_COMPONENTS = ("prompt", "project", "visible", "hidden", "reference")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(
    r"(?:placeholder|unknown|changeme|change[-_ ]?me|todo|tbd|fill[-_ ]?in|^n/?a$|^unset$|^unconfigured$|^dummy$|^fake$|^<[^>]+>$|^\{[^}]+\}$)",
    re.IGNORECASE,
)
_SHELL_META = re.compile(r"[;&|<>`$()\[\]*?\r\n]")
_CREDENTIAL = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+\S+|(?:api[_-]?key|token|secret|password|passwd)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)
_CREDENTIAL_OPTION = re.compile(r"^(?:--?)(?:api[-_]?key|token|secret|password|passwd)$", re.IGNORECASE)


def _default_task_hashes() -> dict[str, dict[str, str]]:
    return {task_id: dict(_component_hashes(task_id)) for task_id in TASKS}


def _schedule(seed: int) -> tuple[tuple[RunCell, ...], tuple[RunCell, ...]]:
    rng = random.Random(seed)
    pilot_systems = list(SYSTEMS)
    rng.shuffle(pilot_systems)
    pilot = tuple(
        RunCell("pilot", TRACK, system_id, "T1", 1, index)
        for index, system_id in enumerate(pilot_systems)
    )
    formal_cells: list[RunCell] = []
    order_index = len(pilot)
    for task_id in TASKS:
        for replicate in REPLICATES:
            systems = list(SYSTEMS)
            rng.shuffle(systems)
            formal_cells.extend(
                RunCell("formal", TRACK, system_id, task_id, replicate, order_index + index)
                for index, system_id in enumerate(systems)
            )
            order_index += len(systems)
    return pilot, tuple(formal_cells)


def build_manifest(
    *,
    systems: Sequence[SystemConfig] | None = None,
    task_hashes: Mapping[str, Mapping[str, str]] | None = None,
    track_b_status: str = "PENDING_MODEL_CHECK",
    seed: int = SEED,
) -> FrozenManifest:
    """Build the fixed pilot/formal schedule using a private seeded RNG."""
    if systems is None:
        raise ValueError("systems must be explicitly supplied after environment preflight")
    if type(seed) is not int or seed != SEED:
        raise ValueError("seed is fixed to the preregistered value")
    supplied_systems = tuple(systems)
    if len(supplied_systems) != len(SYSTEMS) or tuple(
        config.system_id for config in supplied_systems if isinstance(config, SystemConfig)
    ) != SYSTEMS:
        raise ValueError("systems must contain exactly the three required SystemConfig entries")
    pilot, formal = _schedule(seed)
    normalized_hashes = (
        _default_task_hashes()
        if task_hashes is None
        else {task_id: dict(values) for task_id, values in task_hashes.items()}
    )
    return FrozenManifest(
        schema_version=1,
        study_id=STUDY_ID,
        seed=seed,
        timeout_seconds=TIMEOUT_SECONDS,
        systems=supplied_systems,
        task_hashes=normalized_hashes,
        pilot=pilot,
        formal=formal,
        track_b_status=track_b_status,
    )


def freeze_track_b_status(
    model_matches: bool, *, pilot_started: bool = False
) -> str:
    """Freeze Track B's model gate before Pilot starts."""
    if type(model_matches) is not bool:
        raise TypeError("model_matches must be bool")
    if pilot_started:
        raise ValueError("track B status must be frozen before Pilot starts")
    return "READY" if model_matches else "NOT_RUN_MODEL_MISMATCH"


def validate_track_b_status(
    status: str, *, pilot_started: bool = False
) -> tuple[str, ...]:
    violations: list[str] = []
    if status not in TRACK_B_STATUSES:
        violations.append("invalid-track-b-status")
    if pilot_started and status == "PENDING_MODEL_CHECK":
        violations.append("track-b-not-frozen-before-pilot")
    return tuple(violations)


def validate_before_pilot(manifest: FrozenManifest) -> tuple[str, ...]:
    """Validate a manifest at the gate immediately before Pilot execution."""
    return validate_manifest(manifest, pilot_started=True)


def _validate_system(config: SystemConfig) -> list[str]:
    violations: list[str] = []
    if not isinstance(config.system_id, str):
        violations.append("system-id-not-string")
    elif config.system_id not in SYSTEMS:
        violations.append(f"unknown-system:{config.system_id}")
    for field_name in ("cli_version", "model", "permission_mode", "output_mode"):
        value = getattr(config, field_name)
        if not isinstance(value, str) or not value.strip() or _PLACEHOLDER.search(value):
            violations.append(f"placeholder-{field_name}")
        elif _CREDENTIAL.search(value):
            violations.append(f"credential-in-{field_name}")
    if not isinstance(config.auth_type, str) or config.auth_type not in {
        "environment",
        "official-login-session",
        "none",
    }:
        violations.append("invalid-auth-type")
    argv_valid = isinstance(config.argv_template, tuple)
    if not argv_valid or not config.argv_template:
        violations.append("empty-argv-template")
    else:
        executable = config.argv_template[0]
        if not isinstance(executable, str) or not executable or not os.path.isabs(executable):
            violations.append("executable-must-be-absolute")
    env_valid = isinstance(config.environment_names, tuple)
    if not env_valid:
        violations.append("environment-names-must-be-tuple")
    if env_valid:
        for name in config.environment_names:
            if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
                violations.append("invalid-environment-name")
            if "=" in str(name) or _CREDENTIAL.search(str(name)):
                violations.append("credential-in-environment")
    if not argv_valid:
        return violations + ["argv-template-must-be-tuple"]
    for index, value in enumerate(config.argv_template):
        if not isinstance(value, str):
            violations.append("argv-value-not-string")
            continue
        if _SHELL_META.search(value) or re.search(r"\bshell\s*=\s*True\b", value, re.IGNORECASE):
            violations.append("shell-metacharacter-in-argv")
        if _CREDENTIAL.search(value):
            violations.append("credential-in-argv")
        if _CREDENTIAL_OPTION.fullmatch(value):
            violations.append("credential-option-in-argv")
        elif index and isinstance(config.argv_template[index - 1], str) and _CREDENTIAL_OPTION.fullmatch(config.argv_template[index - 1]):
            violations.append("credential-value-in-argv")
    if not isinstance(config.executable_sha256, str) or not _HEX64.fullmatch(config.executable_sha256) or config.executable_sha256 == "0" * 64:
        violations.append("invalid-executable-sha256")
    return violations


def _cell_key(cell: RunCell) -> tuple[object, ...]:
    return (cell.phase, cell.track, cell.system_id, cell.task_id, cell.replicate)


def validate_manifest(
    manifest: FrozenManifest, *, pilot_started: bool = False
) -> tuple[str, ...]:
    """Return deterministic violation codes; an empty tuple means valid."""
    violations: list[str] = []
    if not isinstance(manifest, FrozenManifest):
        return ("not-frozen-manifest",)
    if type(manifest.schema_version) is not int or manifest.schema_version != 1:
        violations.append("unsupported-schema-version")
    if manifest.study_id != STUDY_ID:
        violations.append("invalid-study-id")
    if type(manifest.seed) is not int or manifest.seed != SEED:
        violations.append("invalid-seed")
    if type(manifest.timeout_seconds) is not int or manifest.timeout_seconds != TIMEOUT_SECONDS:
        violations.append("invalid-timeout")
    systems = manifest.systems if isinstance(manifest.systems, tuple) else ()
    if not isinstance(manifest.systems, tuple):
        violations.append("systems-must-be-tuple")
    if tuple(config.system_id for config in systems if isinstance(config, SystemConfig)) != SYSTEMS:
        violations.append("system-set-mismatch")
    system_ids = [
        config.system_id
        for config in systems
        if isinstance(config, SystemConfig) and isinstance(config.system_id, str)
    ]
    if len(set(system_ids)) != len(systems):
        violations.append("duplicate-system")
    for config in systems:
        if not isinstance(config, SystemConfig):
            violations.append("invalid-system-config")
        else:
            violations.extend(_validate_system(config))
    if not isinstance(manifest.task_hashes, dict):
        violations.append("task-hashes-must-be-dict")
        task_hashes: Mapping[str, Mapping[str, str]] = {}
    else:
        task_hashes = manifest.task_hashes
    if set(task_hashes) != set(TASKS):
        violations.append("task-hash-set-mismatch")
    for task_id in TASKS:
        components = task_hashes.get(task_id, {})
        if not isinstance(components, dict):
            violations.append(f"task-hash-components-not-dict:{task_id}")
            components = {}
        if set(components) != set(TASK_HASH_COMPONENTS):
            violations.append(f"task-hash-components-mismatch:{task_id}")
        for component in TASK_HASH_COMPONENTS:
            value = components.get(component)
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                violations.append(f"invalid-task-hash:{task_id}:{component}")
            elif value != _default_task_hashes().get(task_id, {}).get(component):
                violations.append(f"task-hash-mismatch:{task_id}:{component}")
    violations.extend(validate_track_b_status(manifest.track_b_status, pilot_started=pilot_started))

    if not isinstance(manifest.pilot, tuple):
        violations.append("pilot-must-be-tuple")
    if not isinstance(manifest.formal, tuple):
        violations.append("formal-must-be-tuple")
    pilot = tuple(manifest.pilot) if isinstance(manifest.pilot, tuple) else ()
    formal = tuple(manifest.formal) if isinstance(manifest.formal, tuple) else ()
    all_cells = pilot + formal
    if any(cell == prior for index, cell in enumerate(all_cells) for prior in all_cells[:index]):
        violations.append("duplicate-cell")
    valid_cells: list[RunCell] = []
    for cell in all_cells:
        if not isinstance(cell, RunCell):
            violations.append("invalid-run-cell")
            continue
        field_types = (
            isinstance(cell.phase, str),
            isinstance(cell.track, str),
            isinstance(cell.system_id, str),
            isinstance(cell.task_id, str),
            isinstance(cell.replicate, int) and not isinstance(cell.replicate, bool),
            isinstance(cell.order_index, int) and not isinstance(cell.order_index, bool),
        )
        if not all(field_types):
            violations.append("invalid-run-cell-fields")
        else:
            valid_cells.append(cell)
    expected_pilot, expected_formal = _schedule(SEED)
    if pilot != expected_pilot:
        violations.append("pilot-schedule-mismatch")
    if formal != expected_formal:
        violations.append("formal-schedule-mismatch")
    if len(pilot) < len(expected_pilot):
        violations.append("missing-pilot-cell")
    if len(formal) < len(expected_formal):
        violations.append("missing-formal-cell")
    order_indices = [cell.order_index for cell in valid_cells]
    if len(set(order_indices)) != len(order_indices):
        violations.append("duplicate-order-index")
    if sorted(order_indices) != list(range(len(all_cells))):
        violations.append("non-contiguous-order-index")
    for cell in valid_cells:
        if cell.track != TRACK:
            violations.append("invalid-track")
        if cell.task_id not in TASKS:
            violations.append(f"unknown-task:{cell.task_id}")
        if cell.replicate not in REPLICATES:
            violations.append(f"unknown-replicate:{cell.replicate}")
        if cell.system_id not in SYSTEMS:
            violations.append(f"unknown-system:{cell.system_id}")
        if cell.phase not in {"pilot", "formal"}:
            violations.append(f"unknown-phase:{cell.phase}")
    for task_id in TASKS:
        for replicate in REPLICATES:
            block = [
                cell.system_id
                for cell in formal
                if isinstance(cell, RunCell)
                and isinstance(cell.system_id, str)
                and cell.task_id == task_id
                and cell.replicate == replicate
            ]
            if sorted(block) != sorted(SYSTEMS):
                violations.append(f"unbalanced-block:{task_id}:{replicate}")
    return tuple(sorted(set(violations)))


def canonical_json(value: FrozenManifest | Mapping[str, object]) -> bytes:
    """Serialize a manifest with stable keys, separators and UTF-8 bytes."""
    payload = value.to_dict() if isinstance(value, FrozenManifest) else dict(value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: FrozenManifest | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def write_manifest_exclusive(path: Path, manifest: FrozenManifest) -> None:
    """Write one reviewed manifest as exact canonical bytes without replacement."""
    target = Path(path)
    if validate_before_pilot(manifest):
        raise ValueError("manifest is not valid before pilot")
    with target.open("xb") as stream:
        stream.write(canonical_json(manifest))


__all__ = [
    "FORMAL_RUN_COUNT",
    "REPLICATES",
    "SEED",
    "STUDY_ID",
    "SYSTEMS",
    "TASKS",
    "TIMEOUT_SECONDS",
    "TRACK",
    "build_manifest",
    "canonical_json",
    "canonical_sha256",
    "freeze_track_b_status",
    "validate_manifest",
    "validate_before_pilot",
    "validate_track_b_status",
    "write_manifest_exclusive",
]
