from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import signal
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from code_operator.__main__ import run_task
from code_operator.config import ConfigError, ProviderConfig, load_provider_config
from code_operator.loop import ModelLike
from code_operator.models import RunResult
from code_operator.redaction import Redactor, sanitized_subprocess_environment


EVAL_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = EVAL_ROOT / "golden_bug" / "project"
TASK_PATH = EVAL_ROOT / "golden_bug" / "task.txt"
ALLOWED_CHANGED_PATHS = frozenset({"pricing.py", "invoice.py"})
OFFICIAL_RUNS = 3
TEST_TIMEOUT_SECONDS = 60
GIT_TIMEOUT_SECONDS = 30
PROCESS_TERMINATION_TIMEOUT_SECONDS = 1.0
RUNTIME_DIRECTORY_NAMES = frozenset({".code-operator", ".pytest_cache", "__pycache__"})
RUNTIME_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})


if os.name == "nt":
    from ctypes import wintypes

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTime", ctypes.c_longlong),
            ("PerJobUserTime", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("Priority", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _WindowsJob:
        _KILL_ON_JOB_CLOSE = 0x2000
        _EXTENDED_LIMIT_INFORMATION = 9
        _PROCESS_SET_QUOTA = 0x0100
        _PROCESS_TERMINATE = 0x0001

        def __init__(self) -> None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
            self._kernel32.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
            ]
            self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            self._kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            self._kernel32.OpenProcess.restype = wintypes.HANDLE
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel32.CloseHandle.restype = wintypes.BOOL
            self._handle = self._kernel32.CreateJobObjectW(None, None)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _JobObjectExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                self._handle,
                self._EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

        def assign(self, process: subprocess.Popen[str]) -> None:
            process_handle = self._kernel32.OpenProcess(
                self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE,
                False,
                process.pid,
            )
            if not process_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not self._kernel32.AssignProcessToJobObject(
                    self._handle, process_handle
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self._kernel32.CloseHandle(process_handle)

        def close(self) -> None:
            if self._handle:
                self._kernel32.CloseHandle(self._handle)
                self._handle = None


class EvalInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


class AgentRunner(Protocol):
    def __call__(
        self,
        config: ProviderConfig,
        *,
        workspace: Path,
        task: str,
        client: ModelLike | None,
    ) -> RunResult: ...


def _ignore_runtime_artifacts(_directory: str, names: list[str]) -> set[str]:
    """Exclude harness/test runtime output when copying the frozen fixture."""
    return {
        name
        for name in names
        if name in RUNTIME_DIRECTORY_NAMES
        or Path(name).suffix.lower() in RUNTIME_FILE_SUFFIXES
    }


def stable_summary(output: str, *, lines: int = 12, characters: int = 2_000) -> str:
    nonempty = [line.rstrip() for line in output.splitlines() if line.strip()]
    return "\n".join(nonempty[-lines:])[-characters:]


def combined_hash(paths: Sequence[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def fixture_hash(root: Path) -> str:
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part in RUNTIME_DIRECTORY_NAMES
            for part in path.relative_to(root).parts[:-1]
        )
        and path.suffix.lower() not in RUNTIME_FILE_SUFFIXES
    ]
    return combined_hash(files, root=root)


def tests_hash(root: Path) -> str:
    files = [path for path in (root / "tests").rglob("*.py") if path.is_file()]
    if not files:
        raise EvalInfrastructureError("冻结项目缺少 Python 测试文件")
    return combined_hash(files, root=root)


def _text_output(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    job: object | None = None,
) -> None:
    if os.name == "nt":
        if job is not None:
            job.close()  # type: ignore[attr-defined]
        elif process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.kill()


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> CommandResult:
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "shell": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": (
            dict(environment)
            if environment is not None
            else sanitized_subprocess_environment(
                api_key=os.environ.get("CODE_OPERATOR_API_KEY")
            )
        ),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process: subprocess.Popen[str] | None = None
    job: object | None = None
    try:
        process = subprocess.Popen(list(argv), **popen_kwargs)
        if os.name == "nt":
            try:
                job = _WindowsJob()
                job.assign(process)  # type: ignore[attr-defined]
            except OSError as error:
                if job is not None:
                    job.close()  # type: ignore[attr-defined]
                process.kill()
                try:
                    process.communicate(timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
                raise EvalInfrastructureError(
                    "无法建立 Windows Job Object 进程生命周期容器"
                ) from error
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate_process_tree(process, job=job)
            job = None
            try:
                stdout, stderr = process.communicate(
                    timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS
                )
            except subprocess.TimeoutExpired as finish_error:
                process.kill()
                try:
                    stdout, stderr = process.communicate(
                        timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired:
                    stdout = _text_output(finish_error.stdout or error.stdout)
                    stderr = _text_output(finish_error.stderr or error.stderr)
            return CommandResult(
                None,
                _text_output(stdout),
                _text_output(stderr),
                True,
            )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            None,
            _text_output(error.stdout),
            _text_output(error.stderr),
            True,
        )
    except OSError as error:
        raise EvalInfrastructureError(f"无法启动命令：{argv[0]}") from error
    finally:
        if job is not None:
            job.close()  # type: ignore[attr-defined]
    assert process is not None
    return CommandResult(process.returncode, _text_output(stdout), _text_output(stderr), False)


def require_process(argv: Sequence[str], *, cwd: Path, timeout: float) -> CommandResult:
    result = run_process(argv, cwd=cwd, timeout=timeout)
    if result.timed_out or result.returncode != 0:
        raise EvalInfrastructureError(
            f"基础设施命令失败：{json.dumps(list(argv), ensure_ascii=False)}"
        )
    return result


def _default_agent_runner(
    config: ProviderConfig,
    *,
    workspace: Path,
    task: str,
    client: ModelLike | None,
) -> RunResult:
    return run_task(
        config,
        workspace=workspace,
        task=task,
        approve=lambda _argv, _cwd: False,
        client=client,
        auto_approve_tests=True,
    )


def _test_result(workspace: Path) -> CommandResult:
    return run_process(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        timeout=TEST_TIMEOUT_SECONDS,
    )


def _git_text(workspace: Path, *arguments: str) -> str:
    result = require_process(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=workspace,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout


def _initialize_git(workspace: Path) -> str:
    require_process(["git", "init", "-q"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS)
    require_process(["git", "add", "--all"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS)
    return require_process(
        ["git", "write-tree"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS
    ).stdout.strip()


def _changed_paths(workspace: Path, baseline_tree: str) -> list[str]:
    encoded_paths = _git_text(
        workspace,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        baseline_tree,
        "--",
    )
    return sorted(path for path in encoded_paths.split("\0") if path)


def run_single(
    config: ProviderConfig,
    *,
    index: int,
    fixture_root: Path = FIXTURE_ROOT,
    task_path: Path = TASK_PATH,
    agent_runner: AgentRunner = _default_agent_runner,
    client: ModelLike | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"code-operator-golden-{index}-") as directory:
        workspace = Path(directory) / "project"
        shutil.copytree(
            fixture_root,
            workspace,
            ignore=_ignore_runtime_artifacts,
        )
        task = task_path.read_text(encoding="utf-8").strip()
        before_tests = tests_hash(workspace)
        baseline_tree = _initialize_git(workspace)
        initial = _test_result(workspace)
        if initial.timed_out:
            raise EvalInfrastructureError("冻结项目初始测试超时")
        if initial.returncode == 0:
            raise EvalInfrastructureError("INVALID_FIXTURE：冻结项目初始测试意外通过")

        agent_error: BaseException | None = None
        agent_result: RunResult | None = None
        try:
            candidate = agent_runner(
                config,
                workspace=workspace,
                task=task,
                client=client,
            )
            if not isinstance(candidate, RunResult):
                raise TypeError("agent_runner must return RunResult")
            agent_result = candidate
        except Exception as error:
            agent_error = error

        final = _test_result(workspace)
        try:
            after_tests: str | None = tests_hash(workspace)
        except EvalInfrastructureError:
            # A run that deletes the tests is a failed run, while a missing
            # fixture test set remains an infrastructure error above.
            after_tests = None
        require_process(
            ["git", "add", "--all"], cwd=workspace, timeout=GIT_TIMEOUT_SECONDS
        )
        changed_paths = _changed_paths(workspace, baseline_tree)
        git_diff = _git_text(
            workspace,
            "diff",
            "--cached",
            "--binary",
            baseline_tree,
            "--",
        )
        failure_reasons: list[str] = []
        status = agent_result.status if agent_result is not None else "HARNESS_AGENT_ERROR"
        if agent_error is not None:
            failure_reasons.append(f"agent_exception:{type(agent_error).__name__}")
        if status != "COMPLETED":
            failure_reasons.append(f"agent_status:{status}")
        if final.timed_out:
            failure_reasons.append("final_test_timeout")
        elif final.returncode != 0:
            failure_reasons.append("final_tests_failed")
        if before_tests != after_tests:
            failure_reasons.append("tests_modified")
        unexpected = sorted(set(changed_paths) - ALLOWED_CHANGED_PATHS)
        if unexpected:
            failure_reasons.append(f"unexpected_paths:{','.join(unexpected)}")
        if not git_diff.strip():
            failure_reasons.append("empty_diff")

        return {
            "index": index,
            "success": not failure_reasons,
            "failure_reasons": failure_reasons,
            "initial_test": {
                "exit_code": initial.returncode,
                "summary": stable_summary(initial.stdout + "\n" + initial.stderr),
            },
            "final_test": {
                "exit_code": final.returncode,
                "timed_out": final.timed_out,
                "summary": stable_summary(final.stdout + "\n" + final.stderr),
            },
            "agent_status": status,
            "model_rounds": agent_result.model_rounds if agent_result else 0,
            "tool_calls": agent_result.tool_calls if agent_result else 0,
            "provider_total_tokens": (
                agent_result.provider_total_tokens if agent_result else None
            ),
            "usage_complete": bool(
                agent_result is not None
                and agent_result.provider_total_tokens is not None
            ),
            "tests_sha256_before": before_tests,
            "tests_sha256_after": after_tests,
            "tests_unchanged": before_tests == after_tests,
            "changed_paths": changed_paths,
            "git_diff": git_diff,
        }


def write_report_exclusive(path: Path, report: dict[str, object], *, api_key: str) -> None:
    if not path.parent.is_dir():
        raise EvalInfrastructureError("报告父目录不存在")
    payload = Redactor([api_key]).redact_object(report)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(encoded)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise EvalInfrastructureError("报告文件已存在，拒绝覆盖") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def run_official_eval(
    config: ProviderConfig,
    *,
    fixture_root: Path = FIXTURE_ROOT,
    task_path: Path = TASK_PATH,
    client_factory: Callable[[int], ModelLike | None] | None = None,
) -> dict[str, object]:
    """Run the fixed three-run evaluation and retain every run result."""
    if config.model != "kimi-k3":
        raise EvalInfrastructureError("正式黄金 Eval 必须使用 kimi-k3")
    if not fixture_root.is_dir() or not task_path.is_file():
        raise EvalInfrastructureError("黄金 Eval fixture 或 task.txt 不存在")

    task = task_path.read_text(encoding="utf-8").strip()
    source_fixture_hash = fixture_hash(fixture_root)
    task_sha256 = hashlib.sha256(task.encode("utf-8")).hexdigest()
    common: dict[str, object] = {
        "schema_version": 1,
        "platform": {
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "configuration": {
            "model": config.model,
            "context_window": config.context_window,
            "max_output_tokens": config.max_output_tokens,
            "max_model_rounds": config.max_model_rounds,
            "max_tool_calls": config.max_tool_calls,
        },
        "fixture_sha256": source_fixture_hash,
        "task_sha256": task_sha256,
    }

    # Check the frozen fixture once before any model client is requested.  The
    # copy ignores runtime output so preflight cannot invalidate itself.
    with tempfile.TemporaryDirectory(prefix="code-operator-golden-preflight-") as directory:
        preflight_workspace = Path(directory) / "project"
        shutil.copytree(
            fixture_root,
            preflight_workspace,
            ignore=_ignore_runtime_artifacts,
        )
        try:
            tests_hash(preflight_workspace)
        except EvalInfrastructureError:
            raise
        preflight = _test_result(preflight_workspace)
    if preflight.timed_out:
        raise EvalInfrastructureError("冻结项目前置测试超时")
    if preflight.returncode == 0:
        return {
            **common,
            "batch_status": "INVALID_FIXTURE",
            "run_count": 0,
            "success_count": 0,
            "video_candidate": False,
            "runs": [],
        }

    runs: list[dict[str, object]] = []
    for index in range(1, OFFICIAL_RUNS + 1):
        # A factory is deliberately called once per run, ensuring no client or
        # conversation state is shared across fresh workspaces.
        try:
            client = client_factory(index) if client_factory is not None else None
        except Exception as error:
            # Client construction is part of this run.  Feed the stable
            # exception into run_single so it still performs independent
            # post-run checks; infrastructure errors from run_single itself
            # must continue to abort the batch.
            def failed_runner(
                _config: ProviderConfig,
                *,
                workspace: Path,
                task: str,
                client: ModelLike | None,
            ) -> RunResult:
                raise error

            runs.append(
                run_single(
                    config,
                    index=index,
                    fixture_root=fixture_root,
                    task_path=task_path,
                    agent_runner=failed_runner,
                    client=None,
                )
            )
            continue

        runs.append(
            run_single(
                config,
                index=index,
                fixture_root=fixture_root,
                task_path=task_path,
                client=client,
            )
        )

    success_count = sum(bool(item["success"]) for item in runs)
    return {
        **common,
        "batch_status": "COMPLETED",
        "run_count": len(runs),
        "success_count": success_count,
        "video_candidate": success_count >= 2,
        "runs": runs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行三次冻结的 code-operator 黄金 Eval。"
    )
    parser.add_argument("--report", required=True, type=Path, help="新的 JSON 报告路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Refuse the destination before loading provider configuration, so an
    # accidental rerun cannot even inspect credentials or contact a provider.
    if args.report.exists() or not args.report.parent.is_dir():
        print("报告路径必须尚不存在且父目录已经存在", file=sys.stderr)
        return 2

    try:
        config = load_provider_config()
        report = run_official_eval(config)
        write_report_exclusive(args.report, report, api_key=config.api_key)
    except (ConfigError, EvalInfrastructureError) as error:
        # Error messages are intentionally stable and contain no provider
        # response, request identifier, or credential material.
        print(Redactor([os.environ.get("CODE_OPERATOR_API_KEY", "")]).redact(error), file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "report": str(args.report),
                "success_count": report["success_count"],
                "run_count": report["run_count"],
                "video_candidate": report["video_candidate"],
            },
            ensure_ascii=False,
        )
    )
    if report["batch_status"] == "INVALID_FIXTURE":
        return 2
    return 0 if report["video_candidate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
