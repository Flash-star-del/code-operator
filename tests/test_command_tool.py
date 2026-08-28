from __future__ import annotations

import _thread
import json
import os
import sys
import threading
import time
from pathlib import Path

from code_operator.__main__ import build_registry
from code_operator.config import ProviderConfig
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, ToolCall, ToolResult
from code_operator.policy import CommandPolicy
from code_operator.redaction import Redactor, redact
from code_operator.tools.command import MAX_TOOL_OUTPUT_CHARS, run_command
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient


def approved_policy(tmp_path: Path) -> CommandPolicy:
    return CommandPolicy(tmp_path, approve=lambda _argv, _cwd: True)


def test_nonzero_exit_returns_recoverable_core_contract(tmp_path: Path) -> None:
    result = run_command(
        tool_call_id="nonzero",
        argv=[
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)",
        ],
        timeout_seconds=10,
        policy=approved_policy(tmp_path),
        redactor=Redactor([]),
        environment=os.environ,
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_FAILED"
    assert set(result.details) == {
        "argv",
        "exit_code",
        "stdout",
        "stderr",
        "timed_out",
        "stdout_truncated",
        "stderr_truncated",
    }
    assert result.details["exit_code"] == 7
    assert result.details["stdout"].strip() == "out"
    assert result.details["stderr"].strip() == "err"
    assert result.details["timed_out"] is False


def test_stdout_and_stderr_use_head_tail_truncation(tmp_path: Path) -> None:
    result = run_command(
        tool_call_id="large-output",
        argv=[
            sys.executable,
            "-c",
            (
                "import sys; "
                "print('HEAD-' + 'x'*13000 + '-TAIL'); "
                "print('ERRHEAD-' + 'y'*13000 + '-ERRTAIL', file=sys.stderr)"
            ),
        ],
        timeout_seconds=10,
        policy=approved_policy(tmp_path),
        redactor=Redactor([]),
        environment=os.environ,
    )

    assert result.ok is True
    assert result.details["stdout_truncated"] is True
    assert result.details["stderr_truncated"] is True
    assert result.details["stdout"].startswith("HEAD-")
    assert result.details["stdout"].rstrip().endswith("-TAIL")
    assert result.details["stderr"].startswith("ERRHEAD-")
    assert result.details["stderr"].rstrip().endswith("-ERRTAIL")
    assert "original_chars=" in result.details["stdout"]
    assert len(result.details["stdout"]) <= MAX_TOOL_OUTPUT_CHARS
    assert len(result.details["stderr"]) <= MAX_TOOL_OUTPUT_CHARS


