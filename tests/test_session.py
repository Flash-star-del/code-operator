from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from code_operator.config import ProviderConfig
from code_operator.journal import UndoResult
from code_operator.models import AssistantTurn, ToolCall, Usage
from code_operator.session import AgentSession


def config(**overrides: object) -> ProviderConfig:
    values: dict[str, object] = {
        "api_key": "private-key",
        "base_url": "https://provider.example/v1",
        "model": "test-model",
    }
    values.update(overrides)
    return ProviderConfig(**values)  # type: ignore[arg-type]


def turn(content: str = "done") -> AssistantTurn:
    return AssistantTurn(
        content=content,
        tool_calls=[],
        finish_reason="stop",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def deny(_argv: list[str], _cwd: Path) -> bool:
    return False


class ClosingFakeModel:
    def __init__(self, turns: Sequence[AssistantTurn | Exception]) -> None:
        self._turns = list(turns)
        self.calls: list[
            tuple[list[dict[str, object]], list[dict[str, object]]]
        ] = []
        self.close_calls = 0

    def complete(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> AssistantTurn:
        self.calls.append(
            ([dict(message) for message in messages], [dict(tool) for tool in tools])
        )
        if not self._turns:
            raise AssertionError("ClosingFakeModel 没有剩余响应")
        next_item = self._turns.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    def close(self) -> None:
        self.close_calls += 1


class FailingCloseModel(ClosingFakeModel):
    def close(self) -> None:
        self.close_calls += 1
        raise OSError("close failed")


def assert_close_failure_is_diagnosable(error: BaseException) -> None:
    notes = getattr(error, "__notes__", [])
    assert any("close failed" in note for note in notes) or (
        isinstance(error.__cause__, OSError)
        and "close failed" in str(error.__cause__)
    )


def test_session_reuses_loop_and_external_client_without_closing_it(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([turn("one"), turn("two")])

    with AgentSession(
        config(), workspace=tmp_path, approve=deny, client=client
    ) as session:
        first = session.run("first")
        second = session.run("second")
        session.close()
        assert client.close_calls == 0

    assert first.final_text == "one"
    assert second.final_text == "two"
    assert client.close_calls == 0
    assert client.calls[1][0][-1] == {"role": "user", "content": "second"}
    assert any(item.get("content") == "first" for item in client.calls[1][0])


def test_owned_client_closes_exactly_once_and_closed_session_rejects_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = ClosingFakeModel([turn()])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)

    with AgentSession(config(), workspace=tmp_path, approve=deny) as session:
        assert session.run("task").status == "COMPLETED"
        session.close()
        session.close()
        assert client.close_calls == 1
        with pytest.raises(RuntimeError, match="已关闭"):
            session.run("late")
        with pytest.raises(RuntimeError, match="已关闭"):
            session.undo()
        with pytest.raises(RuntimeError, match="已关闭"):
            session.reset()

    assert client.close_calls == 1
    assert len(client.calls) == 1


def test_owned_client_closes_if_session_construction_fails_after_client_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = ClosingFakeModel([])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)

    class BrokenLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ValueError("invalid loop configuration")

    monkeypatch.setattr("code_operator.session.AgentLoop", BrokenLoop)

    with pytest.raises(ValueError, match="invalid loop configuration"):
        AgentSession(config(), workspace=tmp_path, approve=deny)

    assert client.close_calls == 1


def test_close_failure_does_not_replace_loop_construction_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FailingCloseModel([])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)

    class BrokenLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ValueError("loop failed")

    monkeypatch.setattr("code_operator.session.AgentLoop", BrokenLoop)

    with pytest.raises(ValueError, match="loop failed") as captured:
        AgentSession(config(), workspace=tmp_path, approve=deny)

    assert client.close_calls == 1
    assert_close_failure_is_diagnosable(captured.value)


def test_close_failure_does_not_replace_run_error_during_context_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FailingCloseModel([AssertionError("run failed")])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)

    with pytest.raises(AssertionError, match="run failed") as captured:
        with AgentSession(config(), workspace=tmp_path, approve=deny) as session:
            session.run("task")

    assert client.close_calls == 1
    assert_close_failure_is_diagnosable(captured.value)


