"""Offline contract tests for the frozen real-CLI stream protocols."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from evals.agent_comparison.adapters import run_adapter
from evals.agent_comparison.schema import SystemConfig


def _executable_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config(system_id: str, events: list[object], *, returncode: int = 0) -> SystemConfig:
    jsonl = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    encoded = jsonl.encode("utf-8").hex()
    code = (
        f"import sys; sys.stdout.buffer.write(bytes.fromhex('{encoded}')); "
        f"sys.exit({returncode})"
    )
    return SystemConfig(
        system_id=system_id,
        cli_version="test",
        executable_sha256=_executable_hash(sys.executable),
        model="frozen-test-model",
        auth_type="test",
        argv_template=(sys.executable, "-c", code),
        environment_names=(),
        permission_mode="test",
        output_mode="stream-json",
    )


def _run(tmp_path: Path, system_id: str, events: list[object], *, returncode: int = 0):
    return run_adapter(
        _config(system_id, events, returncode=returncode),
        workspace=tmp_path,
        task="synthetic task text",
        timeout_seconds=2,
        source_environment={"UNAPPROVED_SECRET": "top-secret-token"},
    )


def _kimi_events(*, command: str = "python -m pytest -q") -> list[object]:
    return [
        {
            "role": "assistant",
            "content": "I will inspect C:/private/workspace and run the tests.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "Shell",
                        "arguments": json.dumps({"command": command}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "3 passed; top-secret-token; C:/private/workspace",
        },
        {
            "role": "assistant",
            "content": "Done. pytest passed.",
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "private_text": "top-secret-token",
            },
        },
    ]


def _claude_events(*, command: str = "python -m pytest -q") -> list[object]:
    return [
        {
            "type": "system",
            "subtype": "init",
            "cwd": "C:/private/workspace",
            "session_id": "private-session",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ],
                "usage": {"input_tokens": 13, "output_tokens": 5},
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "3 passed; top-secret-token; C:/private/workspace",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done. pytest passed."}],
                "usage": {"input_tokens": 13, "output_tokens": 5},
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done. pytest passed.",
            "stop_reason": "end_turn",
            "exit_code": 0,
            "total_cost_usd": 0.002,
            "usage": {"input_tokens": 13, "output_tokens": 5},
        },
    ]


def test_kimi_stream_json_accepts_message_objects_and_observes_structured_pytest(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "kimi-code", _kimi_events())

    assert result.stop_reason == "COMPLETED"
    assert result.tests_observed is True
    assert result.usage == {"input_tokens": 11, "output_tokens": 7}
    assert "top-secret-token" not in repr(result)
    assert "private/workspace" not in repr(result)


def test_claude_stream_json_accepts_official_event_envelopes_and_bash_tool_use(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "claude-code", _claude_events())

    assert result.stop_reason == "COMPLETED"
    assert result.tests_observed is True
    assert result.usage == {
        "input_tokens": 13,
        "output_tokens": 5,
        "cost_usd": 0.002,
    }
    assert "top-secret-token" not in repr(result)
    assert "private/workspace" not in repr(result)


@pytest.mark.parametrize(
    ("system_id", "events"),
    (
        ("kimi-code", _kimi_events(command="python -m unittest")),
        ("claude-code", _claude_events(command="python -m unittest")),
    ),
)
def test_stream_protocols_do_not_infer_pytest_from_prose_or_tool_results(
    tmp_path: Path, system_id: str, events: list[object]
) -> None:
    result = _run(tmp_path, system_id, events)

    assert result.stop_reason == "COMPLETED"
    assert result.tests_observed is False


@pytest.mark.parametrize(
    ("system_id", "events"),
    (
        ("kimi-code", _claude_events()),
        ("claude-code", _kimi_events()),
        ("kimi-code", _kimi_events() + [_claude_events()[-1]]),
        ("claude-code", _claude_events() + [_kimi_events()[-1]]),
    ),
)
def test_stream_protocols_reject_foreign_or_mixed_schemas(
    tmp_path: Path, system_id: str, events: list[object]
) -> None:
    result = _run(tmp_path, system_id, events)

    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert result.usage == "unavailable"


@pytest.mark.parametrize("system_id", ("kimi-code", "claude-code", "unknown-cli"))
def test_stream_protocols_reject_malformed_or_unknown_protocols(
    tmp_path: Path, system_id: str
) -> None:
    result = _run(tmp_path, system_id, [{"unexpected": "shape"}])

    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert result.usage == "unavailable"


@pytest.mark.parametrize(
    ("system_id", "events"),
    (
        (
            "kimi-code",
            [
                {"role": "tool", "tool_call_id": "orphan", "content": "pytest passed"},
                {"role": "assistant", "content": "done"},
            ],
        ),
        (
            "kimi-code",
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "Shell",
                                "arguments": json.dumps({"command": "pytest -q"}),
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "done"},
            ],
        ),
        ("claude-code", _claude_events()[1:]),
        ("claude-code", [_claude_events()[0], _claude_events()[1], _claude_events()[-1]]),
    ),
)
def test_stream_protocols_reject_impossible_event_sequences(
    tmp_path: Path, system_id: str, events: list[object]
) -> None:
    result = _run(tmp_path, system_id, events)

    assert result.stop_reason == "INVALID_OUTPUT"
    assert result.tests_observed is False
    assert result.usage == "unavailable"


@pytest.mark.parametrize(
    ("system_id", "events"),
    (("kimi-code", _kimi_events()), ("claude-code", _claude_events())),
)
def test_nonzero_process_exit_precedes_valid_stream_protocol(
    tmp_path: Path, system_id: str, events: list[object]
) -> None:
    result = _run(tmp_path, system_id, events, returncode=7)

    assert result.returncode == 7
    assert result.stop_reason == "NONZERO_EXIT"
