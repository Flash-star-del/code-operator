from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code_operator.policy import (
    CommandDecision,
    CommandPolicy,
    PathPolicyError,
    WorkspacePolicy,
)
from code_operator.redaction import Redactor, sanitized_subprocess_environment
from code_operator.tools.command import run_command


def make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def test_workspace_policy_rejects_parent_and_external_absolute_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspacePolicy(workspace)

    with pytest.raises(PathPolicyError, match="工作区"):
        policy.resolve("../outside.txt")
    with pytest.raises(PathPolicyError, match="工作区"):
        policy.resolve(tmp_path / "outside.txt")


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".git/config",
        ".agent/state.json",
        ".agents/config.json",
        ".code-operator/audit.jsonl",
        "secret.pem",
        "private.key",
        "id_rsa",
        "credentials.json",
    ],
)
def test_workspace_policy_rejects_sensitive_paths(tmp_path: Path, path: str) -> None:
    policy = WorkspacePolicy(tmp_path)

    with pytest.raises(PathPolicyError, match="敏感"):
        policy.resolve(path, for_write=True)


def test_existing_link_escape_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link = workspace / "linked-dir"
    make_directory_link(link, outside)

    with pytest.raises(PathPolicyError, match="工作区"):
        WorkspacePolicy(workspace).resolve("linked-dir/secret.txt")


def test_new_target_through_symlinked_parent_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "linked-dir"
    make_directory_link(link, outside)

    with pytest.raises(PathPolicyError, match="工作区"):
        WorkspacePolicy(workspace).resolve("linked-dir/new.py", for_write=True)


def test_workspace_policy_accepts_normal_new_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resolved = WorkspacePolicy(workspace).resolve("src/new.py", for_write=True)

    assert resolved == workspace.resolve() / "src" / "new.py"


@pytest.mark.parametrize(
    ("argv", "decision"),
    [
        (["git", "status", "--short"], CommandDecision.ALLOW),
        (["git", "diff", "--stat"], CommandDecision.ALLOW),
        (["git", "diff", "--no-index", "../outside", "file"], CommandDecision.DENY),
        (["git", "diff", "--output=outside.patch"], CommandDecision.DENY),
        (["git", "status", "--git-dir=../outside/.git"], CommandDecision.DENY),
        (["git", "status", "unexpected-path"], CommandDecision.ASK),
        (["python", "-V"], CommandDecision.ALLOW),
        (["python", "script.py"], CommandDecision.ASK),
        (["python", "-m", "pytest"], CommandDecision.ASK),
        (["cmd", "/c", "echo unsafe"], CommandDecision.DENY),
        (["powershell", "-Command", "Get-ChildItem"], CommandDecision.DENY),
        (["bash", "-c", "pwd"], CommandDecision.DENY),
        (["git", "push", "--force"], CommandDecision.DENY),
        (["git", "reset", "--hard"], CommandDecision.DENY),
    ],
)
def test_command_policy_classifies_allow_ask_and_deny(
    tmp_path: Path, argv: list[str], decision: CommandDecision
) -> None:
    assert CommandPolicy(tmp_path).classify(argv) is decision


def test_subprocess_environment_removes_api_keys_tokens_and_secrets() -> None:
    source = {
        "PATH": "safe-path",
        "PATHEXT": ".EXE",
        "CODE_OPERATOR_API_KEY": "current-key",
        "OTHER_API_KEY": "other-key",
        "ACCESS_TOKEN": "token",
        "CLIENT_SECRET": "secret",
        "AUTHORIZATION": "Bearer value",
        "UNRELATED": "must-not-be-forwarded",
    }

    environment = sanitized_subprocess_environment(source, "current-key")

    assert environment == {"PATH": "safe-path", "PATHEXT": ".EXE"}


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 1234

    def communicate(self, *, timeout: int) -> tuple[str, str]:
        assert timeout == 9
        return ("output current-key", "")

    def poll(self) -> int:
        return 0


