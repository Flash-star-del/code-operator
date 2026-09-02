"""Deterministic checks for the three frozen task fixtures.

The reference implementations live as byte maps in this grader-only helper.  They
are never copied by the run-workspace builder, which only receives ``project``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


TASK_ROOT = Path(__file__).with_name("tasks")

REFERENCE_BYTES: dict[str, dict[str, bytes]] = {
    "T1": {
        "ranges.py": (
            "def chunk_ranges(total: int, size: int) -> list[tuple[int, int]]:\n"
            '    """Return half-open ranges that cover [0, total) without empty chunks."""\n'
            "    if total < 0:\n"
            '        raise ValueError("total must be non-negative")\n'
            "    if size <= 0:\n"
            '        raise ValueError("size must be positive")\n'
            "    result: list[tuple[int, int]] = []\n"
            "    start = 0\n"
            "    while start < total:\n"
            "        result.append((start, min(start + size, total)))\n"
            "        start += size\n"
            "    return result\n"
        ).encode(),
    },
    "T2": {
        "retry.py": (
            "def retry_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:\n"
            '    """Return capped delay; attempt 1 waits base seconds."""\n'
            "    if attempt < 1:\n"
            '        raise ValueError("attempt must be at least 1")\n'
            "    if base <= 0 or cap <= 0:\n"
            '        raise ValueError("base and cap must be positive")\n'
            "    return min(cap, base * (2 ** (attempt - 1)))\n"
        ).encode(),
        "tests/test_retry_first_attempt.py": (
            "from retry import retry_delay\n\n\n"
            "def test_first_retry_waits_base() -> None:\n"
            "    assert retry_delay(1) == 0.5\n"
        ).encode(),
    },
    "T3": {
        "levels.py": (
            'VALID_LEVELS = frozenset({"debug", "info", "warning", "error"})\n\n'
            "\n"
            "def is_valid_level(value: str) -> bool:\n"
            "    return value in VALID_LEVELS\n\n\n"
            "def normalize_level(value: str) -> str:\n"
            "    if not isinstance(value, str):\n"
            '        raise TypeError("level must be a string")\n'
            "    normalized = value.strip().lower()\n"
            "    if not normalized or normalized not in VALID_LEVELS:\n"
            '        raise ValueError(f"unsupported level: {value}")\n'
            "    return normalized\n"
        ).encode(),
        "events.py": (
            "from levels import normalize_level\n\n\n"
            'def format_event(message: str, level: str = "info") -> str:\n'
            "    normalized_level = normalize_level(level)\n"
            '    return f"[{normalized_level.upper()}] {message}"\n'
        ).encode(),
    },
}


@dataclass(frozen=True)
class TaskValidation:
    task_id: str
    initial_fail_to_pass: tuple[str, ...]
    initial_pass_to_pass: tuple[str, ...]
    reference_fail_to_pass: tuple[str, ...]
    reference_pass_to_pass: tuple[str, ...]
    initial_hidden_red: bool
    reference_hidden_green: bool
    component_hashes: dict[str, str]
    allowed_paths: tuple[str, ...]
    violations: tuple[str, ...]

    @property
    def initial_failures(self) -> int:
        return len(self.initial_fail_to_pass)

    @property
    def valid(self) -> bool:
        return (
            bool(self.initial_fail_to_pass)
            and not self.initial_pass_to_pass
            and not self.reference_fail_to_pass
            and not self.reference_pass_to_pass
            and self.initial_hidden_red
            and self.reference_hidden_green
            and not self.violations
        )


@dataclass(frozen=True)
class RetryArtifactValidation:
    """Counterfactual result for a participant-added T2 regression test."""

    added_tests: tuple[str, ...]
    baseline_failed_tests: tuple[str, ...]
    mutant_failed_tests: tuple[str, ...]
    final_failed_tests: tuple[str, ...]
    candidate_violations: tuple[str, ...] = ()
    timed_out: bool = False
    infrastructure_failure: bool = False

    @property
    def baseline_failed(self) -> bool:
        return bool(self.baseline_failed_tests)

    @property
    def mutant_failed(self) -> bool:
        return bool(self.mutant_failed_tests)

    @property
    def final_passed(self) -> bool:
        return bool(self.added_tests) and not self.final_failed_tests

    @property
    def attempt_one_failure(self) -> bool:
        """Backward-compatible name for the behavioral mutation result."""
        return self.mutant_failed

    @property
    def valid(self) -> bool:
        same_failure = set(self.baseline_failed_tests) & set(self.mutant_failed_tests)
        return bool(self.added_tests) and bool(same_failure) and self.final_passed and not self.candidate_violations


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _framed_hash(files: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for relative, content in files:
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _project_files(task_id: str) -> tuple[str, ...]:
    project = TASK_ROOT / task_id / "project"
    return tuple(
        sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and ".pytest_cache" not in path.parts
                and path.suffix != ".pyc"
            )
        )
    )


def _component_hashes(task_id: str) -> dict[str, str]:
    task = TASK_ROOT / task_id
    project = task / "project"
    visible = project / "tests"
    hidden = task / "grader"
    project_files = tuple((relative, (project / relative).read_bytes()) for relative in _project_files(task_id))
    visible_files = tuple(
        (path.relative_to(project).as_posix(), path.read_bytes()) for path in sorted(visible.glob("test_*.py"))
    )
    hidden_files = tuple(
        (path.relative_to(task).as_posix(), path.read_bytes()) for path in sorted(hidden.glob("test_*.py"))
    )
    reference_files = tuple(sorted(REFERENCE_BYTES[task_id].items()))
    return {
        "prompt": _framed_hash((("task.txt", (task / "task.txt").read_bytes()),)),
        "project": _framed_hash(project_files),
        "visible": _framed_hash(visible_files),
        "hidden": _framed_hash(hidden_files),
        "reference": _framed_hash(reference_files),
    }


def _run_pytest(project: Path, relative_test: str) -> bool:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(project),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", relative_test],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return completed.returncode == 0


_FORBIDDEN_CANDIDATE_NAMES = frozenset(
    {"conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", "setup.py"}
)
_ALLOWED_TEST_IMPORTS = frozenset({"pytest", "retry"})
_FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "compile", "open", "input", "breakpoint", "system", "popen", "run", "Popen"}
)


def _retry_test_files(project: Path) -> tuple[str, ...]:
    return tuple(
        sorted(path.relative_to(project).as_posix() for path in project.glob("tests/**/test_*.py") if path.is_file())
    )


def _candidate_violations(project: Path) -> tuple[str, ...]:
    violations: list[str] = []
    try:
        root_attributes = os.lstat(project).st_file_attributes
    except (AttributeError, OSError):
        root_attributes = 0
    if project.is_symlink() or root_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return ("reparse-or-symlink:<candidate-root>",)
    if not project.is_dir():
        return ("candidate-not-directory",)
    retry = project / "retry.py"
    if retry.is_symlink() or _is_reparse(retry):
        violations.append("reparse-or-symlink:retry.py")
    elif not retry.exists():
        violations.append("missing-retry-file")
    elif not retry.is_file():
        violations.append("invalid-retry-file")
    for path in project.rglob("*"):
        try:
            attributes = os.lstat(path).st_file_attributes
        except (AttributeError, OSError):
            attributes = 0
        if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            violations.append(f"reparse-or-symlink:{path.relative_to(project).as_posix()}")
        if path.name in _FORBIDDEN_CANDIDATE_NAMES:
            violations.append(f"pytest-control:{path.relative_to(project).as_posix()}")
    for relative in _retry_test_files(project):
        path = project / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            violations.append(f"unsafe-test-source:{relative}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module.split(".")[0]] if node.module else []
            else:
                imports = []
            for imported in imports:
                if imported not in _ALLOWED_TEST_IMPORTS:
                    violations.append(f"unsafe-test-import:{relative}:{imported}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                    violations.append(f"unsafe-test-call:{relative}:{node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_CALLS:
                    violations.append(f"unsafe-test-call:{relative}:{node.func.attr}")
    return tuple(sorted(set(violations)))


def _is_reparse(path: Path) -> bool:
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _run_retry_tests(project: Path, retry_bytes: bytes, added_tests: tuple[str, ...]) -> tuple[str, ...]:
    """Run only selected added tests against a supplied retry implementation."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="retry-artifact-run-") as directory:
        run_root = Path(directory) / "project"
        run_root.mkdir()
        (run_root / "retry.py").write_bytes(retry_bytes)
        for relative in added_tests:
            target = run_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((project / relative).read_bytes())
            completed = _pytest_completed(run_root, relative)
            if completed.returncode != 0:
                failures.append(relative)
    return tuple(failures)


