from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence

from code_operator.models import ToolResult
from code_operator.policy import CommandDecision, CommandPolicy
from code_operator.redaction import Redactor, sanitized_subprocess_environment


MAX_TOOL_OUTPUT_CHARS = 12_000


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n... <truncated; original_chars={len(text)}> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + text[-tail:], True


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.kill()


def _failure(
    tool_call_id: str,
    code: str,
    message: str,
    argv: Sequence[str],
    redactor: Redactor,
) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        name="run_command",
        ok=False,
        error_code=code,
        message=message,
        details={"argv": [redactor.redact(item) for item in argv]},
    )


def run_command(
    *,
    tool_call_id: str,
    argv: list[str],
    timeout_seconds: int = 30,
    policy: CommandPolicy,
    redactor: Redactor,
    environment: Mapping[str, str] | None = None,
) -> ToolResult:
    decision = policy.approve(argv)
    if decision is CommandDecision.DENY:
        return _failure(
            tool_call_id,
            "COMMAND_DENIED",
            "命令被安全策略拒绝",
            argv,
            redactor,
        )
    if decision is not CommandDecision.ALLOW:
        return _failure(
            tool_call_id,
            "USER_DENIED",
            "用户未批准命令",
            argv,
            redactor,
        )

    popen_kwargs: dict[str, object] = {
        "cwd": str(policy.workspace),
        "shell": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": sanitized_subprocess_environment(environment),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(argv, **popen_kwargs)
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        exit_code = process.poll()
        timed_out = False
        aborted = False
    except subprocess.TimeoutExpired:
        if process is None:
            return _failure(
                tool_call_id,
                "COMMAND_START_FAILED",
                "命令未成功启动",
                argv,
                redactor,
            )
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        exit_code = process.poll()
        timed_out = True
        aborted = False
    except KeyboardInterrupt:
        if process is None:
            stdout, stderr = "", ""
            exit_code = None
        else:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            exit_code = process.poll()
        timed_out = False
        aborted = True
    except (OSError, ValueError) as error:
        return _failure(
            tool_call_id,
            "COMMAND_START_FAILED",
            redactor.redact(error),
            argv,
            redactor,
        )

    clean_stdout, stdout_truncated = _truncate(redactor.redact(stdout))
    clean_stderr, stderr_truncated = _truncate(redactor.redact(stderr))
    details: dict[str, object] = {
        "argv": [redactor.redact(item) for item in argv],
        "exit_code": exit_code,
        "stdout": clean_stdout,
        "stderr": clean_stderr,
        "timed_out": timed_out,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if aborted:
        code = "USER_ABORTED"
        message = "用户中止了命令"
    elif timed_out:
        code = "COMMAND_TIMEOUT"
        message = "命令执行超时"
    elif exit_code != 0:
        code = "COMMAND_FAILED"
        message = "命令返回非零退出码"
    else:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="run_command",
            ok=True,
            error_code=None,
            message="命令执行完成",
            details=details,
        )
    return ToolResult(
        tool_call_id=tool_call_id,
        name="run_command",
        ok=False,
        error_code=code,
        message=message,
        details=details,
    )
