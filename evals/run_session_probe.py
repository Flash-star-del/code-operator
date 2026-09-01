from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from code_operator.config import ConfigError, ProviderConfig, load_provider_config
from code_operator.loop import ModelLike
from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor
from code_operator.session import AgentSession
from evals.run_golden import (
    CommandResult,
    EvalInfrastructureError,
    RUNTIME_DIRECTORY_NAMES,
    RUNTIME_FILE_SUFFIXES,
    fixture_hash,
    run_process,
    write_report_exclusive,
)


EVAL_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = EVAL_ROOT.parent
FIXTURE_ROOT = EVAL_ROOT / "session_probe" / "project"
TURN1_PATH = EVAL_ROOT / "session_probe" / "turn1.txt"
TURN2_PATH = EVAL_ROOT / "session_probe" / "turn2.txt"
TARGET_RELATIVE_PATH = "greeting.py"
TEST_RELATIVE_PATH = "tests/test_greeting.py"
TEST_TIMEOUT_SECONDS = 60
PROBE_ID = "e4-session-probe-2026-09-01"
ALLOWED_CHANGED_PATHS = frozenset({"greeting.py"})
REPORT_RELATIVE_PATH = Path("docs") / "evidence" / "e4-session-probe.json"
O1B_PROTOCOL_VERSION = "o1b-v1"
O1B_PLANNED_ATTEMPTS = 3
O1B_REPORT_PATHS = {
    index: Path("docs") / "evidence" / f"o1b-session-probe-{index:02d}.json"
    for index in range(1, O1B_PLANNED_ATTEMPTS + 1)
}
O1B_RESERVATION_PATHS = {
    index: path.with_suffix(".reservation.json")
    for index, path in O1B_REPORT_PATHS.items()
}
O1B_DESIGN_SPEC_RELATIVE = (
    Path("docs")
    / "superpowers"
    / "specs"
    / "2026-09-01-o1b-session-replication-design.md"
)
O1B_STOP_REASONS = frozenset(
    {
        "COMPLETED_PLANNED_ATTEMPTS",
        "SECURITY_STOP",
        "PRODUCTION_DEFECT_STOP",
        "PROVIDER_BOUNDARY_STOP",
        "AUTHORIZATION_WITHDRAWN",
        "EVALUATOR_PROTOCOL_STOP",
    }
)
_PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [^-\r\n]+ PRIVATE KEY-----")
_UNREDACTED_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+(?!<REDACTED>)[^\s,;]+")
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![:/])\/(?:[^\s\"\\]+))")
_RAW_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\(?:[?.][\\/]|[^\\/\s]+[\\/])|(?<![:/])\/(?:[^\s\"\\]+))"
)


_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _ToolEvent:
    name: str
    ok: bool
    error_code: str | None


class _ProbeTrace:
    """Keep only tool outcome metadata needed by the probe's policy checks."""

    def __init__(self) -> None:
        self._turns: list[list[_ToolEvent]] = []

    def start_turn(self) -> None:
        self._turns.append([])

    def events(self, turn: int) -> tuple[_ToolEvent, ...]:
        return tuple(self._turns[turn])

    def record_model_round(
        self, _round_number: int, _tool_call_count: int, _usage_available: bool
    ) -> None:
        return

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        if self._turns:
            self._turns[-1].append(
                _ToolEvent(call.name, result.ok, result.error_code)
            )

    def record_run(self, _result: RunResult) -> None:
        return


@dataclass(frozen=True)
class _ManifestEntry:
    kind: str
    size: int
    mtime_ns: int
    ctime_ns: int
    identity: tuple[int, int]
    sha256: str | None


@dataclass(frozen=True)
class FixtureSummary:
    fixture_sha256: str
    prompt_sha256: str
    initial_test_returncode: int
    target_relative_path: str
    target_initial_sha256: str


@dataclass(frozen=True)
class FrozenProbeMetadata:
    fixture_sha256: str
    prompt_sha256: str
    target_initial_sha256: str
    production_tree_sha256: str
    evaluator_protocol_sha256: str
    config: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AttemptReservation:
    protocol_version: str
    attempt_index: int
    created_at: str
    metadata: FrozenProbeMetadata

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class O1BSummary:
    """Offline reconstruction of the fixed three-attempt O1b queue."""

    schema_version: int
    protocol_version: str
    planned_attempts: int
    attempted_count: int
    valid_attempts: int
    primary_passes: int
    invalid_infra_count: int
    ideal_trace_passes: int
    unexecuted_attempts: tuple[int, ...]
    stop_reason: str
    classification: str
    attempts: tuple[dict[str, object], ...]
    input_sha256: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _ComponentGuard:
    components: tuple[tuple[Path, Path, tuple[int, ...]], ...]


@dataclass(frozen=True)
class ProbeReport:
    schema_version: int
    probe_id: str
    mode: str
    protocol_version: str | None
    attempt_index: int | None
    production_tree_sha256: str | None
    evaluator_protocol_sha256: str | None
    config: dict[str, object] | None
    outcome: str
    failure_code: str | None
    fixture_sha256: str | None
    prompt_sha256: str | None
    target_initial_sha256: str | None
    target_after_turn1_sha256: str | None
    target_after_undo_sha256: str | None
    turn_statuses: tuple[str, ...]
    model_rounds: tuple[int, ...]
    tool_calls: tuple[int, ...]
    provider_total_tokens: tuple[int | None, ...]
    changed_paths_after_turn1: tuple[str, ...]
    tests_after_turn1_returncode: int | None
    tests_after_turn2_returncode: int | None
    undo_ok: bool
    undo_depth_after: int
    reset_undo_depth: int
    reset_pending_events: int
    reset_history_message_count: int | None
    reset_read_hash_count: int | None
    close_idempotent: bool
    owned_client_close_calls: int | None
    baseline_direct_subprocess_count: int | None
    new_residual_direct_subprocess_count: int | None
    session_artifact_count: int
    elapsed_seconds: float
    turn1_ideal_trace: bool | None
    turn2_ideal_trace: bool | None
    ideal_trace_overall: bool | None
    turn2_exact_value_observed: bool | None

    @classmethod
    def invalid_infra(cls, mode: str, failure_code: str) -> "ProbeReport":
        return cls(
            schema_version=2,
            probe_id=PROBE_ID,
            mode=mode,
            protocol_version=None,
            attempt_index=None,
            production_tree_sha256=None,
            evaluator_protocol_sha256=None,
            config=None,
            outcome="INVALID_INFRA",
            failure_code=failure_code,
            fixture_sha256=None,
            prompt_sha256=None,
            target_initial_sha256=None,
            target_after_turn1_sha256=None,
            target_after_undo_sha256=None,
            turn_statuses=(),
            model_rounds=(),
            tool_calls=(),
            provider_total_tokens=(),
            changed_paths_after_turn1=(),
            tests_after_turn1_returncode=None,
            tests_after_turn2_returncode=None,
            undo_ok=False,
            undo_depth_after=0,
            reset_undo_depth=0,
            reset_pending_events=0,
            reset_history_message_count=None,
            reset_read_hash_count=None,
            close_idempotent=False,
            owned_client_close_calls=None,
            baseline_direct_subprocess_count=None,
            new_residual_direct_subprocess_count=None,
            session_artifact_count=0,
            elapsed_seconds=0.0,
            turn1_ideal_trace=None,
            turn2_ideal_trace=None,
            ideal_trace_overall=None,
            turn2_exact_value_observed=None,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


O1B_RESULT_SCHEMA_FIELDS = frozenset(ProbeReport.__dataclass_fields__)
_PROBE_STATUS_VALUES = frozenset(
    {
        "COMPLETED", "CONTEXT_LIMIT", "USER_ABORTED", "PROVIDER_PROTOCOL_ERROR",
        "PROVIDER_ERROR", "TOOL_CALL_LIMIT", "REPEATED_CALL",
        "CONSECUTIVE_TOOL_FAILURES", "OUTPUT_TRUNCATED", "CONTENT_FILTERED",
        "EMPTY_RESPONSE", "MODEL_ROUND_LIMIT",
    }
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt_hash(turn1_path: Path, turn2_path: Path) -> str:
    digest = hashlib.sha256()
    for relative, path in (("turn1.txt", turn1_path), ("turn2.txt", turn2_path)):
        encoded_relative = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(encoded_relative).to_bytes(4, "big"))
        digest.update(encoded_relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _manifest_sha256(
    paths: Sequence[Path], *, repository_root: Path | None = None
) -> str:
    """Hash a fixed set of ordinary files and their repository-relative names."""
    root = REPOSITORY_ROOT if repository_root is None else Path(repository_root)
    try:
        if _is_link_or_reparse(root):
            raise EvalInfrastructureError("哈希仓库根目录无效")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise EvalInfrastructureError("哈希仓库根目录无效")
    except EvalInfrastructureError:
        raise
    except (OSError, RuntimeError):
        raise EvalInfrastructureError("哈希仓库根目录无效") from None

    entries: dict[
        str,
        tuple[Path, tuple[tuple[Path, tuple[int, int, int, int, int], int], ...]],
    ] = {}
    for raw_path in paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.absolute()
        try:
            lexical_components = _component_snapshot(root, candidate)
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except EvalInfrastructureError:
            raise
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            raise EvalInfrastructureError("哈希输入文件无效") from None
        if relative in entries:
            raise EvalInfrastructureError("哈希输入路径重复")
        if not stat.S_ISREG(lexical_components[-1][2]):
            raise EvalInfrastructureError("哈希输入必须是普通文件")
        entries[relative] = (candidate, lexical_components)

    if not entries:
        raise EvalInfrastructureError("哈希输入文件为空")
    digest = hashlib.sha256()
    try:
        for relative in sorted(entries):
            path, before_components = entries[relative]
            content = _read_file_stably(path)
            after_components = _component_snapshot(root, path)
            if before_components != after_components:
                raise EvalInfrastructureError("哈希输入在读取期间发生变化")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
            digest.update(b"\n")
    except (OSError, UnicodeError, ValueError):
        raise EvalInfrastructureError("哈希输入文件读取失败") from None
    return digest.hexdigest()


def _file_signature(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _guard_identity(details: os.stat_result) -> tuple[int, int, int]:
    # Directory timestamps legitimately change when reservation files are
    # created; device/inode/mode identify the guarded component itself.
    return details.st_dev, details.st_ino, details.st_mode


def _component_identity(details: os.stat_result) -> tuple[int, ...]:
    if stat.S_ISREG(details.st_mode):
        return _file_signature(details) + (details.st_mode,)
    return _guard_identity(details)


def _capture_component_guard(anchor: Path, target: Path) -> _ComponentGuard:
    try:
        anchor = Path(anchor).absolute()
        target = Path(target).absolute()
        relative = target.relative_to(anchor)
        current = anchor
        components: list[tuple[Path, Path, tuple[int, ...]]] = []
        for part in ((), *relative.parts):
            if part:
                current = current / part
            details = current.lstat()
            if _is_link_or_reparse(current):
                raise EvalInfrastructureError("O1b evidence 路径包含链接")
            components.append((current, current.resolve(strict=True), _component_identity(details)))
        return _ComponentGuard(tuple(components))
    except EvalInfrastructureError:
        raise
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise EvalInfrastructureError("O1b evidence 路径无效") from None


def _verify_component_guard(guard: _ComponentGuard) -> None:
    try:
        for lexical, expected_resolved, expected_identity in guard.components:
            details = lexical.lstat()
            if _is_link_or_reparse(lexical):
                raise EvalInfrastructureError("O1b evidence 路径发生变化")
            if lexical.resolve(strict=True) != expected_resolved:
                raise EvalInfrastructureError("O1b evidence 路径发生变化")
            if _component_identity(details) != expected_identity:
                raise EvalInfrastructureError("O1b evidence 路径发生变化")
    except EvalInfrastructureError:
        raise
    except (FileNotFoundError, OSError, RuntimeError):
        raise EvalInfrastructureError("O1b evidence 路径发生变化") from None


def _component_snapshot(
    root: Path, path: Path
) -> tuple[tuple[Path, tuple[int, int, int, int, int], int], ...]:
    try:
        relative = path.relative_to(root)
        current = root
        snapshots: list[tuple[Path, tuple[int, int, int, int, int], int]] = []
        for part in relative.parts:
            current = current / part
            details = current.lstat()
            if _is_link_or_reparse(current):
                raise EvalInfrastructureError("哈希输入不得包含链接")
            snapshots.append((current, _file_signature(details), details.st_mode))
        return tuple(snapshots)
    except EvalInfrastructureError:
        raise
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise EvalInfrastructureError("哈希输入文件无效") from None


def _read_file_stably(path: Path) -> bytes:
    try:
        details = path.lstat()
        if _is_link_or_reparse(path) or not stat.S_ISREG(details.st_mode):
            raise EvalInfrastructureError("哈希输入必须是普通文件")
        expected = _file_signature(details)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_signature(opened) != expected:
                raise EvalInfrastructureError("哈希输入在打开期间发生变化")
            content = stream.read()
            finished = os.fstat(stream.fileno())
        if _file_signature(finished) != expected:
            raise EvalInfrastructureError("哈希输入在读取期间发生变化")
        return content
    except EvalInfrastructureError:
        raise
    except (OSError, UnicodeError):
        raise EvalInfrastructureError("哈希输入文件读取失败") from None


def _paths_matching(root: Path, pattern: str) -> list[Path]:
    try:
        paths = sorted(root.glob(pattern), key=lambda path: path.as_posix())
    except (OSError, RuntimeError):
        raise EvalInfrastructureError("哈希输入清单无效") from None
    if not paths:
        raise EvalInfrastructureError("哈希输入清单为空")
    return paths


def _production_manifest_paths(repository_root: Path) -> list[Path]:
    root = Path(repository_root)
    paths = _paths_matching(root, "code_operator/**/*.py")
    paths.append(root / "requirements.txt")
    return paths


def _production_tree_sha256(repository_root: Path | None = None) -> str:
    root = REPOSITORY_ROOT if repository_root is None else Path(repository_root)
    return _manifest_sha256(_production_manifest_paths(root), repository_root=root)


def _evaluator_manifest_paths(repository_root: Path) -> list[Path]:
    root = Path(repository_root)
    paths = [
        root / "evals" / "run_session_probe.py",
        root / "evals" / "run_golden.py",
        root / "evals" / "session_probe" / "turn1.txt",
        root / "evals" / "session_probe" / "turn2.txt",
        root / O1B_DESIGN_SPEC_RELATIVE,
    ]
    fixture = root / "evals" / "session_probe" / "project"
    try:
        fixture_paths = sorted(fixture.rglob("*"), key=lambda path: path.as_posix())
    except (OSError, RuntimeError):
        raise EvalInfrastructureError("哈希输入清单无效") from None
    for path in fixture_paths:
        relative_parts = path.relative_to(fixture).parts
        if any(part in RUNTIME_DIRECTORY_NAMES for part in relative_parts[:-1]):
            continue
        if relative_parts and path.suffix.lower() in RUNTIME_FILE_SUFFIXES:
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path)
    return paths


def _evaluator_protocol_sha256(repository_root: Path | None = None) -> str:
    root = REPOSITORY_ROOT if repository_root is None else Path(repository_root)
    return _manifest_sha256(_evaluator_manifest_paths(root), repository_root=root)


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _config_snapshot(config: ProviderConfig) -> dict[str, object]:
    """Return only reproducibility settings; credentials never enter this object."""
    return {
        "base_url": config.base_url,
        "model": config.model,
        "context_window": config.context_window,
        "max_output_tokens": config.max_output_tokens,
        "max_model_rounds": config.max_model_rounds,
        "max_tool_calls": config.max_tool_calls,
        "http_timeout_seconds": {
            "connect": 10.0,
            "read": 60.0,
            "write": 30.0,
            "pool": 10.0,
        },
        "test_command": ["python", "-m", "pytest", "-q"],
        "test_timeout_seconds": TEST_TIMEOUT_SECONDS,
        "ask_all": True,
        "auto_approve_tests": False,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "httpx_version": _distribution_version("httpx"),
        "pytest_version": _distribution_version("pytest"),
    }


def _freeze_probe_metadata(config: ProviderConfig) -> FrozenProbeMetadata:
    summary = validate_fixture()
    return FrozenProbeMetadata(
        fixture_sha256=summary.fixture_sha256,
        prompt_sha256=summary.prompt_sha256,
        target_initial_sha256=summary.target_initial_sha256,
        production_tree_sha256=_production_tree_sha256(),
        evaluator_protocol_sha256=_evaluator_protocol_sha256(),
        config=_config_snapshot(config),
    )


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    details = path.lstat()
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(reparse_point and attributes & reparse_point)


def _tree_entries(root: Path):
    """Yield descendants without following symlinks, junctions, or reparse points."""
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                details = entry.stat(follow_symlinks=False)
                link_or_reparse = _is_link_or_reparse(path)
                yield path, details, link_or_reparse
                if not link_or_reparse and stat.S_ISDIR(details.st_mode):
                    pending.append(path)


def _validate_source_tree(fixture_root: Path) -> None:
    if _is_link_or_reparse(fixture_root):
        raise EvalInfrastructureError("fixture tree symlink is not allowed")
    if not fixture_root.is_dir():
        raise EvalInfrastructureError("fixture tree root is not a directory")

    expected = {
        "greeting.py": False,
        "tests": True,
        "tests/test_greeting.py": False,
    }
    actual: set[str] = set()
    for path, _details, link_or_reparse in _tree_entries(fixture_root):
        if link_or_reparse:
            raise EvalInfrastructureError("fixture tree symlink is not allowed")
        relative = path.relative_to(fixture_root).as_posix()
        if any(part in RUNTIME_DIRECTORY_NAMES for part in relative.split("/")):
            raise EvalInfrastructureError("源 fixture 包含 runtime artifact")
        if path.is_file() and path.suffix.lower() in RUNTIME_FILE_SUFFIXES:
            raise EvalInfrastructureError("源 fixture 包含 runtime artifact")
        actual.add(relative)
        expected_directory = expected.get(relative)
        if expected_directory is None:
            raise EvalInfrastructureError("fixture tree entries are invalid")
        if path.is_dir() != expected_directory:
            raise EvalInfrastructureError("fixture tree entry types are invalid")
    if actual != set(expected):
        raise EvalInfrastructureError("fixture tree entries are invalid")


def validate_fixture(
    *,
    fixture_root: Path | None = None,
    turn1_path: Path | None = None,
    turn2_path: Path | None = None,
    runner: Callable[..., CommandResult] | None = None,
) -> FixtureSummary:
    """Validate the frozen project and its two prompt files without model calls."""
    fixture_root = FIXTURE_ROOT if fixture_root is None else Path(fixture_root)
    turn1_path = TURN1_PATH if turn1_path is None else Path(turn1_path)
    turn2_path = TURN2_PATH if turn2_path is None else Path(turn2_path)
    runner = run_process if runner is None else runner
    try:
        _validate_source_tree(fixture_root)
        if not turn1_path.is_file() or not turn2_path.is_file():
            raise EvalInfrastructureError("冻结 prompt 文件不存在")
        target = fixture_root / TARGET_RELATIVE_PATH
        if not target.is_file():
            raise EvalInfrastructureError("冻结 fixture 缺少 greeting.py")
        if not (fixture_root / TEST_RELATIVE_PATH).is_file():
            raise EvalInfrastructureError("冻结 fixture 缺少测试文件")

        with tempfile.TemporaryDirectory(prefix="code-operator-session-probe-") as raw:
            workspace = Path(raw) / "project"
            shutil.copytree(fixture_root, workspace)
            try:
                result = runner(
                    [sys.executable, "-m", "pytest", "-q"],
                    cwd=workspace,
                    timeout=TEST_TIMEOUT_SECONDS,
                )
            except Exception:
                raise EvalInfrastructureError(
                    "Session 探针测试进程执行失败"
                ) from None
        if not isinstance(result, CommandResult):
            raise EvalInfrastructureError("fixture 测试 runner 返回值无效")
        if result.timed_out:
            raise EvalInfrastructureError("冻结项目初始测试超时")
        if result.returncode != 1:
            raise EvalInfrastructureError("冻结项目初始测试必须恰好返回 1")

        return FixtureSummary(
            fixture_sha256=fixture_hash(fixture_root),
            prompt_sha256=_prompt_hash(turn1_path, turn2_path),
            initial_test_returncode=result.returncode,
            target_relative_path=TARGET_RELATIVE_PATH,
            target_initial_sha256=_sha256_file(target),
        )
    except EvalInfrastructureError:
        raise
    except (OSError, shutil.Error):
        raise EvalInfrastructureError("Session 探针 fixture 读取失败") from None


def _workspace_file_map(root: Path) -> dict[str, str]:
    """Return ordinary source-file hashes, excluding local runtime output."""
    result: dict[str, str] = {}
    for path, details, link_or_reparse in _tree_entries(root):
        if link_or_reparse or not stat.S_ISREG(details.st_mode):
            continue
        relative = path.relative_to(root)
        if (
            relative.parts[0] == ".git"
            or any(part in RUNTIME_DIRECTORY_NAMES for part in relative.parts[:-1])
        ):
            continue
        if path.suffix.lower() in RUNTIME_FILE_SUFFIXES:
            continue
        result[relative.as_posix()] = _sha256_file(path)
    return result


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    )


def _safe_changed_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    allowed = tuple(path for path in paths if path in ALLOWED_CHANGED_PATHS)
    return allowed + (("<unexpected-path>",) if len(allowed) != len(paths) else ())


def _workspace_manifest(root: Path) -> dict[str, _ManifestEntry]:
    manifest: dict[str, _ManifestEntry] = {}
    for path, details, link_or_reparse in _tree_entries(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            kind = "symlink"
        elif link_or_reparse:
            kind = "reparse"
        elif stat.S_ISDIR(details.st_mode):
            kind = "dir"
        elif stat.S_ISREG(details.st_mode):
            kind = "file"
        else:
            kind = "other"
        manifest[relative] = _ManifestEntry(
            kind=kind,
            size=details.st_size,
            mtime_ns=details.st_mtime_ns,
            ctime_ns=details.st_ctime_ns,
            identity=(details.st_dev, details.st_ino),
            sha256=_sha256_file(path) if kind == "file" else None,
        )
    return manifest


def _is_allowed_runtime_entry(relative: str, entry: _ManifestEntry) -> bool:
    parts = tuple(relative.split("/"))
    if parts == (".code-operator",):
        return entry.kind == "dir"
    if parts == (".code-operator", "audit.jsonl"):
        return entry.kind == "file"
    if parts == (".pytest_cache",):
        return entry.kind == "dir"
    if parts in {
        (".pytest_cache", ".gitignore"),
        (".pytest_cache", "CACHEDIR.TAG"),
        (".pytest_cache", "README.md"),
    }:
        return entry.kind == "file"
    if parts in {
        (".pytest_cache", "v"),
        (".pytest_cache", "v", "cache"),
    }:
        return entry.kind == "dir"
    if (
        len(parts) == 4
        and parts[:3] == (".pytest_cache", "v", "cache")
        and parts[3] in {"lastfailed", "nodeids", "stepwise"}
    ):
        return entry.kind == "file"
    if "__pycache__" in parts:
        return entry.kind == "dir" or (
            entry.kind == "file"
            and Path(parts[-1]).suffix.lower() in RUNTIME_FILE_SUFFIXES
        )
    return entry.kind == "file" and Path(parts[-1]).suffix.lower() in RUNTIME_FILE_SUFFIXES


def _has_link_or_reparse(manifest: dict[str, _ManifestEntry]) -> bool:
    return any(entry.kind in {"symlink", "reparse"} for entry in manifest.values())


def _first_manifest_is_valid(
    initial: dict[str, _ManifestEntry], current: dict[str, _ManifestEntry]
) -> bool:
    if _has_link_or_reparse(current):
        return False
    for relative, before in initial.items():
        after = current.get(relative)
        if after is None or after.kind != before.kind:
            return False
        if relative in ALLOWED_CHANGED_PATHS:
            if after.sha256 == before.sha256:
                return False
        elif after.sha256 != before.sha256:
            return False
    return all(
        relative in initial or _is_allowed_runtime_entry(relative, entry)
        for relative, entry in current.items()
    )


def _second_manifest_is_valid(
    before: dict[str, _ManifestEntry], after: dict[str, _ManifestEntry]
) -> bool:
    if _has_link_or_reparse(after) or before.keys() != after.keys():
        return False
    for relative, earlier in before.items():
        later = after[relative]
        if earlier.kind != later.kind:
            return False
        if _is_allowed_runtime_entry(relative, earlier):
            continue
        if earlier != later:
            return False
    return True


def _forbidden_session_artifact_count(
    initial: dict[str, _ManifestEntry], current: dict[str, _ManifestEntry]
) -> int:
    return sum(
        1
        for relative, entry in current.items()
        if relative not in initial and not _is_allowed_runtime_entry(relative, entry)
    )


def _approved_probe_command(
    argv: list[str], cwd: Path, *, workspace: Path
) -> bool:
    return (
        argv == ["python", "-m", "pytest", "-q"]
        and Path(cwd).resolve(strict=True) == workspace.resolve(strict=True)
    )


def _unexpected_command(events: tuple[_ToolEvent, ...]) -> bool:
    # Keep the frozen command approval boundary observable.  Cardinality and
    # ordinary tool failures belong to the primary policy below; an explicitly
    # denied command remains the older, stable failure classification.
    return any(
        event.name == "run_command"
        and event.error_code in {"COMMAND_DENIED", "USER_DENIED"}
        for event in events
    )


def _unexpected_write(events: tuple[_ToolEvent, ...], *, first_turn: bool) -> bool:
    writes = [
        event for event in events if event.name in {"write_file", "edit_file"}
    ]
    if first_turn:
        return len(writes) != 1 or not writes[0].ok
    return bool(writes)


def _first_tool_sequence_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    return (
        len(events) == 3
        and [event.name for event in events]
        == ["read_file", "edit_file", "run_command"]
        and all(event.ok for event in events)
    )


def _second_tool_sequence_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    return (
        len(events) == 1
        and events[0].name == "run_command"
        and events[0].ok
    )


_READ_ONLY_PROBE_TOOLS = frozenset({"read_file", "grep", "list_dir"})


def _first_primary_tool_policy_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    names = [event.name for event in events]
    allowed = _READ_ONLY_PROBE_TOOLS | {"edit_file", "run_command"}
    if not events or not all(event.ok for event in events):
        return False
    if any(name not in allowed for name in names):
        return False
    if names.count("edit_file") != 1 or names.count("run_command") != 1:
        return False
    edit_index = names.index("edit_file")
    command_index = names.index("run_command")
    return "read_file" in names[:edit_index] and edit_index < command_index


def _second_primary_tool_policy_is_valid(events: tuple[_ToolEvent, ...]) -> bool:
    names = [event.name for event in events]
    allowed = _READ_ONLY_PROBE_TOOLS | {"run_command"}
    return (
        bool(events)
        and all(event.ok for event in events)
        and all(name in allowed for name in names)
        and names.count("run_command") == 1
    )


def _reset_observability(session: AgentSession) -> tuple[int, int, int, int]:
    loop = session._loop
    messages = loop._messages
    reads = session._file_tools._complete_read_hashes
    journal = session._journal
    pending = session._pending_events
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[0], dict)
        or messages[0].get("role") != "system"
        or not isinstance(reads, dict)
        or not isinstance(pending, list)
        or not isinstance(journal.depth, int)
    ):
        raise RuntimeError("reset observability unavailable")
    return len(messages), len(reads), journal.depth, len(pending)


def _direct_subprocess_pids() -> frozenset[int]:
    parent_pid = os.getpid()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessEntry),
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessEntry),
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise OSError(ctypes.get_last_error(), "process snapshot failed")
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                raise OSError(ctypes.get_last_error(), "process enumeration failed")
            child_pids: set[int] = set()
            while True:
                if entry.th32ParentProcessID == parent_pid:
                    child_pids.add(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    error_code = ctypes.get_last_error()
                    if error_code != 18:
                        raise OSError(error_code, "process enumeration failed")
                    break
            return frozenset(child_pids)
        finally:
            if not kernel32.CloseHandle(snapshot):
                raise OSError(ctypes.get_last_error(), "process snapshot close failed")
    if sys.platform.startswith("linux"):
        proc_root = Path("/proc")
        child_pids: set[int] = set()
        for candidate in proc_root.iterdir():
            if not candidate.name.isdecimal():
                continue
            try:
                raw = (candidate / "stat").read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            closing = raw.rfind(")")
            fields = raw[closing + 2 :].split()
            if closing < 0 or len(fields) < 2:
                raise OSError("invalid proc stat")
            if int(fields[1]) == parent_pid:
                child_pids.add(int(candidate.name))
        return frozenset(child_pids)
    raise OSError("unsupported process scan platform")


def _build_report(
    *,
    mode: str,
    outcome: str,
    failure_code: str | None,
    summary: FixtureSummary | None,
    attempt_index: int | None = None,
    frozen_metadata: FrozenProbeMetadata | None = None,
    target_after_turn1: str | None = None,
    target_after_undo: str | None = None,
    turn_statuses: tuple[str, ...] = (),
    model_rounds: tuple[int, ...] = (),
    tool_calls: tuple[int, ...] = (),
    provider_total_tokens: tuple[int | None, ...] = (),
    changed_paths_after_turn1: tuple[str, ...] = (),
    tests_after_turn1_returncode: int | None = None,
    tests_after_turn2_returncode: int | None = None,
    undo_ok: bool = False,
    undo_depth_after: int = 0,
    reset_undo_depth: int = 0,
    reset_pending_events: int = 0,
    reset_history_message_count: int | None = None,
    reset_read_hash_count: int | None = None,
    close_idempotent: bool = False,
    owned_client_close_calls: int | None = None,
    baseline_direct_subprocess_count: int | None = None,
    new_residual_direct_subprocess_count: int | None = None,
    session_artifact_count: int = 0,
    turn1_ideal_trace: bool | None = None,
    turn2_ideal_trace: bool | None = None,
    ideal_trace_overall: bool | None = None,
    turn2_exact_value_observed: bool | None = None,
    started: float,
) -> ProbeReport:
    return ProbeReport(
        schema_version=2,
        probe_id=PROBE_ID,
        mode=mode,
        protocol_version=(
            O1B_PROTOCOL_VERSION if frozen_metadata is not None else None
        ),
        attempt_index=attempt_index,
        production_tree_sha256=(
            None if frozen_metadata is None else frozen_metadata.production_tree_sha256
        ),
        evaluator_protocol_sha256=(
            None if frozen_metadata is None else frozen_metadata.evaluator_protocol_sha256
        ),
        config=None if frozen_metadata is None else dict(frozen_metadata.config),
        outcome=outcome,
        failure_code=failure_code,
        fixture_sha256=None if summary is None else summary.fixture_sha256,
        prompt_sha256=None if summary is None else summary.prompt_sha256,
        target_initial_sha256=None if summary is None else summary.target_initial_sha256,
        target_after_turn1_sha256=target_after_turn1,
        target_after_undo_sha256=target_after_undo,
        turn_statuses=turn_statuses,
        model_rounds=model_rounds,
        tool_calls=tool_calls,
        provider_total_tokens=provider_total_tokens,
        changed_paths_after_turn1=_safe_changed_paths(changed_paths_after_turn1),
        tests_after_turn1_returncode=tests_after_turn1_returncode,
        tests_after_turn2_returncode=tests_after_turn2_returncode,
        undo_ok=undo_ok,
        undo_depth_after=undo_depth_after,
        reset_undo_depth=reset_undo_depth,
        reset_pending_events=reset_pending_events,
        reset_history_message_count=reset_history_message_count,
        reset_read_hash_count=reset_read_hash_count,
        close_idempotent=close_idempotent,
        owned_client_close_calls=owned_client_close_calls,
        baseline_direct_subprocess_count=baseline_direct_subprocess_count,
        new_residual_direct_subprocess_count=new_residual_direct_subprocess_count,
        session_artifact_count=session_artifact_count,
        elapsed_seconds=max(0.0, time.monotonic() - started),
        turn1_ideal_trace=turn1_ideal_trace,
        turn2_ideal_trace=turn2_ideal_trace,
        ideal_trace_overall=ideal_trace_overall,
        turn2_exact_value_observed=turn2_exact_value_observed,
    )


def run_probe(
    *,
    fixture_root: Path,
    turn1: str,
    turn2: str,
    config: ProviderConfig,
    client: ModelLike | None,
    mode: str,
    attempt_index: int | None = None,
    frozen_metadata: FrozenProbeMetadata | None = None,
) -> ProbeReport:
    """Run the frozen two-turn stateful session probe in a temporary workspace."""
    started = time.monotonic()
    try:
        expected_turn1 = TURN1_PATH.read_text(encoding="utf-8")
        expected_turn2 = TURN2_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise EvalInfrastructureError("Session 探针冻结 prompt 读取失败") from None
    if turn1 != expected_turn1 or turn2 != expected_turn2:
        raise EvalInfrastructureError("Session 探针 prompt 不匹配") from None

    summary = validate_fixture(fixture_root=fixture_root)
    statuses: list[str] = []
    rounds: list[int] = []
    calls: list[int] = []
    tokens: list[int | None] = []
    changed_after_turn1: tuple[str, ...] = ()
    tests_after_turn1: int | None = None
    tests_after_turn2: int | None = None
    target_after_turn1: str | None = None
    target_after_undo: str | None = None
    undo_ok = False
    undo_depth_after = 0
    reset_undo_depth = 0
    reset_pending_events = 0
    reset_history_message_count: int | None = None
    reset_read_hash_count: int | None = None
    close_idempotent = False
    owned_client_close_calls: int | None = None
    owned_close_counter: list[int] | None = None
    baseline_direct_subprocess_count: int | None = None
    new_residual_direct_subprocess_count: int | None = None
    baseline_direct_subprocess_pids: frozenset[int] | None = None
    artifact_count = 0
    turn1_ideal_trace: bool | None = None
    turn2_ideal_trace: bool | None = None
    ideal_trace_overall: bool | None = None
    turn2_exact_value_observed: bool | None = None
    session: AgentSession | None = None
    trace = _ProbeTrace()

    def _report(**values: object) -> ProbeReport:
        """Finalize Session ownership evidence before freezing any report."""
        nonlocal close_idempotent
        nonlocal owned_client_close_calls
        nonlocal new_residual_direct_subprocess_count

        lifecycle_failure: str | None = None
        if session is not None:
            close_failed = False
            for _attempt in range(2):
                try:
                    session.close()
                except Exception:
                    close_failed = True
            close_idempotent = not close_failed
            if owned_close_counter is not None:
                owned_client_close_calls = owned_close_counter[0]
                if owned_client_close_calls != 1 and not close_failed:
                    values["outcome"] = "FAIL"
                    values["failure_code"] = "OWNED_CLIENT_CLOSE_INVALID"
            if close_failed:
                lifecycle_failure = "PROBE_RUNTIME_ERROR"

            try:
                final_direct_subprocess_pids = _direct_subprocess_pids()
                if not isinstance(final_direct_subprocess_pids, frozenset) or any(
                    not isinstance(pid, int) or isinstance(pid, bool)
                    for pid in final_direct_subprocess_pids
                ):
                    raise ValueError("invalid subprocess scan result")
                if (
                    baseline_direct_subprocess_count is None
                    or baseline_direct_subprocess_pids is None
                ):
                    raise ValueError("baseline subprocess scan unavailable")
                # Only the count is retained; PID values never enter report data.
                new_residual_direct_subprocess_count = len(
                    final_direct_subprocess_pids
                    - baseline_direct_subprocess_pids
                )
            except Exception:
                if lifecycle_failure is None:
                    lifecycle_failure = "SUBPROCESS_SCAN_FAILED"
            if (
                new_residual_direct_subprocess_count not in (None, 0)
                and lifecycle_failure is None
                and values.get("outcome") != "INVALID_INFRA"
            ):
                values["outcome"] = "FAIL"
                values["failure_code"] = "NEW_RESIDUAL_DIRECT_SUBPROCESS"

        if lifecycle_failure is not None:
            values["outcome"] = "INVALID_INFRA"
            values["failure_code"] = lifecycle_failure
        values.update(
            attempt_index=attempt_index,
            frozen_metadata=frozen_metadata,
            turn1_ideal_trace=turn1_ideal_trace,
            turn2_ideal_trace=turn2_ideal_trace,
            ideal_trace_overall=ideal_trace_overall,
            turn2_exact_value_observed=turn2_exact_value_observed,
            reset_undo_depth=reset_undo_depth,
            reset_pending_events=reset_pending_events,
            reset_history_message_count=reset_history_message_count,
            reset_read_hash_count=reset_read_hash_count,
            close_idempotent=close_idempotent,
            owned_client_close_calls=owned_client_close_calls,
            baseline_direct_subprocess_count=baseline_direct_subprocess_count,
            new_residual_direct_subprocess_count=new_residual_direct_subprocess_count,
        )
        return _build_report(**values)  # type: ignore[arg-type]

    try:
        with tempfile.TemporaryDirectory(prefix="code-operator-session-probe-") as raw:
            workspace = Path(raw) / "project"
            shutil.copytree(fixture_root, workspace)
            initial_files = _workspace_file_map(workspace)
            initial_manifest = _workspace_manifest(workspace)
            target = workspace / TARGET_RELATIVE_PATH
            try:
                baseline_direct_subprocess_pids = _direct_subprocess_pids()
                if not isinstance(baseline_direct_subprocess_pids, frozenset) or any(
                    not isinstance(pid, int) or isinstance(pid, bool)
                    for pid in baseline_direct_subprocess_pids
                ):
                    raise ValueError("invalid subprocess scan result")
                baseline_direct_subprocess_count = len(baseline_direct_subprocess_pids)
            except Exception:
                return _report(
                    mode=mode,
                    outcome="INVALID_INFRA",
                    failure_code="SUBPROCESS_SCAN_FAILED",
                    summary=summary,
                    started=started,
                )
            session = AgentSession(
                config,
                workspace=workspace,
                approve=lambda argv, cwd: _approved_probe_command(
                    argv, cwd, workspace=workspace
                ),
                client=client,
                ask_all=True,
                auto_approve_tests=False,
                trace=trace,
            )
            if client is None:
                try:
                    owned_client = session._owned_client
                    if owned_client is None:
                        raise RuntimeError("owned client unavailable")
                    original_close = owned_client.close
                    owned_close_counter = [0]

                    def counted_close() -> None:
                        assert owned_close_counter is not None
                        owned_close_counter[0] += 1
                        original_close()

                    owned_client.close = counted_close
                except Exception:
                    return _report(
                        mode=mode, outcome="INVALID_INFRA",
                        failure_code="OWNED_CLIENT_OBSERVABILITY_UNAVAILABLE",
                        summary=summary, started=started,
                    )
            trace.start_turn()
            first = session.run(turn1)
            statuses.append(first.status)
            rounds.append(first.model_rounds)
            calls.append(first.tool_calls)
            tokens.append(first.provider_total_tokens)
            turn1_ideal_trace = _first_tool_sequence_is_valid(trace.events(0))
            if first.status in {"PROVIDER_ERROR", "PROVIDER_PROTOCOL_ERROR"}:
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code=first.status,
                    summary=summary, turn_statuses=tuple(statuses),
                    model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), started=started,
                )
            if first.status != "COMPLETED":
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN1_NOT_COMPLETED",
                    summary=summary, turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens), started=started,
                )
            if _unexpected_command(trace.events(0)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="UNEXPECTED_COMMAND",
                    summary=summary, turn_statuses=tuple(statuses),
                    model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), started=started,
                )
            if not _first_primary_tool_policy_is_valid(trace.events(0)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="PRIMARY_TOOL_POLICY_FAILED",
                    summary=summary, turn_statuses=tuple(statuses),
                    model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), started=started,
                )

            first_files = _workspace_file_map(workspace)
            first_agent_manifest = _workspace_manifest(workspace)
            changed_after_turn1 = _changed_paths(initial_files, first_files)
            target_after_turn1 = _sha256_file(target) if target.is_file() else None
            if _has_link_or_reparse(first_agent_manifest):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="WORKSPACE_MANIFEST_CHANGED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1, started=started,
                )
            if changed_after_turn1 != tuple(sorted(ALLOWED_CHANGED_PATHS)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN1_CHANGE_SCOPE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    started=started,
                )
            if not _first_manifest_is_valid(initial_manifest, first_agent_manifest):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="WORKSPACE_MANIFEST_CHANGED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1, started=started,
                )
            try:
                first_test = run_process(
                    [sys.executable, "-m", "pytest", "-q"], cwd=workspace,
                    timeout=TEST_TIMEOUT_SECONDS,
                )
            except Exception:
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code="PROCESS_FAILURE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    started=started,
                )
            tests_after_turn1 = first_test.returncode
            if first_test.timed_out:
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code="PROCESS_FAILURE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            if first_test.returncode != 0:
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN1_TESTS_FAILED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )

            first_manifest = _workspace_manifest(workspace)
            if not _first_manifest_is_valid(initial_manifest, first_manifest):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="WORKSPACE_MANIFEST_CHANGED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            depth_after_turn1 = session.undo_depth
            trace.start_turn()
            second = session.run(turn2)
            statuses.append(second.status)
            rounds.append(second.model_rounds)
            calls.append(second.tool_calls)
            tokens.append(second.provider_total_tokens)
            turn2_ideal_trace = _second_tool_sequence_is_valid(trace.events(1))
            if turn1_ideal_trace is not None and turn2_ideal_trace is not None:
                ideal_trace_overall = turn1_ideal_trace and turn2_ideal_trace
            if second.status in {"PROVIDER_ERROR", "PROVIDER_PROTOCOL_ERROR"}:
                turn2_exact_value_observed = "你好，小明！" in second.final_text
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code=second.status,
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            if second.status != "COMPLETED":
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN2_NOT_COMPLETED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            turn2_exact_value_observed = "你好，小明！" in second.final_text
            if _unexpected_command(trace.events(1)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="UNEXPECTED_COMMAND",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            if not turn2_exact_value_observed:
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN2_EXACT_VALUE_MISSING",
                    summary=summary, turn1_ideal_trace=turn1_ideal_trace,
                    turn2_ideal_trace=turn2_ideal_trace,
                    ideal_trace_overall=ideal_trace_overall,
                    turn2_exact_value_observed=turn2_exact_value_observed,
                    target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            if not _second_primary_tool_policy_is_valid(trace.events(1)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="PRIMARY_TOOL_POLICY_FAILED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            if (
                not _second_manifest_is_valid(first_manifest, _workspace_manifest(workspace))
                or session.undo_depth != depth_after_turn1
            ):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="WORKSPACE_MANIFEST_CHANGED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            try:
                second_test = run_process(
                    [sys.executable, "-m", "pytest", "-q"], cwd=workspace,
                    timeout=TEST_TIMEOUT_SECONDS,
                )
            except Exception:
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code="PROCESS_FAILURE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1, started=started,
                )
            tests_after_turn2 = second_test.returncode
            if second_test.timed_out:
                return _report(
                    mode=mode, outcome="INVALID_INFRA", failure_code="PROCESS_FAILURE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, started=started,
                )
            if second_test.returncode != 0:
                return _report(
                    mode=mode, outcome="FAIL", failure_code="TURN2_TESTS_FAILED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, started=started,
                )

            if not _second_manifest_is_valid(first_manifest, _workspace_manifest(workspace)):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="WORKSPACE_MANIFEST_CHANGED",
                    summary=summary, target_after_turn1=target_after_turn1,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds),
                    tool_calls=tuple(calls), provider_total_tokens=tuple(tokens),
                    changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, started=started,
                )

            undone = session.undo()
            undo_ok = undone.ok
            undo_depth_after = session.undo_depth
            target_after_undo = _sha256_file(target) if target.is_file() else None
            if not undo_ok or target_after_undo != summary.target_initial_sha256 or undo_depth_after != 0:
                return _report(
                    mode=mode, outcome="FAIL", failure_code="UNDO_NOT_RESTORED",
                    summary=summary, target_after_turn1=target_after_turn1, target_after_undo=target_after_undo,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
                    undo_depth_after=undo_depth_after, started=started,
                )
            session.reset()
            try:
                (
                    reset_history_message_count,
                    reset_read_hash_count,
                    reset_undo_depth,
                    reset_pending_events,
                ) = _reset_observability(session)
            except Exception:
                return _report(
                    mode=mode, outcome="INVALID_INFRA",
                    failure_code="RESET_OBSERVABILITY_UNAVAILABLE",
                    summary=summary, target_after_turn1=target_after_turn1,
                    target_after_undo=target_after_undo, turn_statuses=tuple(statuses),
                    model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
                    undo_depth_after=undo_depth_after, started=started,
                )
            if (
                reset_history_message_count != 1
                or reset_read_hash_count != 0
                or reset_undo_depth != 0
                or reset_pending_events != 0
            ):
                return _report(
                    mode=mode, outcome="FAIL", failure_code="RESET_NOT_CLEAN",
                    summary=summary, target_after_turn1=target_after_turn1, target_after_undo=target_after_undo,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
                    undo_depth_after=undo_depth_after, reset_undo_depth=reset_undo_depth,
                    reset_pending_events=reset_pending_events,
                    reset_history_message_count=reset_history_message_count,
                    reset_read_hash_count=reset_read_hash_count, started=started,
                )
            artifact_count = _forbidden_session_artifact_count(
                initial_manifest, _workspace_manifest(workspace)
            )
            if artifact_count:
                return _report(
                    mode=mode, outcome="FAIL", failure_code="SESSION_ARTIFACT_FORBIDDEN",
                    summary=summary, target_after_turn1=target_after_turn1, target_after_undo=target_after_undo,
                    turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                    provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                    tests_after_turn1_returncode=tests_after_turn1,
                    tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
                    undo_depth_after=undo_depth_after, reset_undo_depth=reset_undo_depth,
                    reset_pending_events=reset_pending_events,
                    reset_history_message_count=reset_history_message_count,
                    reset_read_hash_count=reset_read_hash_count,
                    close_idempotent=close_idempotent,
                    owned_client_close_calls=owned_client_close_calls,
                    baseline_direct_subprocess_count=baseline_direct_subprocess_count,
                    new_residual_direct_subprocess_count=new_residual_direct_subprocess_count,
                    session_artifact_count=artifact_count, started=started,
                )
            return _report(
                mode=mode, outcome="PASS", failure_code=None, summary=summary,
                target_after_turn1=target_after_turn1, target_after_undo=target_after_undo,
                turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
                provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
                tests_after_turn1_returncode=tests_after_turn1,
                tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
                undo_depth_after=undo_depth_after, reset_undo_depth=reset_undo_depth,
                reset_pending_events=reset_pending_events,
                reset_history_message_count=reset_history_message_count,
                reset_read_hash_count=reset_read_hash_count,
                close_idempotent=close_idempotent,
                owned_client_close_calls=owned_client_close_calls,
                baseline_direct_subprocess_count=baseline_direct_subprocess_count,
                new_residual_direct_subprocess_count=new_residual_direct_subprocess_count,
                session_artifact_count=artifact_count, started=started,
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return _report(
            mode=mode, outcome="INVALID_INFRA", failure_code="PROBE_RUNTIME_ERROR",
            summary=summary, target_after_turn1=target_after_turn1, target_after_undo=target_after_undo,
            turn_statuses=tuple(statuses), model_rounds=tuple(rounds), tool_calls=tuple(calls),
            provider_total_tokens=tuple(tokens), changed_paths_after_turn1=changed_after_turn1,
            tests_after_turn1_returncode=tests_after_turn1,
            tests_after_turn2_returncode=tests_after_turn2, undo_ok=undo_ok,
            undo_depth_after=undo_depth_after, reset_undo_depth=reset_undo_depth,
            reset_pending_events=reset_pending_events, close_idempotent=close_idempotent,
            session_artifact_count=artifact_count, started=started,
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


class _ProbeArgumentParser(argparse.ArgumentParser):
    """Parser that keeps the two operational modes unambiguous."""

    def error(self, _message: str) -> None:
        # argparse normally includes the unrecognized token sequence in this
        # message.  CLI arguments can contain credentials, so never echo it.
        self._print_message("invalid command-line arguments\n", sys.stderr)
        raise SystemExit(2)

    def parse_args(
        self, args: Sequence[str] | None = None, namespace: object | None = None
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if parsed.validate_fixture and any(
            value is not None
            for value in (parsed.report, parsed.attempt, parsed.reservation, parsed.summary, parsed.stop_reason)
        ):
            self.error("--validate-fixture 参数组合无效")
        if parsed.real:
            if parsed.report is None:
                self.error("--real 必须同时提供 --report")
            o1b_args = (parsed.attempt, parsed.reservation, parsed.summary, parsed.stop_reason)
            if any(value is not None for value in o1b_args) and (
                parsed.attempt is None or parsed.reservation is None or parsed.report is None
                or parsed.summary is not None or parsed.stop_reason is not None
            ):
                self.error("--real 的 O1b 参数不完整")
        if parsed.summarize and any(
            value is not None for value in (parsed.report, parsed.attempt, parsed.reservation)
        ):
            self.error("--summarize 参数组合无效")
        if parsed.summarize and (parsed.summary is None or parsed.stop_reason is None):
            self.error("--summarize 必须同时提供 --summary 和 --stop-reason")
        if parsed.summarize and parsed.stop_reason not in O1B_STOP_REASONS:
            self.error("--summarize 的 stop reason 无效")
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _ProbeArgumentParser(description="运行冻结的双轮会话探针。")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-fixture", action="store_true")
    modes.add_argument("--real", action="store_true")
    modes.add_argument("--summarize", action="store_true")
    parser.add_argument("--report", type=Path, help="唯一允许的探针 JSON 报告路径")
    parser.add_argument("--attempt", type=int, help="O1b 固定 attempt 编号")
    parser.add_argument("--reservation", type=Path, help="O1b 固定 reservation 路径")
    parser.add_argument("--summary", type=Path, help="O1b 固定 summary 路径")
    parser.add_argument("--stop-reason", help="O1b 固定停止原因")
    return parser


def _require_real_directory(path: Path) -> None:
    try:
        if _is_link_or_reparse(path) or not path.is_dir():
            raise EvalInfrastructureError("报告目录不安全")
    except OSError:
        raise EvalInfrastructureError("报告目录不安全") from None


def _entry_is_link_or_reparse(path: Path) -> bool:
    """Inspect an entry with lstat, treating only an absent entry as absent."""
    try:
        return _is_link_or_reparse(path)
    except FileNotFoundError:
        return False
    except OSError:
        raise EvalInfrastructureError("报告路径无效") from None


def _directory_snapshot(path: Path) -> tuple[Path, tuple[int, int, int]]:
    _require_real_directory(path)
    try:
        details = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise EvalInfrastructureError("报告目录不安全") from None
    return resolved, (details.st_dev, details.st_ino, details.st_ctime_ns)


def resolve_report_path(
    raw: Path, *, repository_root: Path | None = None
) -> Path:
    """Accept precisely the committed evidence destination under a real tree."""
    raw = Path(raw)
    if raw.is_absolute() or raw.parts != REPORT_RELATIVE_PATH.parts:
        raise EvalInfrastructureError("报告路径无效")
    try:
        root = (REPOSITORY_ROOT if repository_root is None else Path(repository_root)).resolve(
            strict=True
        )
    except OSError:
        raise EvalInfrastructureError("报告路径无效") from None
    _require_real_directory(root)
    current = root
    for part in REPORT_RELATIVE_PATH.parts[:-1]:
        current = current / part
        _require_real_directory(current)
    destination = root.joinpath(*REPORT_RELATIVE_PATH.parts)
    if _entry_is_link_or_reparse(destination):
        raise EvalInfrastructureError("报告路径无效")
    return destination


def _contains_value(value: object, expected: str) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_value(key, expected) or _contains_value(item, expected)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_value(item, expected) for item in value)
    return isinstance(value, str) and expected in value


def _contains_raw_absolute_path(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_raw_absolute_path(key) or _contains_raw_absolute_path(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_raw_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    without_urls = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"']+", "", value)
    return bool(_RAW_ABSOLUTE_PATH_PATTERN.search(without_urls))


def _report_payload_is_safe(encoded: str, *, api_key: str) -> bool:
    prompts = (TURN1_PATH.read_text(encoding="utf-8"), TURN2_PATH.read_text(encoding="utf-8"))
    without_urls = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"']+", "", encoded)
    return not (
        (api_key and api_key in encoded)
        or _UNREDACTED_BEARER_PATTERN.search(encoded)
        or _PRIVATE_KEY_PATTERN.search(encoded)
        or any(prompt in encoded for prompt in prompts)
        or _ABSOLUTE_PATH_PATTERN.search(without_urls)
    )


def _verify_written_report(
    path: Path, *, expected_parent: Path, expected_identity: tuple[int, int, int]
) -> None:
    current_parent, current_identity = _directory_snapshot(path.parent)
    if current_parent != expected_parent or current_identity != expected_identity:
        raise EvalInfrastructureError("报告目录在写入期间发生变化")
    if _entry_is_link_or_reparse(path):
        raise EvalInfrastructureError("报告路径无效")
    try:
        resolved_report = path.resolve(strict=True)
    except OSError:
        raise EvalInfrastructureError("报告写入验证失败") from None
    if resolved_report.parent != expected_parent:
        raise EvalInfrastructureError("报告写入验证失败")


def write_probe_report_exclusive(
    path: Path, report: dict[str, object], *, api_key: str
) -> None:
    """Refuse unsafe reports before delegating exclusive creation to the shared helper."""
    frozen_prompts = (
        TURN1_PATH.read_text(encoding="utf-8"),
        TURN2_PATH.read_text(encoding="utf-8"),
    )
    if (api_key and _contains_value(report, api_key)) or any(
        _contains_value(report, prompt) for prompt in frozen_prompts
    ) or _contains_raw_absolute_path(report):
        raise EvalInfrastructureError("报告内容不安全")
    try:
        expected_parent, expected_identity = _directory_snapshot(path.parent)
        if path.exists() or _entry_is_link_or_reparse(path):
            raise EvalInfrastructureError("报告文件已存在，拒绝覆盖")
        redacted = Redactor([api_key]).redact_object(report)
        encoded = json.dumps(redacted, ensure_ascii=False)
    except EvalInfrastructureError:
        raise
    except (OSError, TypeError, ValueError):
        raise EvalInfrastructureError("报告内容不安全") from None
    if not _report_payload_is_safe(encoded, api_key=api_key):
        raise EvalInfrastructureError("报告内容不安全")
    # Recheck the path immediately before the shared atomic exclusive writer.
    if _directory_snapshot(path.parent) != (expected_parent, expected_identity):
        raise EvalInfrastructureError("报告目录在写入期间发生变化")
    if path.exists() or _entry_is_link_or_reparse(path):
        raise EvalInfrastructureError("报告文件已存在，拒绝覆盖")
    write_report_exclusive(path, redacted, api_key=api_key)
    # If verification cannot prove the same parent and target ownership, do
    # not unlink through this mutable lexical path: it may now name a victim
    # placed by an attacker.  A possible residue is safer than deleting it.
    _verify_written_report(
        path,
        expected_parent=expected_parent,
        expected_identity=expected_identity,
    )


def _write_json_exclusive_verified(
    path: Path,
    payload: dict[str, object],
    *,
    api_key: str = "",
    evidence_guard: _ComponentGuard | None = None,
) -> None:
    """Canonicalize a JSON-compatible object, write it once, then verify it."""
    try:
        canonical = json.loads(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        if not isinstance(canonical, dict):
            raise EvalInfrastructureError("JSON 写入验证失败")
        expected = Redactor([api_key]).redact_object(canonical)
        if not isinstance(expected, dict):
            raise EvalInfrastructureError("JSON 写入验证失败")
        if evidence_guard is not None:
            _verify_component_guard(evidence_guard)
        # Keep a final guard check immediately adjacent to the delegated
        # writer; the earlier validation may have observed a replaced parent.
        if evidence_guard is not None:
            _verify_component_guard(evidence_guard)
        write_probe_report_exclusive(path, canonical, api_key=api_key)
        written = json.loads(path.read_text(encoding="utf-8"))
        if evidence_guard is not None:
            _verify_component_guard(evidence_guard)
    except EvalInfrastructureError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise EvalInfrastructureError("JSON 写入验证失败") from None
    if not isinstance(written, dict) or written != expected:
        raise EvalInfrastructureError("JSON 写入验证失败")


def _o1b_production_evidence_root(root: Path) -> Path:
    repository = Path(REPOSITORY_ROOT)
    expected_lexical = (repository / O1B_REPORT_PATHS[1].parent).absolute()
    candidate_lexical = Path(root).absolute()
    if os.path.normcase(str(candidate_lexical)) != os.path.normcase(
        str(expected_lexical)
    ):
        raise EvalInfrastructureError("O1b evidence 目录不安全")
    current = repository
    try:
        if _is_link_or_reparse(current):
            raise EvalInfrastructureError("O1b evidence 目录不安全")
        for part in O1B_REPORT_PATHS[1].parent.parts:
            current = current / part
            if _is_link_or_reparse(current) or not current.is_dir():
                raise EvalInfrastructureError("O1b evidence 目录不安全")
        resolved = candidate_lexical.resolve(strict=True)
        if resolved != expected_lexical.resolve(strict=True):
            raise EvalInfrastructureError("O1b evidence 目录不安全")
        return resolved
    except EvalInfrastructureError:
        raise
    except (FileNotFoundError, OSError, RuntimeError):
        raise EvalInfrastructureError("O1b evidence 目录不安全") from None


def _o1b_evidence_root(root: Path, *, test_root: Path | None = None) -> Path:
    candidate = Path(root)
    try:
        if _is_link_or_reparse(candidate) or not candidate.is_dir():
            raise EvalInfrastructureError("O1b evidence 目录不安全")
        resolved = candidate.resolve(strict=True)
        if test_root is None:
            return _o1b_production_evidence_root(candidate)
        trusted = Path(test_root)
        if _is_link_or_reparse(trusted) or not trusted.is_dir():
            raise EvalInfrastructureError("O1b evidence 目录不安全")
        if resolved != trusted.resolve(strict=True):
            raise EvalInfrastructureError("O1b evidence 目录不安全")
        return resolved
    except EvalInfrastructureError:
        raise
    except (FileNotFoundError, OSError, RuntimeError):
        raise EvalInfrastructureError("O1b evidence 目录不安全") from None


def _o1b_evidence_guard(
    root: Path, *, test_root: Path | None = None
) -> tuple[Path, _ComponentGuard]:
    resolved = _o1b_evidence_root(root, test_root=test_root)
    if test_root is None:
        guard = _capture_component_guard(Path(REPOSITORY_ROOT), resolved)
    else:
        guard = _capture_component_guard(resolved, resolved)
    return resolved, guard


def _o1b_expected_path(root: Path, attempt_index: int, *, reservation: bool) -> Path:
    expected = (
        O1B_RESERVATION_PATHS[attempt_index]
        if reservation
        else O1B_REPORT_PATHS[attempt_index]
    )
    repository_root = REPOSITORY_ROOT.resolve()
    return root / expected if root == repository_root else root / expected.name


def _validate_o1b_path(
    path: Path, *, root: Path, attempt_index: int, reservation: bool
) -> Path:
    expected = _o1b_expected_path(root, attempt_index, reservation=reservation)
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        raise EvalInfrastructureError("O1b 路径无效") from None
    if resolved != expected:
        raise EvalInfrastructureError("O1b 路径无效")
    if _entry_is_link_or_reparse(Path(path)):
        raise EvalInfrastructureError("O1b 路径无效")
    return expected


def _validate_o1b_previous_reservation(
    path: Path,
    *,
    expected_index: int,
    expected_metadata: FrozenProbeMetadata,
    evidence_guard: _ComponentGuard | None = None,
) -> None:
    try:
        reservation_guard = _capture_component_guard(path.parent, path)
        if evidence_guard is not None:
            _verify_component_guard(evidence_guard)
        _verify_component_guard(reservation_guard)
        payload = json.loads(_read_file_stably(path).decode("utf-8"))
        _verify_component_guard(reservation_guard)
        if evidence_guard is not None:
            _verify_component_guard(evidence_guard)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise EvalInfrastructureError("O1b 前序 reservation 无效") from None
    if not isinstance(payload, dict):
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    if set(payload) != {"protocol_version", "attempt_index", "created_at", "metadata"}:
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    if payload.get("protocol_version") != O1B_PROTOCOL_VERSION:
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    if payload.get("attempt_index") != expected_index or isinstance(
        payload.get("attempt_index"), bool
    ):
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    if not isinstance(payload.get("created_at"), str) or not payload["created_at"]:
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise EvalInfrastructureError("O1b 前序 reservation 无效")
    expected = expected_metadata.to_dict()
    if set(metadata) != set(expected) or metadata != expected:
        raise EvalInfrastructureError("O1b 前序 reservation 无效")


def reserve_then_run_real_attempt(
    *,
    attempt_index: int,
    evidence_root: Path,
    reservation_path: Path,
    report_path: Path,
    config: ProviderConfig,
    _test_evidence_root: Path | None = None,
) -> ProbeReport:
    """Reserve one fixed O1b attempt before entering the model-backed runner."""
    if attempt_index not in O1B_REPORT_PATHS:
        raise EvalInfrastructureError("O1b attempt 编号无效")
    root, evidence_guard = _o1b_evidence_guard(
        Path(evidence_root), test_root=_test_evidence_root
    )
    reservation = _validate_o1b_path(
        Path(reservation_path), root=root, attempt_index=attempt_index, reservation=True
    )
    report = _validate_o1b_path(
        Path(report_path), root=root, attempt_index=attempt_index, reservation=False
    )

    if report.exists() or _entry_is_link_or_reparse(report):
        raise EvalInfrastructureError("O1b 报告文件已存在，拒绝覆盖")
    if reservation.exists() or _entry_is_link_or_reparse(reservation):
        raise EvalInfrastructureError("O1b reservation 已存在，拒绝复用")
    metadata = _freeze_probe_metadata(config)
    for previous_index in range(1, attempt_index):
        previous = _o1b_expected_path(root, previous_index, reservation=True)
        if _entry_is_link_or_reparse(previous) or not previous.is_file():
            raise EvalInfrastructureError("O1b reservation 编号不连续")
        _validate_o1b_previous_reservation(
            previous,
            expected_index=previous_index,
            expected_metadata=metadata,
            evidence_guard=evidence_guard,
        )
    frozen_reservation = AttemptReservation(
        protocol_version=O1B_PROTOCOL_VERSION,
        attempt_index=attempt_index,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        metadata=metadata,
    )
    _write_json_exclusive_verified(
        reservation,
        frozen_reservation.to_dict(),
        evidence_guard=evidence_guard,
    )
    # Do not create a client or invoke any network-capable code until the
    # reservation has been read back and structurally verified above.
    try:
        _verify_component_guard(evidence_guard)
        frozen_turn1 = TURN1_PATH.read_text(encoding="utf-8")
        frozen_turn2 = TURN2_PATH.read_text(encoding="utf-8")
        _verify_component_guard(evidence_guard)
    except (OSError, UnicodeError):
        raise EvalInfrastructureError("O1b 冻结 prompt 读取失败") from None
    result = run_probe(
        fixture_root=FIXTURE_ROOT,
        turn1=frozen_turn1,
        turn2=frozen_turn2,
        config=config,
        client=None,
        mode="real",
        attempt_index=attempt_index,
        frozen_metadata=metadata,
    )
    return result


def _classify_o1b(*, valid_attempts: int, primary_passes: int) -> str:
    if valid_attempts < 2:
        return "O1B_INCONCLUSIVE"
    if primary_passes >= 2:
        return "O1B_SUPPORTED"
    if primary_passes == 1:
        return "O1B_MIXED"
    return "O1B_NOT_SUPPORTED"


def _o1b_summary_path(root: Path) -> Path:
    """Return the fixed summary destination below an already trusted root."""
    expected = root / "o1b-session-probe-summary.json"
    if _entry_is_link_or_reparse(expected):
        raise EvalInfrastructureError("O1b summary 路径无效")
    return expected


def _read_json_object(path: Path, *, error: str) -> tuple[dict[str, object], str]:
    try:
        if _entry_is_link_or_reparse(path) or not path.is_file():
            raise EvalInfrastructureError(error)
        raw = _read_file_stably(path)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise EvalInfrastructureError(error)
        return payload, hashlib.sha256(raw).hexdigest()
    except EvalInfrastructureError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise EvalInfrastructureError(error) from None


def _validate_o1b_reservation_payload(
    payload: dict[str, object], *, expected_index: int
) -> FrozenProbeMetadata:
    if set(payload) != {"protocol_version", "attempt_index", "created_at", "metadata"}:
        raise EvalInfrastructureError("O1b reservation 无效")
    if payload.get("protocol_version") != O1B_PROTOCOL_VERSION:
        raise EvalInfrastructureError("O1b reservation 无效")
    if payload.get("attempt_index") != expected_index or isinstance(
        payload.get("attempt_index"), bool
    ):
        raise EvalInfrastructureError("O1b reservation 无效")
    if not isinstance(payload.get("created_at"), str) or not payload["created_at"]:
        raise EvalInfrastructureError("O1b reservation 无效")
    raw_metadata = payload.get("metadata")
    if not isinstance(raw_metadata, dict):
        raise EvalInfrastructureError("O1b reservation 无效")
    required = {
        "fixture_sha256", "prompt_sha256", "target_initial_sha256",
        "production_tree_sha256", "evaluator_protocol_sha256", "config",
    }
    if set(raw_metadata) != required or not all(
        isinstance(raw_metadata.get(name), str) and _HEX_SHA256.fullmatch(raw_metadata[name])
        for name in required - {"config"}
    ) or not isinstance(raw_metadata.get("config"), dict):
        raise EvalInfrastructureError("O1b reservation 无效")
    config = raw_metadata["config"]
    if any(str(key).lower() in {"api_key", "apikey", "authorization", "bearer"} for key in config):
        raise EvalInfrastructureError("O1b reservation 无效")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raise EvalInfrastructureError("O1b reservation 无效") from None
    if not _report_payload_is_safe(encoded, api_key=""):
        raise EvalInfrastructureError("O1b reservation 无效")
    return FrozenProbeMetadata(
        fixture_sha256=raw_metadata["fixture_sha256"],  # type: ignore[arg-type]
        prompt_sha256=raw_metadata["prompt_sha256"],  # type: ignore[arg-type]
        target_initial_sha256=raw_metadata["target_initial_sha256"],  # type: ignore[arg-type]
        production_tree_sha256=raw_metadata["production_tree_sha256"],  # type: ignore[arg-type]
        evaluator_protocol_sha256=raw_metadata["evaluator_protocol_sha256"],  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
    )


def _validate_o1b_result_payload(
    payload: dict[str, object], *, expected_index: int, metadata: FrozenProbeMetadata
) -> tuple[str, bool | None, str | None]:
    if set(payload) != O1B_RESULT_SCHEMA_FIELDS:
        raise EvalInfrastructureError("O1b result 无效")

    def invalid() -> None:
        raise EvalInfrastructureError("O1b result 字段无效")

    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 2:
        invalid()
    if type(payload.get("probe_id")) is not str or payload.get("probe_id") != PROBE_ID:
        invalid()
    if type(payload.get("mode")) is not str or payload.get("mode") != "real":
        invalid()
    if type(payload.get("protocol_version")) is not str or payload.get("protocol_version") != O1B_PROTOCOL_VERSION:
        invalid()
    if type(payload.get("attempt_index")) is not int or not 1 <= payload["attempt_index"] <= O1B_PLANNED_ATTEMPTS:
        invalid()
    for field in (
        "production_tree_sha256", "evaluator_protocol_sha256", "fixture_sha256",
        "prompt_sha256", "target_initial_sha256",
    ):
        value = payload.get(field)
        if type(value) is not str or _HEX_SHA256.fullmatch(value) is None:
            invalid()
    if payload.get("config") != metadata.config or not isinstance(payload.get("config"), dict):
        invalid()
    for field in ("target_after_turn1_sha256", "target_after_undo_sha256"):
        value = payload.get(field)
        if value is not None and (type(value) is not str or _HEX_SHA256.fullmatch(value) is None):
            invalid()

    def list_of_ints(field: str) -> list[object]:
        value = payload.get(field)
        if not isinstance(value, list) or len(value) > 2:
            invalid()
        if any(type(item) is not int or item < 0 for item in value):
            invalid()
        return value

    statuses = payload.get("turn_statuses")
    if not isinstance(statuses, list) or len(statuses) > 2 or any(
        type(item) is not str or item not in _PROBE_STATUS_VALUES for item in statuses
    ):
        invalid()
    rounds = list_of_ints("model_rounds")
    calls = list_of_ints("tool_calls")
    tokens = payload.get("provider_total_tokens")
    if not isinstance(tokens, list) or len(tokens) > 2 or any(
        item is not None and (type(item) is not int or item < 0) for item in tokens
    ):
        invalid()
    if not (len(statuses) == len(rounds) == len(calls) == len(tokens)):
        invalid()
    changed = payload.get("changed_paths_after_turn1")
    if not isinstance(changed, list) or any(type(item) is not str for item in changed):
        invalid()

    def nullable_int(field: str, *, nonnegative: bool = False) -> int | None:
        value = payload.get(field)
        if value is not None and (type(value) is not int or (nonnegative and value < 0)):
            invalid()
        return value

    for field in ("tests_after_turn1_returncode", "tests_after_turn2_returncode"):
        nullable_int(field)
    for field in (
        "undo_depth_after", "reset_undo_depth", "reset_pending_events",
        "reset_history_message_count", "reset_read_hash_count",
        "owned_client_close_calls", "baseline_direct_subprocess_count",
        "new_residual_direct_subprocess_count", "session_artifact_count",
    ):
        nullable_int(field, nonnegative=True)
    elapsed = payload.get("elapsed_seconds")
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
        invalid()
    for field in (
        "undo_ok", "close_idempotent", "turn1_ideal_trace", "turn2_ideal_trace",
        "ideal_trace_overall", "turn2_exact_value_observed",
    ):
        value = payload.get(field)
        if value is not None and type(value) is not bool:
            invalid()
    outcome = payload.get("outcome")
    if type(outcome) is not str or outcome not in {"PASS", "FAIL", "INVALID_INFRA"}:
        raise EvalInfrastructureError("O1b result schema 无效")
    if payload.get("attempt_index") != expected_index:
        raise EvalInfrastructureError("O1b result 无效")
    for name in (
        "production_tree_sha256", "evaluator_protocol_sha256",
        "fixture_sha256", "prompt_sha256", "target_initial_sha256",
    ):
        if payload.get(name) != getattr(metadata, name):
            raise EvalInfrastructureError("O1b result metadata 不一致")
    if payload.get("config") != metadata.config:
        raise EvalInfrastructureError("O1b result metadata 不一致")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raise EvalInfrastructureError("O1b result 不安全") from None
    if not _report_payload_is_safe(encoded, api_key=""):
        raise EvalInfrastructureError("O1b result 不安全")
    failure_code = payload.get("failure_code")
    if outcome == "PASS" and failure_code is not None:
        raise EvalInfrastructureError("O1b result failure_code 无效")
    if outcome != "PASS" and not isinstance(failure_code, str):
        raise EvalInfrastructureError("O1b result failure_code 无效")
    ideal = payload.get("ideal_trace_overall")
    turn1_ideal = payload.get("turn1_ideal_trace")
    turn2_ideal = payload.get("turn2_ideal_trace")
    expected_ideal = (
        turn1_ideal and turn2_ideal
        if turn1_ideal is not None and turn2_ideal is not None
        else None
    )
    if ideal != expected_ideal:
        raise EvalInfrastructureError("O1b result ideal_trace_overall 不一致")
    if outcome == "PASS":
        if any(
            type(payload.get(field)) is not bool
            for field in ("turn1_ideal_trace", "turn2_ideal_trace", "ideal_trace_overall")
        ):
            raise EvalInfrastructureError("O1b result PASS ideal_trace 证据不完整")
        required_pass = {
            "turn_statuses": ["COMPLETED", "COMPLETED"],
            "tests_after_turn1_returncode": 0,
            "tests_after_turn2_returncode": 0,
            "undo_ok": True,
            "undo_depth_after": 0,
            "reset_undo_depth": 0,
            "reset_pending_events": 0,
            "reset_history_message_count": 1,
            "reset_read_hash_count": 0,
            "close_idempotent": True,
            "new_residual_direct_subprocess_count": 0,
            "session_artifact_count": 0,
            "changed_paths_after_turn1": [TARGET_RELATIVE_PATH],
            "turn2_exact_value_observed": True,
        }
        if any(payload.get(field) != value for field, value in required_pass.items()):
            raise EvalInfrastructureError("O1b result PASS 证据不完整")
        if payload.get("baseline_direct_subprocess_count") is None:
            raise EvalInfrastructureError("O1b result PASS scan 证据不完整")
        if payload.get("target_after_turn1_sha256") is None or payload.get("target_after_undo_sha256") != payload.get("target_initial_sha256"):
            raise EvalInfrastructureError("O1b result PASS 哈希不一致")
        if payload.get("owned_client_close_calls") != 1:
            raise EvalInfrastructureError("O1b result PASS close 证据不完整")
    return outcome, ideal, failure_code if isinstance(failure_code, str) else None


def build_o1b_summary(*, evidence_root: Path, stop_reason: str) -> O1BSummary:
    """Read fixed O1b inputs and build a summary without loading credentials."""
    if stop_reason not in O1B_STOP_REASONS:
        raise EvalInfrastructureError("O1b stop reason 无效")
    root = Path(evidence_root)
    if _is_link_or_reparse(root) or not root.is_dir():
        raise EvalInfrastructureError("O1b evidence 目录不安全")
    root = root.resolve(strict=True)
    try:
        existing_names = {path.name for path in root.iterdir()}
    except OSError:
        raise EvalInfrastructureError("O1b evidence 目录读取失败") from None
    allowed_names = {
        "o1b-session-probe-summary.json",
        *(path.name for path in O1B_REPORT_PATHS.values()),
        *(path.name for path in O1B_RESERVATION_PATHS.values()),
    }
    if any(
        name.startswith("o1b-session-probe-") and name not in allowed_names
        for name in existing_names
    ):
        raise EvalInfrastructureError("O1b evidence 文件名无效")
    guard = _capture_component_guard(root, root)
    reservations: list[tuple[int, dict[str, object], FrozenProbeMetadata, str, Path]] = []
    input_hashes: dict[str, str] = {}
    seen_gap = False
    for index in range(1, O1B_PLANNED_ATTEMPTS + 1):
        reservation_path = root / O1B_RESERVATION_PATHS[index].name
        result_path = root / O1B_REPORT_PATHS[index].name
        reservation_exists = _entry_is_link_or_reparse(reservation_path) or reservation_path.exists()
        result_exists = _entry_is_link_or_reparse(result_path) or result_path.exists()
        if not reservation_exists:
            if result_exists:
                raise EvalInfrastructureError("O1b result 缺少 reservation")
            seen_gap = True
            continue
        if seen_gap:
            raise EvalInfrastructureError("O1b reservation 编号不连续")
        reservation_payload, reservation_hash = _read_json_object(
            reservation_path, error="O1b reservation 无效"
        )
        metadata = _validate_o1b_reservation_payload(
            reservation_payload, expected_index=index
        )
        if reservations and metadata.to_dict() != reservations[0][2].to_dict():
            raise EvalInfrastructureError("O1b reservation metadata 不一致")
        reservations.append((index, reservation_payload, metadata, reservation_hash, reservation_path))
        input_hashes[reservation_path.name] = reservation_hash
        _verify_component_guard(guard)

    if not reservations:
        # An empty queue is a valid offline early-stop summary.
        metadata = None
    else:
        metadata = reservations[0][2]
    attempts: list[dict[str, object]] = []
    primary_passes = valid_attempts = invalid_infra_count = ideal_trace_passes = 0
    for index, _reservation_payload, reservation_metadata, _reservation_hash, reservation_path in reservations:
        result_path = root / O1B_REPORT_PATHS[index].name
        if not result_path.exists() and not _entry_is_link_or_reparse(result_path):
            attempts.append({
                "attempt_index": index,
                "status": "RESERVED_NO_RESULT",
                "outcome": "INVALID_INFRA",
                "failure_code": "RESERVED_NO_RESULT",
                "ideal_trace_overall": None,
            })
            invalid_infra_count += 1
            continue
        payload, result_hash = _read_json_object(result_path, error="O1b result 无效")
        outcome, ideal, failure_code = _validate_o1b_result_payload(
            payload, expected_index=index, metadata=reservation_metadata
        )
        input_hashes[result_path.name] = result_hash
        attempts.append({
            "attempt_index": index,
            "status": outcome,
            "outcome": outcome,
            "failure_code": failure_code,
            "ideal_trace_overall": ideal,
        })
        if outcome in {"PASS", "FAIL"}:
            valid_attempts += 1
            if outcome == "PASS":
                primary_passes += 1
            if ideal is True:
                ideal_trace_passes += 1
        else:
            invalid_infra_count += 1
        _verify_component_guard(guard)
    attempted_count = len(reservations)
    unexecuted = tuple(index for index in range(1, O1B_PLANNED_ATTEMPTS + 1) if index > attempted_count)
    summary = O1BSummary(
        schema_version=2,
        protocol_version=O1B_PROTOCOL_VERSION,
        planned_attempts=O1B_PLANNED_ATTEMPTS,
        attempted_count=attempted_count,
        valid_attempts=valid_attempts,
        primary_passes=primary_passes,
        invalid_infra_count=invalid_infra_count,
        ideal_trace_passes=ideal_trace_passes,
        unexecuted_attempts=unexecuted,
        stop_reason=stop_reason,
        classification=_classify_o1b(valid_attempts=valid_attempts, primary_passes=primary_passes),
        attempts=tuple(attempts),
        input_sha256=input_hashes,
    )
    return summary


def _validate_real_config(config: ProviderConfig) -> None:
    parsed = urlsplit(config.base_url)
    if (
        config.model != "kimi-k3"
        or parsed.scheme.lower() != "https"
        or parsed.hostname != "api.moonshot.cn"
    ):
        raise EvalInfrastructureError("正式会话探针配置不符合要求")


def _fixture_stdout(summary: FixtureSummary) -> dict[str, object]:
    return {
        "mode": "validate-fixture",
        "outcome": "VALID",
        "fixture_sha256": summary.fixture_sha256,
        "prompt_sha256": summary.prompt_sha256,
        "target_initial_sha256": summary.target_initial_sha256,
        "initial_test_returncode": summary.initial_test_returncode,
        "target_relative_path": summary.target_relative_path,
    }


def _real_stdout(report: ProbeReport, *, relative_report: Path) -> dict[str, object]:
    return {
        "mode": report.mode,
        "outcome": report.outcome,
        "failure_code": report.failure_code,
        "turn_statuses": report.turn_statuses,
        "model_rounds": report.model_rounds,
        "tool_calls": report.tool_calls,
        "provider_total_tokens": report.provider_total_tokens,
        "tests_after_turn1_returncode": report.tests_after_turn1_returncode,
        "tests_after_turn2_returncode": report.tests_after_turn2_returncode,
        "undo_ok": report.undo_ok,
        "undo_depth_after": report.undo_depth_after,
        "reset_undo_depth": report.reset_undo_depth,
        "reset_pending_events": report.reset_pending_events,
        "reset_history_message_count": report.reset_history_message_count,
        "reset_read_hash_count": report.reset_read_hash_count,
        "close_idempotent": report.close_idempotent,
        "owned_client_close_calls": report.owned_client_close_calls,
        "baseline_direct_subprocess_count": report.baseline_direct_subprocess_count,
        "new_residual_direct_subprocess_count": report.new_residual_direct_subprocess_count,
        "session_artifact_count": report.session_artifact_count,
        "elapsed_seconds": report.elapsed_seconds,
        "report": relative_report.as_posix(),
    }


def _summary_stdout(summary: O1BSummary) -> dict[str, object]:
    return {
        "mode": "summarize",
        "planned_attempts": summary.planned_attempts,
        "attempted_count": summary.attempted_count,
        "valid_attempts": summary.valid_attempts,
        "primary_passes": summary.primary_passes,
        "invalid_infra_count": summary.invalid_infra_count,
        "ideal_trace_passes": summary.ideal_trace_passes,
        "unexecuted_attempts": summary.unexecuted_attempts,
        "stop_reason": summary.stop_reason,
        "classification": summary.classification,
        "summary": (Path("docs") / "evidence" / "o1b-session-probe-summary.json").as_posix(),
    }


def _resolve_o1b_cli_paths(
    *, attempt_index: int, reservation_path: Path, report_path: Path
) -> tuple[Path, Path, Path]:
    if attempt_index not in O1B_REPORT_PATHS:
        raise EvalInfrastructureError("O1b attempt 编号无效")
    evidence = _o1b_evidence_root(
        REPOSITORY_ROOT / O1B_REPORT_PATHS[1].parent
    )
    reservation_raw = Path(reservation_path)
    report_raw = Path(report_path)
    if not reservation_raw.is_absolute() and reservation_raw.parts != (
        *O1B_RESERVATION_PATHS[attempt_index].parts,
    ):
        raise EvalInfrastructureError("O1b reservation 路径无效")
    if not report_raw.is_absolute() and report_raw.parts != (
        *O1B_REPORT_PATHS[attempt_index].parts,
    ):
        raise EvalInfrastructureError("O1b 报告路径无效")
    reservation_path = (
        REPOSITORY_ROOT / reservation_path
        if not reservation_raw.is_absolute()
        else reservation_raw
    )
    report_path = (
        REPOSITORY_ROOT / report_path
        if not report_raw.is_absolute()
        else report_raw
    )
    reservation = _validate_o1b_path(
        reservation_path, root=evidence, attempt_index=attempt_index, reservation=True
    )
    report = _validate_o1b_path(
        report_path, root=evidence, attempt_index=attempt_index, reservation=False
    )
    return evidence, reservation, report


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    if args.validate_fixture:
        try:
            summary = validate_fixture()
        except (EvalInfrastructureError, OSError, ValueError):
            print("冻结 fixture 校验失败", file=sys.stderr)
            return 2
        except Exception:
            print("冻结 fixture 校验失败", file=sys.stderr)
            return 2
        print(json.dumps(_fixture_stdout(summary), ensure_ascii=False))
        return 0

    if args.summarize:
        try:
            evidence = _o1b_evidence_root(REPOSITORY_ROOT / O1B_REPORT_PATHS[1].parent)
            expected_summary = _o1b_summary_path(evidence)
            summary_raw = Path(args.summary)
            if not summary_raw.is_absolute() and summary_raw.parts != (
                "docs", "evidence", "o1b-session-probe-summary.json"
            ):
                raise EvalInfrastructureError("O1b summary 路径无效")
            supplied_summary = (
                REPOSITORY_ROOT / summary_raw
                if not summary_raw.is_absolute()
                else summary_raw
            )
            if supplied_summary.resolve(strict=False) != expected_summary:
                raise EvalInfrastructureError("O1b summary 路径无效")
            if expected_summary.exists() or _entry_is_link_or_reparse(expected_summary):
                raise EvalInfrastructureError("O1b summary 已存在，拒绝覆盖")
            summary = build_o1b_summary(evidence_root=evidence, stop_reason=args.stop_reason)
            _write_json_exclusive_verified(expected_summary, summary.to_dict())
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            print("O1b summary 生成失败", file=sys.stderr)
            return 2
        print(json.dumps(_summary_stdout(summary), ensure_ascii=False))
        return 0

    # O1b real mode is opt-in through its explicit attempt/reservation fields.
    # The legacy --real --report path remains available for the O1a probe.
    if args.real and args.attempt is not None:
        try:
            evidence, reservation, report_path = _resolve_o1b_cli_paths(
                attempt_index=args.attempt,
                reservation_path=args.reservation,
                report_path=args.report,
            )
            if report_path.exists() or _entry_is_link_or_reparse(report_path):
                raise EvalInfrastructureError("O1b 报告文件已存在，拒绝覆盖")
            if reservation.exists() or _entry_is_link_or_reparse(reservation):
                raise EvalInfrastructureError("O1b reservation 已存在，拒绝复用")
            config = load_provider_config()
            _validate_real_config(config)
            report = reserve_then_run_real_attempt(
                attempt_index=args.attempt,
                evidence_root=evidence,
                reservation_path=reservation,
                report_path=report_path,
                config=config,
            )
            _write_json_exclusive_verified(
                report_path, report.to_dict(), api_key=config.api_key,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            print("O1b 正式会话探针执行或报告写入失败", file=sys.stderr)
            return 2
        print(json.dumps(
            _real_stdout(report, relative_report=O1B_REPORT_PATHS[args.attempt]),
            ensure_ascii=False,
        ))
        return 0 if report.outcome == "PASS" else 2

    try:
        report_path = resolve_report_path(args.report)
        if report_path.exists():
            raise EvalInfrastructureError("报告文件已存在，拒绝覆盖")
    except EvalInfrastructureError:
        print("报告路径无效或已存在", file=sys.stderr)
        return 2

    try:
        config = load_provider_config()
        _validate_real_config(config)
        report = run_probe(
            fixture_root=FIXTURE_ROOT,
            turn1=TURN1_PATH.read_text(encoding="utf-8"),
            turn2=TURN2_PATH.read_text(encoding="utf-8"),
            config=config,
            client=None,
            mode="real",
        )
        write_probe_report_exclusive(report_path, report.to_dict(), api_key=config.api_key)
    except (KeyboardInterrupt, SystemExit):
        raise
    except (ConfigError, EvalInfrastructureError, OSError, UnicodeError, ValueError):
        print("正式会话探针执行或报告写入失败", file=sys.stderr)
        return 2
    except Exception:
        print("正式会话探针执行或报告写入失败", file=sys.stderr)
        return 2

    print(json.dumps(_real_stdout(report, relative_report=REPORT_RELATIVE_PATH), ensure_ascii=False))
    return 0 if report.outcome == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
