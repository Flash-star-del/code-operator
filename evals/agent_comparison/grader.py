"""Offline hidden grading for an agent-comparison run workspace."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import fixture_validator
from .workspace import RunWorkspace, _absolute_without_following, _is_reparse_or_symlink


TASK_IDS = frozenset({"T1", "T2", "T3"})
_ALLOWED = {
    "T1": frozenset({"ranges.py"}),
    "T2": frozenset({"retry.py"}),
    "T3": frozenset({"levels.py", "events.py"}),
}
_BASELINE_PROJECT = {
    task_id: frozenset(fixture_validator._project_files(task_id)) for task_id in TASK_IDS
}


@dataclass(frozen=True)
class GradeResult:
    resolved: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_passed: int
    pass_to_pass_total: int
    forbidden_changes: tuple[str, ...]
    regression: bool
    tests_observed: bool
    changed_files: tuple[str, ...]
    insertions: int
    deletions: int
    patch_sha256: str
    primary_failure: str | None
    evidence_tags: tuple[str, ...]


@dataclass(frozen=True)
class _TestRun:
    passed: bool
    observed: bool
    infrastructure_failure: bool = False
    timed_out: bool = False
    syntax_error: bool = False


def _select_primary_failure(
    *,
    scope: bool = False,
    infra: bool = False,
    timeout: bool = False,
    regression: bool = False,
    did_not_test: bool = False,
    localization: bool = False,
    incorrect: bool = False,
) -> str | None:
    """Apply the frozen failure precedence to observed evidence."""
    for enabled, category in (
        (scope, "SCOPE_VIOLATION"),
        (infra, "TOOL_OR_INFRA_FAILURE"),
        (timeout, "TIMEOUT"),
        (regression, "REGRESSION"),
        (did_not_test, "DID_NOT_TEST"),
        (localization, "LOCALIZATION_FAILURE"),
        (incorrect, "INCORRECT_PATCH"),
    ):
        if enabled:
            return category
    return None


def _git_env() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return environment


def _git(root: Path, *arguments: str, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        env=_git_env(),
        capture_output=True,
        text=not binary,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or b"").strip() or "git command failed")
    return completed.stdout


def _empty_result(primary: str | None, *, evidence: tuple[str, ...] = ()) -> GradeResult:
    return GradeResult(
        resolved=False,
        fail_to_pass_passed=0,
        fail_to_pass_total=0,
        pass_to_pass_passed=0,
        pass_to_pass_total=0,
        forbidden_changes=(),
        regression=False,
        tests_observed=False,
        changed_files=(),
        insertions=0,
        deletions=0,
        patch_sha256=hashlib.sha256(b"").hexdigest(),
        primary_failure=primary,
        evidence_tags=evidence,
    )


def _is_cache_path(relative: str) -> bool:
    path = Path(relative)
    return any(part in {"__pycache__", ".pytest_cache"} for part in path.parts) or path.suffix == ".pyc"


def _numstat_paths(relative: str) -> tuple[str, ...]:
    if " => " not in relative:
        return (relative,)
    old, new = relative.split(" => ", 1)
    brace_start = old.rfind("{")
    brace_end = new.find("}")
    if brace_start >= 0 and brace_end >= 0:
        prefix = old[:brace_start]
        suffix = new[brace_end + 1 :]
        return prefix + old[brace_start + 1 :] + suffix, prefix + new[:brace_end] + suffix
    return old, new


def _status_paths(root: Path, baseline: str) -> tuple[set[str], dict[str, str], set[str]]:
    """Return changed paths, status by path and paths with binary diffs."""
    statuses: dict[str, str] = {}
    changed: set[str] = set()
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", binary=True)
    assert isinstance(status, bytes)
    pieces = status.split(b"\0")
    index = 0
    while index < len(pieces):
        record = pieces[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            continue
        code = record[:2].decode("ascii", "replace")
        old = record[3:].decode("utf-8", "surrogateescape")
        if code[0] in "RC" or code[1] in "RC":
            if index >= len(pieces):
                raise RuntimeError("malformed git rename status")
            new = pieces[index].decode("utf-8", "surrogateescape")
            index += 1
            for relative in (old, new):
                normalized = Path(relative).as_posix()
                if _is_cache_path(normalized):
                    continue
                changed.add(normalized)
                statuses[normalized] = code
        else:
            normalized = Path(old).as_posix()
            if _is_cache_path(normalized):
                continue
            changed.add(normalized)
            statuses[normalized] = code

    diff_names = _git(root, "diff", "--name-status", "--find-renames", "-z", baseline, "--", binary=True)
    assert isinstance(diff_names, bytes)
    pieces = diff_names.split(b"\0")
    index = 0
    while index < len(pieces):
        item = pieces[index]
        index += 1
        if not item:
            continue
        tab = item.find(b"\t")
        if tab < 0:
            code = item.decode("ascii", "replace")
            old = pieces[index].decode("utf-8", "surrogateescape") if index < len(pieces) else ""
            index += 1
        else:
            code = item[:tab].decode("ascii", "replace")
            old = item[tab + 1 :].decode("utf-8", "surrogateescape")
        if code.startswith(("R", "C")):
            new = pieces[index].decode("utf-8", "surrogateescape") if index < len(pieces) else ""
            index += 1
            for relative in (old, new):
                normalized = Path(relative).as_posix()
                if _is_cache_path(normalized):
                    continue
                changed.add(normalized)
                statuses.setdefault(normalized, code)
        else:
            normalized = Path(old).as_posix()
            if _is_cache_path(normalized):
                continue
            changed.add(normalized)
            statuses.setdefault(normalized, code)

    binary_paths: set[str] = set()
    numstat = _git(root, "diff", "--numstat", "--find-renames", baseline, "--")
    assert isinstance(numstat, str)
    for line in numstat.splitlines():
        columns = line.split("\t")
        if len(columns) >= 3 and (columns[0] == "-" or columns[1] == "-"):
            for relative in _numstat_paths(columns[-1]):
                normalized = Path(relative).as_posix()
                if not _is_cache_path(normalized):
                    binary_paths.add(normalized)
    return changed, statuses, binary_paths


def _safe_relative(relative: str) -> bool:
    path = Path(relative)
    return not path.is_absolute() and ".." not in path.parts and relative not in {"", "."}


def _root_chain_is_safe(root: Path) -> bool:
    absolute = _absolute_without_following(root)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_reparse_or_symlink(current):
            return False
    return True


def _baseline_snapshot(root: Path, baseline: str) -> tuple[tuple[tuple[str, bytes], ...], str]:
    names = _git(root, "ls-tree", "-r", "-z", "--name-only", baseline, binary=True)
    assert isinstance(names, bytes)
    relative_names = tuple(sorted(name.decode("utf-8", "surrogateescape") for name in names.split(b"\0") if name))
    files: list[tuple[str, bytes]] = []
    for relative in relative_names:
        content = _git(root, "show", f"{baseline}:{relative}", binary=True)
        assert isinstance(content, bytes)
        files.append((relative, content))
    digest = hashlib.sha256()
    for relative, content in files:
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return tuple(files), digest.hexdigest()


def _forbidden_paths(task_id: str, root: Path, changed: set[str], statuses: dict[str, str]) -> tuple[str, ...]:
    forbidden: set[str] = set()
    baseline_files = _BASELINE_PROJECT[task_id]
    for relative in changed:
        if not _safe_relative(relative):
            forbidden.add(relative)
            continue
        status = statuses.get(relative, "")
        allowed = relative in _ALLOWED[task_id]
        if task_id == "T2" and relative.startswith("tests/") and Path(relative).name.startswith("test_") and Path(relative).suffix == ".py":
            allowed = relative not in baseline_files and status[0:1] in {"?", "A"}
        if not allowed:
            forbidden.add(relative)
    if _is_reparse_or_symlink(root):
        forbidden.add("<workspace-root>")
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == root / ".git":
            directories[:] = []
            continue
        for name in (*directories, *files):
            child = current_path / name
            if _is_reparse_or_symlink(child):
                forbidden.add(child.relative_to(root).as_posix())
    return tuple(sorted(forbidden))


def _workspace_files(root: Path) -> set[str]:
    """Enumerate ordinary participant files independently of Git's ignore rules."""
    result: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == root / ".git":
            directories[:] = []
            continue
        directories[:] = [name for name in directories if name not in {"__pycache__", ".pytest_cache"}]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = current_path / name
            if _is_reparse_or_symlink(path):
                continue
            result.add(path.relative_to(root).as_posix())
    return result