def _pytest_completed(project: Path, relative_test: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(project),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", relative_test],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _attempt_one_mutant() -> bytes:
    """Return a mutant that is wrong only for the first retry attempt."""
    return (
        "def retry_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:\n"
        '    """Return capped delay; attempt 1 waits base seconds."""\n'
        "    if attempt < 1:\n"
        '        raise ValueError("attempt must be at least 1")\n'
        "    if base <= 0 or cap <= 0:\n"
        '        raise ValueError("base and cap must be positive")\n'
        "    if attempt == 1:\n"
        "        return min(cap, base * 2)\n"
        "    return min(cap, base * (2 ** (attempt - 1)))\n"
    ).encode()


def validate_retry_artifact(project: Path) -> RetryArtifactValidation:
    """Validate newly added T2 tests by a baseline counterfactual run."""
    violations = _candidate_violations(project)
    baseline_tests = {
        path.relative_to(TASK_ROOT / "T2" / "project").as_posix()
        for path in (TASK_ROOT / "T2" / "project").glob("tests/test_*.py")
    }
    added_tests = tuple(relative for relative in _retry_test_files(project) if relative not in baseline_tests)
    if len(added_tests) > 20:
        violations = tuple(sorted(set(violations) | {"too-many-added-tests"}))
    if violations:
        return RetryArtifactValidation(added_tests, (), (), (), violations)
    try:
        baseline_failed_tests = _run_retry_tests(
            project, (TASK_ROOT / "T2" / "project" / "retry.py").read_bytes(), added_tests
        )
        mutant_failed_tests = _run_retry_tests(project, _attempt_one_mutant(), added_tests)
        final_failed_tests = _run_retry_tests(project, (project / "retry.py").read_bytes(), added_tests)
    except subprocess.TimeoutExpired:
        return RetryArtifactValidation(added_tests, (), (), (), timed_out=True)
    except (OSError, ValueError):
        return RetryArtifactValidation(added_tests, (), (), (), infrastructure_failure=True)
    return RetryArtifactValidation(
        added_tests=added_tests,
        baseline_failed_tests=baseline_failed_tests,
        mutant_failed_tests=mutant_failed_tests,
        final_failed_tests=final_failed_tests,
        candidate_violations=violations,
    )


def validate_reference_retry_artifact() -> RetryArtifactValidation:
    """Validate the in-memory reference patch and test artifact together."""
    with tempfile.TemporaryDirectory(prefix="retry-reference-artifact-") as directory:
        project = Path(directory) / "project"
        shutil.copytree(TASK_ROOT / "T2" / "project", project)
        for relative, content in REFERENCE_BYTES["T2"].items():
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return validate_retry_artifact(project)


def _run_case(task_id: str, files: dict[str, bytes], expression: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="agent-comparison-fixture-") as directory:
        project = Path(directory)
        for relative in _project_files(task_id):
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files.get(relative, (TASK_ROOT / task_id / "project" / relative).read_bytes()))
        test_file = project / "_fixture_probe.py"
        body = "\n".join(f"    {line}" if line else "" for line in expression.splitlines())
        test_file.write_text(f"def test_fixture_probe():\n{body}\n", encoding="utf-8")
        return _run_pytest(project, "_fixture_probe.py")