def test_explicit_close_reports_failure_but_never_retries_owned_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FailingCloseModel([])
    monkeypatch.setattr("code_operator.session.ModelClient", lambda _config: client)
    session = AgentSession(config(), workspace=tmp_path, approve=deny)

    with pytest.raises(OSError, match="close failed"):
        session.close()
    session.close()

    assert client.close_calls == 1


def test_successful_undo_adds_exactly_one_notice_to_next_user_input(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([turn("continued"), turn("later")])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)
    session.file_tools.write_file(
        tool_call_id="create", path="sample.py", content="x"
    )

    undone = session.undo()

    assert undone.ok is True
    assert session.pending_event_count == 1
    assert len(client.calls) == 0
    session.run("continue")
    assert client.calls[0][0][-1]["content"] == (
        "[本地会话事件]\n"
        "用户已撤销 sample.py 的最近一次直接文件修改。"
        "该文件状态可能与此前对话不同，编辑前必须重新读取。\n\n"
        "[当前用户请求]\ncontinue"
    )
    assert session.pending_event_count == 0

    session.run("second")
    assert client.calls[1][0][-1] == {"role": "user", "content": "second"}
    assert sum(
        "[本地会话事件]" in str(item.get("content", ""))
        for item in client.calls[1][0]
    ) == 1


def test_failed_undo_does_not_queue_notice_or_call_model(tmp_path: Path) -> None:
    client = ClosingFakeModel([])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)

    result = session.undo()

    assert result.error_code == "UNDO_EMPTY"
    assert session.pending_event_count == 0
    assert client.calls == []


def test_local_undo_does_not_append_jsonl_audit(tmp_path: Path) -> None:
    session = AgentSession(
        config(), workspace=tmp_path, approve=deny, client=ClosingFakeModel([])
    )
    audit_path = tmp_path / ".code-operator" / "audit.jsonl"
    session.file_tools.write_file(
        tool_call_id="create", path="sample.py", content="x"
    )
    assert not audit_path.exists()

    result = session.undo()

    assert result.ok is True
    assert not audit_path.exists()
    session.close()


def test_undo_notice_path_is_redacted_terminal_safe_and_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = AgentSession(
        config(api_key="secret-key"),
        workspace=tmp_path,
        approve=deny,
        client=ClosingFakeModel([]),
    )
    unsafe_path = "secret-key/\x1b[31m/" + "x" * 2_000
    monkeypatch.setattr(
        session.file_tools,
        "undo_last_change",
        lambda: UndoResult(
            True,
            None,
            "ok",
            path=unsafe_path,
            source_tool="write_file",
        ),
    )

    assert session.undo().ok is True
    notice = session._pending_events[0]

    assert "secret-key" not in notice
    assert "\x1b" not in notice
    assert r"\x1b" in notice
    assert "original_chars=" in notice
    assert len(notice) <= 500


def test_sync_model_exception_consumes_notice_already_entered_into_history(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([AssertionError("synthetic"), turn("recovered")])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)
    session.file_tools.write_file(
        tool_call_id="create", path="sample.py", content="x"
    )
    assert session.undo().ok is True

    with pytest.raises(AssertionError, match="synthetic"):
        session.run("first try")

    assert session.pending_event_count == 0
    session.run("retry")
    history = client.calls[1][0]
    assert sum(
        "[本地会话事件]" in str(item.get("content", "")) for item in history
    ) == 1
    assert history[-1] == {"role": "user", "content": "retry"}