def _copy_workspace(root: Path, destination: Path) -> None:
    destination.mkdir()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        directories[:] = [name for name in directories if name not in {".git", "__pycache__", ".pytest_cache"}]
        target_dir = destination / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith(".pyc"):
                continue
            source = current_path / name
            if _is_reparse_or_symlink(source):
                raise RuntimeError("workspace contains link")
            target = target_dir / name
            target.write_bytes(source.read_bytes())


def _pytest(project: Path, test_path: str) -> _TestRun:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(project),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONNOUSERSITE": "1",
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", test_path],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _TestRun(False, True, timed_out=True)
    except (OSError, ValueError):
        return _TestRun(False, False, infrastructure_failure=True)
    output = f"{completed.stdout}\n{completed.stderr}"
    return _TestRun(completed.returncode == 0, True, syntax_error="SyntaxError" in output)


def _probe(project: Path, expression: str) -> _TestRun:
    probe = project / "_grader_probe.py"
    body = "\n".join(f"    {line}" if line else "" for line in expression.splitlines())
    probe.write_text(f"def test_probe():\n{body}\n", encoding="utf-8")
    try:
        return _pytest(project, "_grader_probe.py")
    finally:
        probe.unlink(missing_ok=True)


def _run_grading(task_id: str, root: Path) -> tuple[list[_TestRun], _TestRun, _TestRun]:
    with tempfile.TemporaryDirectory(prefix="agent-comparison-grading-") as directory:
        project = Path(directory) / "project"
        _copy_workspace(root, project)
        visible = _pytest(project, "tests")
        probes = [_probe(project, expression) for _, expression in fixture_validator._probes(task_id)]
        hidden_source = next((fixture_validator.TASK_ROOT / task_id / "grader").glob("test_*.py"))
        hidden = project / "_hidden_test.py"
        hidden.write_bytes(hidden_source.read_bytes())
        hidden_result = _pytest(project, "_hidden_test.py")
        return probes, visible, hidden_result