def _run_hidden(task_id: str, files: dict[str, bytes]) -> bool:
    with tempfile.TemporaryDirectory(prefix="agent-comparison-hidden-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        for relative in _project_files(task_id):
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files.get(relative, (TASK_ROOT / task_id / "project" / relative).read_bytes()))
        hidden = next((TASK_ROOT / task_id / "grader").glob("test_*.py"))
        hidden_target = project / "_hidden_test.py"
        hidden_target.write_bytes(hidden.read_bytes())
        return _run_pytest(project, "_hidden_test.py")


def _probes(task_id: str) -> tuple[tuple[str, str], ...]:
    if task_id == "T1":
        return (
            ("coverage", "from ranges import chunk_ranges\nassert chunk_ranges(5, 2) == [(0, 2), (2, 4), (4, 5)]"),
            ("empty", "from ranges import chunk_ranges\nassert chunk_ranges(0, 2) == []"),
            ("validation", "from ranges import chunk_ranges\n\ntry:\n    chunk_ranges(-1, 1)\nexcept ValueError as error:\n    assert str(error) == 'total must be non-negative'\nelse:\n    raise AssertionError"),
        )
    if task_id == "T2":
        return (
            ("first-attempt", "from retry import retry_delay\nassert retry_delay(1) == 0.5"),
            ("cap", "from retry import retry_delay\nassert retry_delay(10) == 8.0"),
            ("validation", "from retry import retry_delay\n\ntry:\n    retry_delay(0)\nexcept ValueError as error:\n    assert 'attempt' in str(error)\nelse:\n    raise AssertionError"),
        )
    return (
        ("human-level", "from events import format_event\nassert format_event('disk', ' Warning ') == '[WARNING] disk'"),
        ("default", "from events import format_event\nassert format_event('ready') == '[INFO] ready'"),
        ("backward-compatibility", "from levels import is_valid_level\nassert is_valid_level('info') and not is_valid_level(' Info ')"),
    )