def test_context_limit_requeues_notice_when_new_user_message_is_rejected(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([])
    session = AgentSession(
        config(context_window=20, max_output_tokens=10),
        workspace=tmp_path,
        approve=deny,
        client=client,
    )
    session.file_tools.write_file(
        tool_call_id="create", path="sample.py", content="x"
    )
    assert session.undo().ok is True

    result = session.run("x" * 1_000)

    assert result.status == "CONTEXT_LIMIT"
    assert session.pending_event_count == 1
    assert client.calls == []


def test_late_context_limit_does_not_requeue_notice_already_seen_by_model(
    tmp_path: Path,
) -> None:
    (tmp_path / "large.txt").write_text("x" * 30_000, encoding="utf-8")
    read_call = ToolCall(
        id="read-large",
        name="read_file",
        arguments_raw='{"path":"large.txt"}',
    )
    client = ClosingFakeModel(
        [
            AssistantTurn(
                content=None,
                tool_calls=[read_call],
                finish_reason="tool_calls",
                usage=Usage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            ),
            turn("recovered"),
        ]
    )
    session = AgentSession(
        config(context_window=4_000, max_output_tokens=200),
        workspace=tmp_path,
        approve=deny,
        client=client,
    )
    session.file_tools.write_file(
        tool_call_id="create", path="sample.py", content="x"
    )
    assert session.undo().ok is True

    first = session.run("inspect")

    assert first.status == "CONTEXT_LIMIT"
    assert first.model_rounds == 1
    assert session.pending_event_count == 0
    second = session.run("continue")
    assert second.status == "COMPLETED"
    assert sum(
        "[本地会话事件]" in str(message.get("content", ""))
        for messages, _tools in client.calls
        for message in messages
    ) == 1


def test_multiple_undo_notices_are_consumed_oldest_first_one_per_run(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([turn("first"), turn("second")])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)
    session.file_tools.write_file(
        tool_call_id="create-first", path="first.py", content="first"
    )
    session.file_tools.write_file(
        tool_call_id="create-second", path="second.py", content="second"
    )
    assert session.undo().path == "second.py"
    assert session.undo().path == "first.py"
    assert session.pending_event_count == 2

    session.run("one")
    assert "second.py" in str(client.calls[0][0][-1]["content"])
    assert "first.py" not in str(client.calls[0][0][-1]["content"])
    assert session.pending_event_count == 1

    session.run("two")
    assert "first.py" in str(client.calls[1][0][-1]["content"])
    assert "second.py" not in str(client.calls[1][0][-1]["content"])
    assert session.pending_event_count == 0


def test_reset_clears_history_reads_journal_and_pending_without_restoring_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("old", encoding="utf-8")
    client = ClosingFakeModel([turn("first"), turn("fresh")])
    session = AgentSession(config(), workspace=tmp_path, approve=deny, client=client)
    session.run("old request")
    assert session.file_tools.read_file(
        tool_call_id="read", path="sample.py"
    ).ok
    assert session.file_tools.write_file(
        tool_call_id="write", path="sample.py", content="changed"
    ).ok
    session.file_tools.write_file(
        tool_call_id="create", path="created.py", content="new"
    )
    assert session.undo().ok is True
    assert session.undo_depth == 1
    assert session.pending_event_count == 1
    audit_path = tmp_path / ".code-operator" / "audit.jsonl"
    audit_before = audit_path.read_bytes()

    session.reset()

    assert target.read_text(encoding="utf-8") == "changed"
    assert session.undo_depth == 0
    assert session.pending_event_count == 0
    assert client.close_calls == 0
    assert audit_path.read_bytes() == audit_before
    denied = session.file_tools.write_file(
        tool_call_id="overwrite", path="sample.py", content="again"
    )
    assert denied.error_code == "READ_REQUIRED"
    session.run("new request")
    assert client.calls[1][0][0]["role"] == "system"
    assert client.calls[1][0][-1] == {"role": "user", "content": "new request"}
    assert len(client.calls[1][0]) == 2


def test_session_builds_one_shared_component_graph(tmp_path: Path) -> None:
    session = AgentSession(
        config(), workspace=tmp_path, approve=deny, client=ClosingFakeModel([])
    )

    assert session.file_tools.policy is session._workspace_policy
    assert session.file_tools.redactor is session._redactor
    assert session.file_tools.journal is session._journal
    assert session._search_tools.policy is session._workspace_policy
    assert session._search_tools.redactor is session._redactor
    assert session._command_policy.workspace == session._workspace_policy.workspace
    assert session._loop._registry is session._registry
    assert session._loop._audit is session._audit
    assert session._loop._trace is session._trace
    assert len(session._registry.tool_schemas()) == 6


def test_session_creates_no_persistent_session_transcript_or_checkpoint(
    tmp_path: Path,
) -> None:
    client = ClosingFakeModel([turn()])
    with AgentSession(
        config(), workspace=tmp_path, approve=deny, client=client
    ) as session:
        session.run("task")

    created = [
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    ]
    assert created == [".code-operator/audit.jsonl"]
    lowered = "\n".join(created).casefold()
    assert "session" not in lowered
    assert "transcript" not in lowered
    assert "checkpoint" not in lowered