def _line_counts(root: Path, baseline: str) -> tuple[int, int]:
    result = _git(root, "diff", "--numstat", baseline, "--")
    assert isinstance(result, str)
    insertions = deletions = 0
    for line in result.splitlines():
        columns = line.split("\t")
        if len(columns) < 3 or columns[0] == "-" or columns[1] == "-":
            continue
        try:
            insertions += int(columns[0])
            deletions += int(columns[1])
        except ValueError:
            continue
    return insertions, deletions


def _count_untracked_lines(root: Path, changed: set[str], statuses: dict[str, str]) -> int:
    total = 0
    for relative in changed:
        if statuses.get(relative, "")[0:1] != "?":
            continue
        path = root / relative
        if path.is_file() and not _is_reparse_or_symlink(path):
            try:
                total += len(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                pass
    return total


def _patch_hash(root: Path, baseline: str, changed: set[str], statuses: dict[str, str]) -> str:
    tracked = _git(root, "diff", "--binary", "--no-ext-diff", baseline, "--", binary=True)
    assert isinstance(tracked, bytes)
    digest = hashlib.sha256()
    digest.update(tracked)
    for relative in sorted(changed):
        if statuses.get(relative, "")[0:1] != "?":
            continue
        candidate = root / relative
        if not candidate.is_file() or _is_reparse_or_symlink(candidate):
            continue
        completed = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "--no-index", os.devnull, relative],
            cwd=root,
            env=_git_env(),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError("git no-index diff failed")
        patch = completed.stdout
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(patch).to_bytes(8, "big"))
        digest.update(patch)
    return digest.hexdigest()