def test_run_command_uses_array_without_shell_fixed_cwd_timeout_and_clean_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> FakeProcess:
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    policy = CommandPolicy(tmp_path, approve=lambda _argv, _cwd: True)

    result = run_command(
        tool_call_id="call-1",
        argv=["python", "script.py"],
        timeout_seconds=9,
        policy=policy,
        redactor=Redactor(["current-key"]),
        environment={
            "PATH": os.environ.get("PATH", ""),
            "CODE_OPERATOR_API_KEY": "current-key",
        },
    )

    assert result.ok is True
    assert captured["argv"] == ["python", "script.py"]
    assert captured["shell"] is False
    assert captured["cwd"] == str(tmp_path.resolve())
    assert "current-key" not in captured["env"].values()  # type: ignore[union-attr]
    assert result.details["stdout"] == "output <REDACTED>"
    assert result.details["exit_code"] == 0


def test_denied_or_unapproved_command_never_starts_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = False

    def fake_popen(*_: object, **__: object) -> FakeProcess:
        nonlocal started
        started = True
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    policy = CommandPolicy(tmp_path, approve=lambda _argv, _cwd: False)

    denied = run_command(
        tool_call_id="denied",
        argv=["cmd", "/c", "echo unsafe"],
        timeout_seconds=9,
        policy=policy,
        redactor=Redactor([]),
        environment={},
    )
    unapproved = run_command(
        tool_call_id="unapproved",
        argv=["python", "script.py"],
        timeout_seconds=9,
        policy=policy,
        redactor=Redactor([]),
        environment={},
    )

    assert started is False
    assert denied.error_code == "COMMAND_DENIED"
    assert unapproved.error_code == "USER_DENIED"


def test_denied_command_result_redacts_secret_arguments(tmp_path: Path) -> None:
    result = run_command(
        tool_call_id="denied-secret",
        argv=["cmd", "/c", "echo current-key"],
        timeout_seconds=9,
        policy=CommandPolicy(tmp_path, approve=lambda _argv, _cwd: False),
        redactor=Redactor(["current-key"]),
        environment={},
    )

    assert result.error_code == "COMMAND_DENIED"
    assert "current-key" not in result.to_message_content()
    assert result.details["argv"] == ["cmd", "/c", "echo <REDACTED>"]


def test_real_subprocess_times_out_and_is_reported_separately(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_command(
        tool_call_id="timeout",
        argv=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
        policy=CommandPolicy(tmp_path, approve=lambda _argv, _cwd: True),
        redactor=Redactor([]),
        environment=os.environ,
    )
    elapsed = time.monotonic() - started

    assert result.ok is False
    assert result.error_code == "COMMAND_TIMEOUT"
    assert result.details["timed_out"] is True
    assert elapsed < 5


def test_real_subprocess_cannot_inherit_provider_key(tmp_path: Path) -> None:
    result = run_command(
        tool_call_id="environment",
        argv=[
            sys.executable,
            "-c",
            "import os; print('present' if os.getenv('CODE_OPERATOR_API_KEY') else 'absent')",
        ],
        timeout_seconds=10,
        policy=CommandPolicy(tmp_path, approve=lambda _argv, _cwd: True),
        redactor=Redactor(["test-provider-key"]),
        environment={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "CODE_OPERATOR_API_KEY": "test-provider-key",
        },
    )

    assert result.ok is True
    assert result.details["stdout"].strip() == "absent"


def test_ctrl_c_during_process_creation_returns_user_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_popen(*_: object, **__: object) -> FakeProcess:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", interrupted_popen)

    result = run_command(
        tool_call_id="abort-before-start",
        argv=["python", "script.py"],
        timeout_seconds=10,
        policy=CommandPolicy(tmp_path, approve=lambda _argv, _cwd: True),
        redactor=Redactor([]),
        environment={},
    )

    assert result.error_code == "USER_ABORTED"
    assert result.details["exit_code"] is None