def test_timeout_closes_parent_and_child_output_handles_promptly(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    parent = tmp_path / "parent.py"
    child.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    parent.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    started = time.monotonic()

    result = run_command(
        tool_call_id="tree-timeout",
        argv=[sys.executable, str(parent), str(child)],
        timeout_seconds=1,
        policy=approved_policy(tmp_path),
        redactor=Redactor([]),
        environment=os.environ,
    )
    elapsed = time.monotonic() - started

    assert result.error_code == "COMMAND_TIMEOUT"
    assert result.details["timed_out"] is True
    assert elapsed < 5


def test_process_interrupted_by_console_signal_is_user_aborted(tmp_path: Path) -> None:
    if os.name == "nt":
        interrupt_code = (
            "import ctypes; "
            "ctypes.windll.kernel32.ExitProcess(0xC000013A)"
        )
    else:
        interrupt_code = (
            "import os, signal; os.kill(os.getpid(), signal.SIGINT)"
        )

    result = run_command(
        tool_call_id="signal-abort",
        argv=[sys.executable, "-c", interrupt_code],
        timeout_seconds=10,
        policy=approved_policy(tmp_path),
        redactor=Redactor([]),
        environment=os.environ,
    )

    assert result.error_code == "USER_ABORTED"
    assert result.details["timed_out"] is False


def test_main_interpreter_interrupt_stops_active_process_promptly(
    tmp_path: Path,
) -> None:
    timer = threading.Timer(0.2, _thread.interrupt_main)
    started = time.monotonic()
    timer.start()
    try:
        result = run_command(
            tool_call_id="active-interrupt",
            argv=[sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=10,
            policy=approved_policy(tmp_path),
            redactor=Redactor([]),
            environment=os.environ,
        )
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started

    assert result.error_code == "USER_ABORTED"
    assert result.details["timed_out"] is False
    assert elapsed < 3


def test_user_aborted_tool_stops_loop_before_another_model_request() -> None:
    call = ToolCall(
        id="abort",
        name="run_command",
        arguments_raw=json.dumps({"argv": ["python", "script.py"]}),
    )
    model = FakeModelClient(
        [
            AssistantTurn(
                content=None,
                tool_calls=[call],
                finish_reason="tool_calls",
            ),
            AssertionError("USER_ABORTED 后不得再次调用模型"),
        ]
    )

    def abort_handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="run_command",
            ok=False,
            error_code="USER_ABORTED",
            message="aborted",
            details={},
        )

    result = AgentLoop(model, ToolRegistry({"run_command": abort_handler})).run(
        "run"
    )

    assert result.status == "USER_ABORTED"
    assert len(model.calls) == 1


def test_unified_redact_covers_key_authorization_and_token_assignment() -> None:
    text = (
        "key=real-provider-key; Authorization: Bearer bearer-value; "
        "ACCESS_TOKEN=token-value; CLIENT_SECRET: secret-value"
    )

    cleaned = redact(text, ["real-provider-key"])

    for secret in [
        "real-provider-key",
        "bearer-value",
        "token-value",
        "secret-value",
    ]:
        assert secret not in cleaned
    assert cleaned.count("<REDACTED>") >= 4


def test_jsonl_candidate_object_is_recursively_redacted() -> None:
    candidate = {
        "message": "real-provider-key",
        "headers": {"Authorization": "Bearer bearer-value"},
        "environment": ["ACCESS_TOKEN=token-value", "safe"],
    }
    redactor = Redactor(["real-provider-key"])

    cleaned = redactor.redact_object(candidate)
    serialized = json.dumps(cleaned, ensure_ascii=False)

    for secret in ["real-provider-key", "bearer-value", "token-value"]:
        assert secret not in serialized
    assert candidate["message"] == "real-provider-key"


def test_exception_and_tool_result_use_same_redaction() -> None:
    redactor = Redactor(["real-provider-key"])
    exception_text = redactor.redact(RuntimeError("real-provider-key failed"))
    result = ToolResult(
        tool_call_id="redact",
        name="run_command",
        ok=False,
        error_code="FAILED",
        message=redactor.redact("Authorization: Bearer bearer-value"),
        details={"stderr": redactor.redact("ACCESS_TOKEN=token-value")},
    )

    assert "real-provider-key" not in exception_text
    serialized = result.to_message_content()
    assert "bearer-value" not in serialized
    assert "token-value" not in serialized


def test_six_tool_integration_grep_read_edit_and_pytest(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer():\n    assert answer() == 2\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (tmp_path / ".code-operator").mkdir()
    config = ProviderConfig(
        api_key="integration-provider-key",
        base_url="https://provider.example/v1",
        model="test-model",
    )
    registry = build_registry(
        config,
        tmp_path,
        approve=lambda _argv, _cwd: True,
        environment=os.environ,
    )
    calls = [
        ToolCall(
            "grep",
            "grep",
            json.dumps({"query": "return 1", "file_pattern": "*.py"}),
        ),
        ToolCall("read", "read_file", json.dumps({"path": "app.py"})),
        ToolCall(
            "edit",
            "edit_file",
            json.dumps(
                {
                    "path": "app.py",
                    "old_text": "return 1",
                    "new_text": "return 2",
                }
            ),
        ),
        ToolCall(
            "pytest",
            "run_command",
            json.dumps(
                {"argv": [sys.executable, "-m", "pytest", "-q"], "timeout_seconds": 30}
            ),
        ),
        ToolCall("outside", "read_file", json.dumps({"path": "../outside"})),
        ToolCall("dotenv", "read_file", json.dumps({"path": ".env"})),
        ToolCall(
            "internal",
            "list_dir",
            json.dumps({"path": ".code-operator"}),
        ),
    ]

    results = registry.execute_calls(calls)

    assert [result.ok for result in results[:4]] == [True, True, True, True]
    assert "app.py:2:     return 1" in results[0].details["text"]
    assert "+    return 2" in results[2].details["diff"]
    assert results[3].details["exit_code"] == 0
    assert "1 passed" in results[3].details["stdout"]
    assert [result.error_code for result in results[4:]] == [
        "PATH_DENIED",
        "PATH_DENIED",
        "PATH_DENIED",
    ]
