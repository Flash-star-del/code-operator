from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path


class PathPolicyError(ValueError):
    """Raised when a requested path crosses the workspace or sensitivity boundary."""


class CommandDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


_SENSITIVE_EXACT = {
    ".git",
    ".agent",
    ".agents",
    ".code-operator",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.json",
}


def _is_sensitive_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in _SENSITIVE_EXACT
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith(".pem")
        or lowered.endswith(".key")
    )


class ExecutionPolicy:
    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise PathPolicyError("工作区必须是已存在的目录")

    def resolve_workspace_path(
        self,
        requested: str | os.PathLike[str],
        *,
        for_write: bool = False,
    ) -> Path:
        del for_write  # Path.resolve(strict=False) already resolves existing parents.
        raw = Path(requested)
        candidate = raw if raw.is_absolute() else self.workspace / raw
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.workspace)
        except (OSError, RuntimeError, ValueError) as error:
            raise PathPolicyError("目标路径必须位于工作区内") from error

        raw_parts = raw.parts if not raw.is_absolute() else relative.parts
        if any(_is_sensitive_name(part) for part in (*raw_parts, *relative.parts)):
            raise PathPolicyError("拒绝访问敏感文件或内部目录")
        return resolved

    def resolve(
        self,
        requested: str | os.PathLike[str],
        *,
        for_write: bool = False,
    ) -> Path:
        return self.resolve_workspace_path(requested, for_write=for_write)


WorkspacePolicy = ExecutionPolicy


ApprovalCallback = Callable[[list[str], Path], bool]


class CommandPolicy:
    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        approve: ApprovalCallback | None = None,
        ask_all: bool = False,
        auto_approve_tests: bool = False,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self._approve = approve or (lambda _argv, _cwd: False)
        self._ask_all = ask_all
        self._auto_approve_tests = auto_approve_tests

    @staticmethod
    def _is_test_command(argv: Sequence[str]) -> bool:
        if not argv:
            return False
        program = Path(argv[0]).name.casefold()
        if program.endswith(".exe"):
            program = program[:-4]
        lowered = [item.casefold() for item in argv[1:]]
        if program == "pytest":
            return True
        return program in {"python", "python3", "py"} and lowered[:2] == [
            "-m",
            "pytest",
        ]

    def _is_unshadowed_bare_runner(self, argv: Sequence[str]) -> bool:
        raw_program = Path(argv[0])
        if raw_program.name != argv[0]:
            return False
        names = {raw_program.name}
        if not raw_program.suffix:
            names.update(
                f"{raw_program.name}{suffix}"
                for suffix in (".com", ".exe", ".bat", ".cmd")
            )
        return not any((self.workspace / name).is_file() for name in names)

    def classify(self, argv: Sequence[str]) -> CommandDecision:
        if not argv:
            return CommandDecision.DENY
        program = Path(argv[0]).name.casefold()
        if program.endswith(".exe"):
            program = program[:-4]
        lowered = [item.casefold() for item in argv[1:]]

        shell_flags = {
            "cmd": {"/c", "/k"},
            "powershell": {"-command", "-c", "-encodedcommand"},
            "pwsh": {"-command", "-c", "-encodedcommand"},
            "sh": {"-c"},
            "bash": {"-c"},
            "zsh": {"-c"},
        }
        if program in shell_flags and any(flag in lowered for flag in shell_flags[program]):
            return CommandDecision.DENY
        if program in {"rm", "rmdir", "del", "erase", "remove-item"}:
            return CommandDecision.DENY
        if program == "git":
            if "push" in lowered and any(
                item in {"--force", "-f", "--force-with-lease"} for item in lowered
            ):
                return CommandDecision.DENY
            if "reset" in lowered or "clean" in lowered or "rebase" in lowered:
                return CommandDecision.DENY
            dangerous_prefixes = (
                "--git-dir",
                "--work-tree",
                "--output",
            )
            if any(
                item in {"-c", "--no-index", "--ext-diff", "--textconv"}
                or item.startswith(dangerous_prefixes)
                for item in lowered
            ):
                return CommandDecision.DENY
            if lowered and lowered[0] == "status":
                safe_status = {
                    "--short",
                    "-s",
                    "--branch",
                    "-b",
                    "-sb",
                    "--porcelain",
                    "--porcelain=v1",
                    "--porcelain=v2",
                    "--untracked-files=no",
                    "--untracked-files=normal",
                    "--untracked-files=all",
                    "-uno",
                    "-unormal",
                    "-uall",
                    "--show-stash",
                    "--ahead-behind",
                    "--no-ahead-behind",
                    "--null",
                    "-z",
                }
                return (
                    CommandDecision.ALLOW
                    if all(item in safe_status for item in lowered[1:])
                    else CommandDecision.ASK
                )
            if lowered and lowered[0] == "diff":
                safe_diff = {
                    "--stat",
                    "--cached",
                    "--staged",
                    "--check",
                    "--name-only",
                    "--name-status",
                    "--numstat",
                    "--shortstat",
                    "--compact-summary",
                    "--summary",
                    "--no-color",
                    "--color=never",
                    "--exit-code",
                    "--quiet",
                }
                return (
                    CommandDecision.ALLOW
                    if all(item in safe_diff for item in lowered[1:])
                    else CommandDecision.ASK
                )
            if lowered == ["--version"]:
                return CommandDecision.ALLOW
        if lowered in [["--version"], ["-v"]] or (
            program in {"python", "python3", "py"} and lowered in [["-v"], ["--version"]]
        ):
            return CommandDecision.ALLOW
        return CommandDecision.ASK

    def approve(self, argv: Sequence[str]) -> CommandDecision:
        decision = self.classify(argv)
        if decision is CommandDecision.DENY:
            return decision
        if self._ask_all:
            return (
                CommandDecision.ALLOW
                if self._approve(list(argv), self.workspace)
                else CommandDecision.ASK
            )
        if (
            self._auto_approve_tests
            and self._is_test_command(argv)
            and self._is_unshadowed_bare_runner(argv)
        ):
            return CommandDecision.ALLOW
        if decision is CommandDecision.ASK:
            return (
                CommandDecision.ALLOW
                if self._approve(list(argv), self.workspace)
                else CommandDecision.ASK
            )
        return decision
