"""Creation of isolated, participant-visible benchmark workspaces."""

from __future__ import annotations

import hashlib
import os
import subprocess
import stat
from dataclasses import dataclass
from pathlib import Path

from .schema import FrozenDict


TASK_ROOT = Path(__file__).resolve().parent / "tasks"
TASK_IDS = frozenset({"T1", "T2", "T3", "T4", "T5"})
_VISIBLE_FILES = {
    "T1": ("ranges.py", "tests/test_ranges.py"),
    "T2": ("retry.py", "tests/test_retry.py"),
    "T3": ("events.py", "levels.py", "tests/test_events.py"),
    "T4": ("fields.py", "tests/test_fields.py"),
    "T5": ("lru.py", "tests/test_lru.py"),
}

_GIT_NAME = "agent-comparison-local"
_GIT_EMAIL = "agent-comparison@localhost"
_FORBIDDEN_PARTS = frozenset(
    {
        ".env",
        ".env.local",
        "agents.md",
        "claude.md",
        "codex.md",
        "grader",
        "plugin",
        "plugins",
        "mcp",
        "session",
        "sessions",
        "transcript",
        "transcripts",
        "reference",
        "hidden",
        "instructions.md",
        "reference.patch",
        "hidden_test.py",
        "grader.py",
        "plugin.json",
        "mcp.json",
        "session.json",
        "transcript.txt",
    }
)


@dataclass(frozen=True)
class RunWorkspace:
    root: Path
    baseline_commit: str
    initial_tree_sha256: str
    initial_file_sha256: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_file_sha256", FrozenDict(self.initial_file_sha256))


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _absolute_without_following(path: Path) -> Path:
    """Make a path absolute while retaining symlink components for inspection."""
    return Path(os.path.abspath(os.fspath(path)))


def _assert_safe_parents(path: Path) -> Path:
    absolute = _absolute_without_following(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_reparse_or_symlink(current):
            raise ValueError(f"destination contains symlink or reparse point: {current}")
    return absolute


def _assert_safe_tree(root: Path) -> None:
    if _is_reparse_or_symlink(root) or not root.is_dir():
        raise ValueError("workspace must be an ordinary directory")
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == root / ".git":
            directories[:] = []
            continue
        for name in (*directories, *files):
            child = current_path / name
            if _is_reparse_or_symlink(child):
                raise ValueError(f"workspace contains symlink or reparse point: {child}")


def _visible_files(task_id: str) -> tuple[str, ...]:
    project = TASK_ROOT / task_id / "project"
    result: list[str] = []
    for relative in _VISIBLE_FILES[task_id]:
        source = project / relative
        _assert_safe_parents(source)
        if _is_reparse_or_symlink(source) or not source.is_file() or source.suffix == ".pyc":
            raise ValueError(f"fixture contains unsafe or missing file: {source}")
        result.append(relative)
    return tuple(sorted(result))


def _framed_tree_hash(files: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for relative, content in files:
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_env() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return environment


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed


def create_run_workspace(task_id: str, destination: Path) -> RunWorkspace:
    """Copy one frozen project into a new ordinary directory and commit its baseline."""
    if task_id not in TASK_IDS:
        raise ValueError(f"unknown task: {task_id}")
    source = TASK_ROOT / task_id / "project"
    if not source.is_dir() or _is_reparse_or_symlink(source):
        raise ValueError("task project is not a safe directory")

    root = _assert_safe_parents(Path(destination))
    if root.exists():
        if _is_reparse_or_symlink(root) or not root.is_dir():
            raise ValueError("destination must be an ordinary directory")
        if any(root.iterdir()):
            raise ValueError("destination must be new or empty")
    else:
        root.mkdir(parents=True, exist_ok=False)
    _assert_safe_parents(root)
    _assert_safe_tree(root)
    # CI 的 TEMP 可能是 8.3 短路径（RUNNER~1）或别名盘；父路径已验证无链接，
    # resolve 仅做规范化，保证 root 与子进程/测试侧的真实路径一致。
    root = root.resolve()

    files = _visible_files(task_id)
    copied: list[tuple[str, bytes]] = []
    for relative in files:
        source_file = source / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source_file.read_bytes()
        target.write_bytes(content)
        copied.append((relative, content))

    _git(root, "init", "-q")
    _git(root, "config", "--local", "user.name", _GIT_NAME)
    _git(root, "config", "--local", "user.email", _GIT_EMAIL)
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--no-gpg-sign", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD").stdout.strip()
    file_hashes = {relative: hashlib.sha256(content).hexdigest() for relative, content in copied}
    return RunWorkspace(
        root=root,
        baseline_commit=baseline,
        initial_tree_sha256=_framed_tree_hash(tuple(copied)),
        initial_file_sha256=file_hashes,
    )