def grade_workspace(task_id: str, workspace: RunWorkspace) -> GradeResult:
    """Grade an exited run while keeping hidden fixtures outside the workspace."""
    if task_id not in TASK_IDS:
        raise ValueError(f"unknown task: {task_id}")
    if not isinstance(workspace, RunWorkspace):
        return _empty_result("TOOL_OR_INFRA_FAILURE", evidence=("INVALID_WORKSPACE",))
    try:
        root = Path(workspace.root)
        if _is_reparse_or_symlink(root) or not root.is_dir() or not _root_chain_is_safe(root):
            return _empty_result("TOOL_OR_INFRA_FAILURE", evidence=("INVALID_WORKSPACE",))
        baseline_files, baseline_tree = _baseline_snapshot(root, workspace.baseline_commit)
        baseline_hashes = {
            relative: hashlib.sha256(content).hexdigest() for relative, content in baseline_files
        }
        if baseline_tree != workspace.initial_tree_sha256 or baseline_hashes != workspace.initial_file_sha256:
            return _empty_result("TOOL_OR_INFRA_FAILURE", evidence=("BASELINE_HASH_MISMATCH",))
        changed, statuses, binary_paths = _status_paths(root, workspace.baseline_commit)
        baseline_paths = set(baseline_hashes)
        for relative in _workspace_files(root) - baseline_paths:
            changed.add(relative)
            statuses.setdefault(relative, "??")
        forbidden = _forbidden_paths(task_id, root, changed, statuses)
        patch_sha = _patch_hash(root, workspace.baseline_commit, changed, statuses)
        insertions, deletions = _line_counts(root, workspace.baseline_commit)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, TypeError):
        return _empty_result("TOOL_OR_INFRA_FAILURE", evidence=("GIT_OR_WORKSPACE_FAILURE",))

    if forbidden:
        return GradeResult(
            resolved=False,
            fail_to_pass_passed=0,
            fail_to_pass_total=2,
            pass_to_pass_passed=0,
            pass_to_pass_total=2,
            forbidden_changes=forbidden,
            regression=False,
            tests_observed=False,
            changed_files=tuple(sorted(changed)),
            insertions=insertions + _count_untracked_lines(root, changed, statuses),
            deletions=deletions,
            patch_sha256=patch_sha,
            primary_failure="SCOPE_VIOLATION",
            evidence_tags=("SCOPE_VIOLATION",),
        )

    try:
        probes, visible, hidden = _run_grading(task_id, root)
    except subprocess.TimeoutExpired:
        return GradeResult(
            False, 0, 2, 0, 2, (), False, False, tuple(sorted(changed)), insertions, deletions, patch_sha,
            "TIMEOUT", ("GRADING_TIMEOUT",),
        )
    except (OSError, RuntimeError, ValueError):
        return GradeResult(
            False, 0, 2, 0, 2, (), False, False, tuple(sorted(changed)), insertions, deletions, patch_sha,
            "TOOL_OR_INFRA_FAILURE", ("GRADING_INFRA_FAILURE",),
        )

    f2p = int(probes[0].passed) + int(hidden.passed)
    p2p = sum(int(probe.passed) for probe in probes[1:])
    # The visible suite intentionally contains the target regression for some
    # frozen fixtures (notably T3).  PASS_TO_PASS therefore comes from the
    # pre-registered probes that were green in the baseline, not from the
    # aggregate visible-suite exit code.
    syntax_error = any(run.syntax_error for run in (*probes, visible, hidden))
    regression = any(not probe.passed for probe in probes[1:]) and not syntax_error
    tests_observed = all(run.observed for run in (*probes, visible, hidden))
    tags = [f"F2P_{f2p}_OF_2", f"P2P_{p2p}_OF_2"]
    if hidden.passed:
        tags.append("HIDDEN_GREEN")
    else:
        tags.append("HIDDEN_RED")
    if visible.passed:
        tags.append("VISIBLE_GREEN")
    else:
        tags.append("REGRESSION")
    if binary_paths:
        tags.append("BINARY_CHANGE")
    if syntax_error:
        tags.append("SYNTAX_ERROR")

    t2_artifact = None
    t2_test_valid = True
    artifact_forbidden: tuple[str, ...] = ()
    if task_id == "T2":
        try:
            t2_artifact = fixture_validator.validate_retry_artifact(root)
        except subprocess.TimeoutExpired:
            t2_artifact = fixture_validator.RetryArtifactValidation((), (), (), (), timed_out=True)
        except OSError:
            t2_artifact = fixture_validator.RetryArtifactValidation((), (), (), (), infrastructure_failure=True)
        artifact_forbidden = t2_artifact.candidate_violations
        if t2_artifact.candidate_violations:
            tags.append("T2_ARTIFACT_SCOPE_VIOLATION")
        if t2_artifact.timed_out:
            tags.append("T2_ARTIFACT_TIMEOUT")
        if t2_artifact.infrastructure_failure:
            tags.append("T2_ARTIFACT_INFRA_FAILURE")
        t2_test_valid = t2_artifact.valid
        if t2_artifact.valid:
            tags.append("T2_REGRESSION_TEST_VALID")
        elif not t2_artifact.added_tests:
            tags.append("T2_REGRESSION_TEST_MISSING")
        else:
            tags.append("T2_REGRESSION_TEST_TOOTHLESS")

    production_changed = bool(changed & _ALLOWED[task_id])
    primary = _select_primary_failure(
        scope=bool(artifact_forbidden),
        infra=any(run.infrastructure_failure for run in (*probes, visible, hidden))
        or bool(t2_artifact and t2_artifact.infrastructure_failure),
        timeout=any(run.timed_out for run in (*probes, visible, hidden))
        or bool(t2_artifact and t2_artifact.timed_out),
        regression=regression,
        did_not_test=bool(t2_artifact and not t2_test_valid) or not tests_observed,
        localization=f2p < 2 and not production_changed and not syntax_error,
        incorrect=syntax_error or (f2p < 2 and production_changed) or (p2p < 2) or not changed,
    )

    return GradeResult(
        resolved=primary is None,
        fail_to_pass_passed=f2p,
        fail_to_pass_total=2,
        pass_to_pass_passed=p2p,
        pass_to_pass_total=2,
        forbidden_changes=tuple(sorted(artifact_forbidden)),
        regression=regression,
        tests_observed=tests_observed,
        changed_files=tuple(sorted(changed)),
        insertions=insertions + _count_untracked_lines(root, changed, statuses),
        deletions=deletions,
        patch_sha256=patch_sha,
        primary_failure=primary,
        evidence_tags=tuple(tags),
    )
