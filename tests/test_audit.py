from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from code_operator.__main__ import run_task
from code_operator.audit import JsonlAudit
from code_operator.config import ProviderConfig
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, ToolCall, ToolResult
from code_operator.redaction import Redactor
from code_operator.tools.registry import ToolRegistry
from tests.fakes import FakeModelClient


def fixed_clock() -> datetime:
    return datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


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


def test_audit_jsonl_contains_only_redacted_execution_summaries(
    tmp_path: Path,
) -> None:
    secret = "active-provider-secret"
    call = ToolCall(
        "audit-call",
        "write_file",
        json.dumps(
            {
                "path": f"demo-{secret}.txt",
                "content": f"ACCESS_TOKEN={secret}",
            }
        ),
    )
    model = FakeModelClient(
        [
            AssistantTurn(None, [call], finish_reason="tool_calls"),
            AssistantTurn("done", [], finish_reason="stop"),
        ]
    )

    def handler(*, tool_call_id: str, **_: object) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="write_file",
            ok=True,
            error_code=None,
            message="written",
            details={"exit_code": None, "stdout": f"Bearer {secret}"},
        )

    audit = JsonlAudit(
        tmp_path,
        redactor=Redactor([secret]),
        clock=fixed_clock,
    )

    result = AgentLoop(
        model,
        ToolRegistry({"write_file": handler}),
        audit=audit,
    ).run("create")

    audit_path = tmp_path / ".code-operator" / "audit.jsonl"
    raw = audit_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines()]
    assert result.status == "COMPLETED"
    assert secret not in raw
    assert len(records) == 2
    assert set(records[0]) == {
        "timestamp",
        "event",
        "tool",
        "arguments",
        "ok",
        "error_code",
        "exit_code",
    }
    assert records[0]["event"] == "tool"
    assert records[0]["tool"] == "write_file"
    assert "<REDACTED>" in records[0]["arguments"]
    assert "<omitted; chars=" in records[0]["arguments"]
    assert set(records[1]) == {
        "timestamp",
        "event",
        "usage_available",
        "stop_reason",
    }
    assert records[1]["event"] == "run"
    assert records[1]["usage_available"] is False
    assert records[1]["stop_reason"] == "COMPLETED"


def test_audit_never_records_file_body_arguments(tmp_path: Path) -> None:
    audit = JsonlAudit(tmp_path, redactor=Redactor([]), clock=fixed_clock)
    result = ToolResult("body", "write_file", True, None, "written", {})

    audit.record_tool(
        ToolCall(
            "body",
            "write_file",
            json.dumps({"path": "demo.txt", "content": "PRIVATE_FILE_BODY"}),
        ),
        result,
    )
    audit.record_tool(
        ToolCall(
            "edit",
            "edit_file",
            json.dumps(
                {
                    "path": "demo.txt",
                    "old_text": "ORIGINAL_PRIVATE_BODY",
                    "new_text": "REPLACEMENT_PRIVATE_BODY",
                }
            ),
        ),
        ToolResult("edit", "edit_file", True, None, "edited", {}),
    )

    raw = audit.path.read_text(encoding="utf-8")
    assert "PRIVATE_FILE_BODY" not in raw
    assert "ORIGINAL_PRIVATE_BODY" not in raw
    assert "REPLACEMENT_PRIVATE_BODY" not in raw
    assert "demo.txt" in raw


def test_audit_invalid_arguments_record_shape_not_raw_input(tmp_path: Path) -> None:
    audit = JsonlAudit(tmp_path, redactor=Redactor([]), clock=fixed_clock)
    result = ToolResult("invalid", "write_file", False, "INVALID_ARGUMENTS", "bad", {})

    audit.record_tool(
        ToolCall("invalid", "write_file", '{"content":"BROKEN_PRIVATE_BODY"'),
        result,
    )
    audit.record_tool(
        ToolCall("scalar", "write_file", '"SCALAR_PRIVATE_BODY"'),
        ToolResult("scalar", "write_file", False, "INVALID_ARGUMENTS", "bad", {}),
    )

    raw = audit.path.read_text(encoding="utf-8")
    assert "BROKEN_PRIVATE_BODY" not in raw
    assert "SCALAR_PRIVATE_BODY" not in raw
    assert "invalid-json" in raw
    assert "non-object-json" in raw


def test_audit_write_failure_never_crashes_agent_loop(tmp_path: Path) -> None:
    (tmp_path / ".code-operator").write_text("not a directory", encoding="utf-8")
    audit = JsonlAudit(tmp_path, redactor=Redactor([]), clock=fixed_clock)
    model = FakeModelClient(
        [AssistantTurn("done", [], finish_reason="stop")]
    )

    result = AgentLoop(model, ToolRegistry({}), audit=audit).run("finish")

    assert result.status == "COMPLETED"
    assert audit.write_failed is True


def test_run_task_enables_redacted_audit_by_default(tmp_path: Path) -> None:
    secret = "run-task-provider-secret"
    config = ProviderConfig(
        api_key=secret,
        base_url="https://provider.example/v1",
        model="test-model",
    )
    model = FakeModelClient(
        [AssistantTurn("done", [], finish_reason="stop")]
    )

    result = run_task(
        config,
        workspace=tmp_path,
        task="finish",
        approve=lambda _argv, _cwd: False,
        client=model,
        environment={},
    )

    raw = (tmp_path / ".code-operator" / "audit.jsonl").read_text(
        encoding="utf-8"
    )
    assert result.status == "COMPLETED"
    assert secret not in raw
    assert json.loads(raw)["stop_reason"] == "COMPLETED"


def test_audit_refuses_internal_directory_link_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-audit-outside"
    outside.mkdir()
    make_directory_link(tmp_path / ".code-operator", outside)
    audit = JsonlAudit(tmp_path, redactor=Redactor([]), clock=fixed_clock)
    model = FakeModelClient(
        [AssistantTurn("done", [], finish_reason="stop")]
    )

    result = AgentLoop(model, ToolRegistry({}), audit=audit).run("finish")

    assert result.status == "COMPLETED"
    assert audit.write_failed is True
    assert not (outside / "audit.jsonl").exists()