def _reference_files(task_id: str) -> dict[str, bytes]:
    return dict(REFERENCE_BYTES[task_id])


def _import_violations(task_id: str) -> tuple[str, ...]:
    violations: list[str] = []
    allowed_local = {"levels"}
    for relative in _project_files(task_id):
        if not relative.endswith(".py") or "/tests/" in f"/{relative}":
            continue
        tree = ast.parse((TASK_ROOT / task_id / "project" / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            for name in names:
                if name not in allowed_local:
                    violations.append(f"third-party-or-runtime-import:{relative}:{name}")
    return tuple(sorted(set(violations)))


def validate_task(task_id: str) -> TaskValidation:
    """Validate one frozen fixture and its in-memory reference implementation."""
    if task_id not in REFERENCE_BYTES:
        raise ValueError(f"unknown task: {task_id}")
    task = TASK_ROOT / task_id
    expected = {
        "T1": {"ranges.py", "tests/test_ranges.py"},
        "T2": {"retry.py", "tests/test_retry.py"},
        "T3": {"levels.py", "events.py", "tests/test_events.py"},
    }[task_id]
    violations = list(_import_violations(task_id))
    actual_project = set(_project_files(task_id))
    if expected - actual_project:
        violations.append("missing-project-file")
    if actual_project - expected:
        violations.append("unexpected-project-file")
    if not all((task / relative).is_file() for relative in ("task.txt",)) or not any((task / "grader").glob("test_*.py")):
        violations.append("missing-fixture-component")
    if any(token in "".join(path.read_text(encoding="utf-8") for path in (task / "grader").glob("*.py")) for token in ("socket", "requests", "urllib", "random", "time")):
        violations.append("forbidden-nondeterminism-or-network")

    initial: dict[str, bytes] = {}
    for relative in actual_project:
        initial[relative] = (task / "project" / relative).read_bytes()
    ref = _reference_files(task_id)
    initial_f2p: list[str] = []
    initial_p2p: list[str] = []
    ref_f2p: list[str] = []
    ref_p2p: list[str] = []
    for index, (name, expression) in enumerate(_probes(task_id)):
        target_initial = initial_f2p if index == 0 else initial_p2p
        target_reference = ref_f2p if index == 0 else ref_p2p
        if not _run_case(task_id, initial, expression):
            target_initial.append(name)
        if not _run_case(task_id, ref, expression):
            target_reference.append(name)
    initial_hidden_red = not _run_hidden(task_id, initial)
    reference_hidden_green = _run_hidden(task_id, ref)
    if not reference_hidden_green:
        violations.append("reference-hidden-tests-failed")
    if not initial_f2p:
        violations.append("initial-fail-to-pass-empty")
    return TaskValidation(
        task_id=task_id,
        initial_fail_to_pass=tuple(initial_f2p),
        initial_pass_to_pass=tuple(initial_p2p),
        reference_fail_to_pass=tuple(ref_f2p),
        reference_pass_to_pass=tuple(ref_p2p),
        initial_hidden_red=initial_hidden_red,
        reference_hidden_green=reference_hidden_green,
        component_hashes=_component_hashes(task_id),
        allowed_paths=("retry.py", "tests/test_*.py") if task_id == "T2" else tuple(sorted(REFERENCE_BYTES[task_id])),
        violations=tuple(sorted(set(violations))),
    )
