from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code_operator.config import ProviderConfig
from code_operator.models import AssistantTurn, RunResult, ToolCall, Usage
from evals import run_golden
from evals.run_golden import (
    EvalInfrastructureError,
    combined_hash,
    fixture_hash,
    OFFICIAL_RUNS,
    run_process,
    run_single,
    run_official_eval,
    stable_summary,
    write_report_exclusive,
)
from tests.fakes import FakeModelClient


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals" / "golden_bug" / "project"

CONFIG = ProviderConfig(
    api_key="golden-secret-value",
    base_url="https://provider.example/v1",
    model="kimi-k3",
)


def run_fixture_tests(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workspace,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_frozen_fixture_starts_with_expected_failures(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    shutil.copytree(FIXTURE, workspace)

    completed = run_fixture_tests(workspace)

    assert completed.returncode == 1
    assert "3 failed, 2 passed" in completed.stdout


def test_fixture_rejects_invalid_tax_percent_in_isolated_subprocess(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    shutil.copytree(FIXTURE, workspace)
    probe = """
from invoice import LineItem, calculate_invoice

accepted = []
for value in (101, True):
    try:
        calculate_invoice(
            [LineItem("probe", 100, 1)], discount_percent=0, tax_percent=value
        )
    except ValueError:
        continue
    accepted.append(repr(value))

if accepted:
    raise AssertionError("accepted invalid tax_percent values: " + ", ".join(accepted))
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=workspace,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_combined_hash_is_path_order_stable(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    first = combined_hash([tmp_path / "b.txt", tmp_path / "a.txt"], root=tmp_path)
    second = combined_hash([tmp_path / "a.txt", tmp_path / "b.txt"], root=tmp_path)
    assert first == second


def test_fixture_hash_ignores_runtime_artifacts_but_tracks_sources(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    source = fixture / "pricing.py"
    source.write_text("subtotal = 1\n", encoding="utf-8")
    before = fixture_hash(fixture)

    (fixture / "__pycache__").mkdir()
    (fixture / "__pycache__" / "x.pyc").write_bytes(b"compiled")
    (fixture / ".pytest_cache").mkdir()
    (fixture / ".pytest_cache" / "nodeids").write_text("runtime", encoding="utf-8")
    assert fixture_hash(fixture) == before

    source.write_text("subtotal = 2\n", encoding="utf-8")
    assert fixture_hash(fixture) != before


def test_stable_summary_keeps_bounded_nonempty_tail() -> None:
    output = "\n".join(f"line-{index}" for index in range(40))
    summary = stable_summary(output)
    assert summary.splitlines()[0] == "line-28"
    assert summary.splitlines()[-1] == "line-39"


def test_report_is_redacted_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    write_report_exclusive(
        path,
        {"message": "Bearer golden-secret-value", "api_key": "golden-secret-value"},
        api_key=CONFIG.api_key,
    )
    text = path.read_text(encoding="utf-8")
    assert "golden-secret-value" not in text
    assert "<REDACTED>" in text
    with pytest.raises(EvalInfrastructureError, match="已存在"):
        write_report_exclusive(path, {"ok": True}, api_key=CONFIG.api_key)


def test_report_redaction_recurses_through_nested_values(tmp_path: Path) -> None:
    path = tmp_path / "nested-report.json"
    write_report_exclusive(
        path,
        {
            "nested": {
                "dict_secret": CONFIG.api_key,
                "items": [
                    f"prefix {CONFIG.api_key}",
                    {"tuple_values": (CONFIG.api_key, "keep this value")},
                ],
                "ordinary": {"answer": 42},
            }
        },
        api_key=CONFIG.api_key,
    )

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert CONFIG.api_key not in text
    assert payload["nested"]["dict_secret"] == "<REDACTED>"
    assert payload["nested"]["items"][0] == "prefix <REDACTED>"
    assert payload["nested"]["items"][1]["tuple_values"] == [
        "<REDACTED>",
        "keep this value",
    ]
    assert payload["nested"]["ordinary"] == {"answer": 42}


def test_report_retries_after_unserializable_payload_without_leftover_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "retry-report.json"
    with pytest.raises(TypeError):
        write_report_exclusive(path, {"invalid": object()}, api_key=CONFIG.api_key)
    assert not path.exists()

    write_report_exclusive(path, {"ok": True}, api_key=CONFIG.api_key)
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_report_cleans_up_when_link_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "interrupted-report.json"
    def interrupting_link(_source, _target) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(run_golden.os, "link", interrupting_link)
    with pytest.raises(KeyboardInterrupt):
        write_report_exclusive(path, {"ok": True}, api_key=CONFIG.api_key)
    assert not path.exists()

    monkeypatch.undo()
    write_report_exclusive(path, {"ok": True}, api_key=CONFIG.api_key)
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_process_timeout_returns_stable_result(tmp_path: Path) -> None:
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        timeout=0.1,
    )
    assert result.timed_out is True
    assert result.returncode is None


def test_process_does_not_inherit_provider_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODE_OPERATOR_API_KEY", "synthetic-api-key")
    monkeypatch.setenv("GOLDEN_DISALLOWED", "synthetic-disallowed-value")
    probe = (
        "import os; "
        "assert not os.environ.get('CODE_OPERATOR_API_KEY'); "
        "assert not os.environ.get('GOLDEN_DISALLOWED'); "
        "assert os.environ.get('PATH'); "
        "assert os.name != 'nt' or os.environ.get('SYSTEMROOT')"
    )

    result = run_process(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        timeout=1,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.timed_out is False


def test_process_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(5)']); child.wait()"
    )
    started = time.monotonic()
    result = run_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout=0.2,
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert elapsed < 2.0


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process_query_limited_information = 0x1000
        still_active = 259
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            if ctypes.get_last_error() == 5:
                return True
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def test_process_timeout_kills_descendant_after_parent_exits(tmp_path: Path) -> None:
    marker = tmp_path / "descendant.pid"
    child_script = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); "
        "time.sleep(3)"
    )
    script = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        "time.sleep(0.01)"
    )
    descendant_pid: int | None = None
    try:
        started = time.monotonic()
        result = run_process(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            timeout=0.2,
        )
        elapsed = time.monotonic() - started
        deadline = time.monotonic() + 1.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        descendant_pid = int(marker.read_text(encoding="utf-8"))
        assert result.timed_out is True
        assert elapsed < 2.0
        deadline = time.monotonic() + 1.0
        while _pid_is_alive(descendant_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_is_alive(descendant_pid)
    finally:
        if descendant_pid is not None and _pid_is_alive(descendant_pid):
            subprocess.run(
                ["taskkill.exe", "/PID", str(descendant_pid), "/T", "/F"]
                if os.name == "nt"
                else ["kill", "-KILL", str(descendant_pid)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )


def completed_result(tokens: int | None = 21) -> RunResult:
    return RunResult("COMPLETED", "done", 4, 5, tokens, 100)


def call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(call_id, name, arguments)


def scripted_fix_client() -> FakeModelClient:
    usage = Usage(10, 2, 12)
    return FakeModelClient(
        [
            AssistantTurn(
                None,
                [
                    call("read-pricing", "read_file", '{"path":"pricing.py"}'),
                    call("read-invoice", "read_file", '{"path":"invoice.py"}'),
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn(
                None,
                [
                    call(
                        "fix-pricing",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "pricing.py",
                                "old_text": "discount_cents = subtotal_cents * discount_percent // 100",
                                "new_text": "discount_cents = round_ratio_half_up(subtotal_cents * discount_percent, 100)",
                            }
                        ),
                    ),
                    call(
                        "fix-invoice",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "invoice.py",
                                "old_text": "tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)",
                                "new_text": "tax_cents = round_ratio_half_up(discounted_cents * tax_percent, 100)",
                            }
                        ),
                    ),
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn(
                None,
                [
                    call(
                        "run-tests",
                        "run_command",
                        '{"argv":["python","-m","pytest","-q"],"timeout_seconds":30}',
                    )
                ],
                finish_reason="tool_calls",
                usage=usage,
            ),
            AssistantTurn("fixed and verified", [], finish_reason="stop", usage=usage),
        ]
    )


def fix_production(workspace: Path) -> None:
    pricing = workspace / "pricing.py"
    pricing.write_text(
        pricing.read_text(encoding="utf-8").replace(
            "discount_cents = subtotal_cents * discount_percent // 100",
            "discount_cents = round_ratio_half_up(subtotal_cents * discount_percent, 100)",
        ),
        encoding="utf-8",
    )
    invoice = workspace / "invoice.py"
    invoice.write_text(
        invoice.read_text(encoding="utf-8").replace(
            "tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)",
            "tax_cents = round_ratio_half_up(discounted_cents * tax_percent, 100)",
        ),
        encoding="utf-8",
    )


def test_single_run_accepts_only_independently_verified_production_fix() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is True
    assert result["failure_reasons"] == []
    assert result["initial_test"]["exit_code"] == 1
    assert result["final_test"]["exit_code"] == 0
    assert result["tests_unchanged"] is True
    assert result["changed_paths"] == ["invoice.py", "pricing.py"]
    assert result["git_diff"]


def test_each_single_run_uses_and_cleans_a_different_workspace() -> None:
    seen: list[Path] = []

    def runner(config, *, workspace, task, client):
        seen.append(workspace)
        fix_production(workspace)
        return completed_result()

    run_single(CONFIG, index=1, agent_runner=runner)
    run_single(CONFIG, index=2, agent_runner=runner)

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(not workspace.exists() for workspace in seen)


def test_single_run_rejects_test_modification_even_when_tests_pass() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        test_file = workspace / "tests" / "test_invoice.py"
        test_file.write_text(test_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "tests_modified" in result["failure_reasons"]


def test_single_run_rejects_unexpected_new_file() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        (workspace / "answer.py").write_text("value = 1\n", encoding="utf-8")
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "unexpected_paths:answer.py" in result["failure_reasons"]


def test_single_run_rejects_completed_without_a_fix() -> None:
    result = run_single(
        CONFIG,
        index=1,
        agent_runner=lambda config, workspace, task, client: completed_result(None),
    )

    assert result["success"] is False
    assert "final_tests_failed" in result["failure_reasons"]
    assert "empty_diff" in result["failure_reasons"]
    assert result["provider_total_tokens"] is None
    assert result["usage_complete"] is False


def test_single_run_handles_deleted_tests_as_a_failed_run() -> None:
    def runner(config, *, workspace, task, client):
        shutil.rmtree(workspace / "tests")
        return completed_result(None)

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "tests_modified" in result["failure_reasons"]
    assert result["tests_unchanged"] is False
    assert result["tests_sha256_after"] is None


def test_single_run_does_not_record_agent_exception_details() -> None:
    sensitive_values = (
        CONFIG.api_key,
        "RID-SECRET",
        "BODY-SECRET",
        "THOUGHT-SECRET",
        "HISTORY-SECRET",
        "ARGS-SECRET",
    )

    def runner(config, *, workspace, task, client):
        raise RuntimeError(
            "request RID-SECRET response BODY-SECRET reasoning THOUGHT-SECRET "
            "history HISTORY-SECRET args ARGS-SECRET api "
            + CONFIG.api_key
        )

    result = run_single(CONFIG, index=1, agent_runner=runner)
    encoded = str(result)

    assert "agent_exception:RuntimeError" in result["failure_reasons"]
    assert all(secret not in encoded for secret in sensitive_values)
    assert all(
        marker not in encoded.lower() for marker in ("request", "response", "reasoning")
    )


def test_single_run_rejects_staged_unexpected_file_and_reports_diff() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        answer = workspace / "answer.py"
        answer.write_text("value = 1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "answer.py"],
            cwd=workspace,
            shell=False,
            check=True,
        )
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is False
    assert "unexpected_paths:answer.py" in result["failure_reasons"]
    assert "answer.py" in result["git_diff"]


def test_single_run_accepts_staged_production_fix_with_complete_diff() -> None:
    def runner(config, *, workspace, task, client):
        fix_production(workspace)
        subprocess.run(
            ["git", "add", "pricing.py", "invoice.py"],
            cwd=workspace,
            shell=False,
            check=True,
        )
        return completed_result()

    result = run_single(CONFIG, index=1, agent_runner=runner)

    assert result["success"] is True
    assert result["failure_reasons"] == []
    assert result["git_diff"]


def test_single_run_rejects_non_run_result_agent_return() -> None:
    result = run_single(
        CONFIG,
        index=1,
        agent_runner=lambda config, workspace, task, client: {},
    )

    assert result["success"] is False
    assert "agent_exception:TypeError" in result["failure_reasons"]
    assert "agent_status:HARNESS_AGENT_ERROR" in result["failure_reasons"]


def test_single_run_uses_real_agent_loop_with_scripted_model() -> None:
    result = run_single(CONFIG, index=1, client=scripted_fix_client())

    assert result["success"] is True
    assert result["model_rounds"] == 4
    assert result["tool_calls"] == 5
    assert result["provider_total_tokens"] == 48


def test_official_eval_runs_three_fresh_successful_workspaces() -> None:
    seen_clients: list[FakeModelClient] = []

    def factory(index: int) -> FakeModelClient:
        client = scripted_fix_client()
        seen_clients.append(client)
        return client

    report = run_official_eval(CONFIG, client_factory=factory)

    assert len(seen_clients) == OFFICIAL_RUNS == 3
    assert report["run_count"] == 3
    assert report["success_count"] == 3
    assert report["batch_status"] == "COMPLETED"
    assert report["video_candidate"] is True
    assert [item["index"] for item in report["runs"]] == [1, 2, 3]


def test_official_eval_reports_invalid_fixture_without_calling_model(
    tmp_path: Path,
) -> None:
    passing_fixture = tmp_path / "passing-project"
    shutil.copytree(FIXTURE, passing_fixture)
    fix_production(passing_fixture)
    calls = 0

    def forbidden_factory(index: int) -> FakeModelClient:
        nonlocal calls
        calls += 1
        return scripted_fix_client()

    report = run_official_eval(
        CONFIG,
        fixture_root=passing_fixture,
        client_factory=forbidden_factory,
    )

    assert calls == 0
    assert report["batch_status"] == "INVALID_FIXTURE"
    assert report["run_count"] == 0
    assert report["video_candidate"] is False


def test_official_eval_keeps_failed_middle_run_and_continues() -> None:
    def factory(index: int) -> FakeModelClient:
        if index == 2:
            return FakeModelClient([RuntimeError("synthetic provider failure")])
        return scripted_fix_client()

    report = run_official_eval(CONFIG, client_factory=factory)

    assert report["run_count"] == 3
    assert report["success_count"] == 2
    assert report["video_candidate"] is True
    assert report["runs"][1]["success"] is False
    assert report["runs"][2]["index"] == 3


def test_written_official_report_has_no_forbidden_fields(tmp_path: Path) -> None:
    report = run_official_eval(
        CONFIG,
        client_factory=lambda index: scripted_fix_client(),
    )
    path = tmp_path / "report.json"
    write_report_exclusive(path, report, api_key=CONFIG.api_key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, ensure_ascii=False).lower()

    assert CONFIG.api_key not in encoded
    for forbidden in (
        "api_key",
        "authorization",
        "request_id",
        "response_body",
        "reasoning",
        "history",
        "tool raw data",
    ):
        assert forbidden not in encoded


def test_cli_refuses_existing_report_before_loading_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "existing.json"
    report.write_text("{}", encoding="utf-8")
    calls = 0

    def forbidden_load() -> ProviderConfig:
        nonlocal calls
        calls += 1
        return CONFIG

    monkeypatch.setattr(run_golden, "load_provider_config", forbidden_load)

    exit_code = run_golden.main(["--report", str(report)])

    assert exit_code == 2
    assert calls == 0
