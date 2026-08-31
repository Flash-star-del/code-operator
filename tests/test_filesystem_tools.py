from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from code_operator.journal import ChangeJournal, ChangeRecord
from code_operator.policy import ExecutionPolicy, PathPolicyError
from code_operator.redaction import Redactor
from code_operator.tools.filesystem import MAX_READ_LINES, FileTools


def tools(tmp_path: Path) -> FileTools:
    return FileTools(ExecutionPolicy(tmp_path))


def test_list_dir_respects_entry_limit_depth_and_internal_ignores(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / ".code-operator").mkdir()
    (tmp_path / ".code-operator" / "audit.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "deep.py").write_text("deep", encoding="utf-8")
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")

    depth_zero = tools(tmp_path).list_dir(
        tool_call_id="depth", path=".", max_depth=0, max_entries=20
    )
    limited = tools(tmp_path).list_dir(
        tool_call_id="limit", path=".", max_depth=5, max_entries=2
    )

    assert depth_zero.ok is True
    assert "root.txt" in depth_zero.details["text"]
    assert "src/" in depth_zero.details["text"]
    assert "src/a.py" not in depth_zero.details["text"]
    assert ".git" not in depth_zero.details["text"]
    assert ".code-operator" not in depth_zero.details["text"]
    assert limited.details["entries"] == 2
    assert limited.details["truncated"] is True
    assert len(limited.details["text"].splitlines()) == 2


def test_list_dir_core_contract_and_path_denial(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    file_tools = tools(tmp_path)

    result = file_tools.list_dir(tool_call_id="list", path=".")
    denied = file_tools.list_dir(tool_call_id="denied", path="../")

    assert result.ok is True
    assert set(result.details) >= {"path", "text", "entries", "truncated"}
    assert result.details["path"] == "."
    assert denied.error_code == "PATH_DENIED"


def test_read_file_pagination_never_authorizes_overwrite(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    file_tools = tools(tmp_path)

    page = file_tools.read_file(
        tool_call_id="page", path="sample.txt", start_line=2, end_line=3
    )
    overwrite = file_tools.write_file(
        tool_call_id="write", path="sample.txt", content="changed\n"
    )

    assert page.ok is True
    assert page.details == {
        "path": "sample.txt",
        "content": "2: two\n3: three",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 3,
        "truncated": False,
        "complete": False,
    }
    assert overwrite.error_code == "READ_REQUIRED"


def test_read_file_line_limit_and_binary_rejection(tmp_path: Path) -> None:
    (tmp_path / "many.txt").write_text(
        "".join(f"line-{index}\n" for index in range(MAX_READ_LINES + 1)),
        encoding="utf-8",
    )
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    file_tools = tools(tmp_path)

    many = file_tools.read_file(tool_call_id="many", path="many.txt")
    binary = file_tools.read_file(tool_call_id="binary", path="binary.bin")

    assert many.details["end_line"] <= MAX_READ_LINES
    assert many.details["truncated"] is True
    assert many.details["complete"] is False
    assert binary.error_code == "BINARY_FILE"


def test_complete_read_and_write_contract_with_stale_detection(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old\n", encoding="utf-8")
    file_tools = tools(tmp_path)

    read = file_tools.read_file(tool_call_id="read", path="sample.txt")
    target.write_text("external\n", encoding="utf-8")
    stale = file_tools.write_file(
        tool_call_id="stale", path="sample.txt", content="new\n"
    )
    reread = file_tools.read_file(tool_call_id="reread", path="sample.txt")
    written = file_tools.write_file(
        tool_call_id="write", path="sample.txt", content="new\n"
    )

    assert read.details["complete"] is True
    assert stale.error_code == "STALE_FILE"
    assert reread.details["complete"] is True
    assert written.ok is True
    assert set(written.details) == {
        "path",
        "before_hash",
        "after_hash",
        "diff",
        "diff_truncated",
    }
    assert target.read_text(encoding="utf-8") == "new\n"


def test_new_file_creation_is_exclusive(tmp_path: Path) -> None:
    file_tools = tools(tmp_path)

    created = file_tools.write_file(
        tool_call_id="create", path="new.txt", content="first"
    )
    second = tools(tmp_path).write_file(
        tool_call_id="second", path="new.txt", content="second"
    )

    assert created.ok is True
    assert created.details["before_hash"] is None
    assert second.error_code == "READ_REQUIRED"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "first"


@pytest.mark.parametrize(
    ("contents", "old_text", "error_code"),
    [
        ("alpha\n", "missing", "OLD_TEXT_NOT_FOUND"),
        ("same same\n", "same", "OLD_TEXT_NOT_UNIQUE"),
    ],
)
def test_edit_file_requires_exactly_one_match(
    tmp_path: Path, contents: str, old_text: str, error_code: str
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text(contents, encoding="utf-8")
    file_tools = tools(tmp_path)
    file_tools.read_file(tool_call_id="read", path="sample.txt")

    result = file_tools.edit_file(
        tool_call_id="edit",
        path="sample.txt",
        old_text=old_text,
        new_text="replacement",
    )

    assert result.error_code == error_code
    assert target.read_text(encoding="utf-8") == contents


def test_edit_file_requires_complete_unchanged_read(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("alpha\n", encoding="utf-8")
    file_tools = tools(tmp_path)

    unread = file_tools.edit_file(
        tool_call_id="unread",
        path="sample.txt",
        old_text="alpha",
        new_text="beta",
    )
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    target.write_text("external\n", encoding="utf-8")
    stale = file_tools.edit_file(
        tool_call_id="stale",
        path="sample.txt",
        old_text="external",
        new_text="beta",
    )

    assert unread.error_code == "READ_REQUIRED"
    assert stale.error_code == "STALE_FILE"
    assert target.read_text(encoding="utf-8") == "external\n"


def test_unique_edit_returns_core_contract_and_unified_diff(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    file_tools = tools(tmp_path)
    read = file_tools.read_file(tool_call_id="read", path="sample.py")

    result = file_tools.edit_file(
        tool_call_id="edit",
        path="sample.py",
        old_text="value = 1",
        new_text="value = 2",
    )

    assert read.details["complete"] is True
    assert result.ok is True
    assert set(result.details) == {
        "path",
        "before_hash",
        "after_hash",
        "diff",
        "diff_truncated",
    }
    assert "-value = 1" in result.details["diff"]
    assert "+value = 2" in result.details["diff"]
    assert result.details["diff_truncated"] is False
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_edit_diff_is_head_tail_truncated(tmp_path: Path) -> None:
    file_tools = tools(tmp_path)
    old_text = "a" * 8_000
    new_text = "b" * 8_000
    created = file_tools.write_file(
        tool_call_id="create", path="large.txt", content=old_text
    )

    edited = file_tools.edit_file(
        tool_call_id="edit",
        path="large.txt",
        old_text=old_text,
        new_text=new_text,
    )

    assert created.ok is True
    assert edited.ok is True
    assert edited.details["diff_truncated"] is True
    assert "original_chars=" in edited.details["diff"]
    assert len(edited.details["diff"]) <= 12_000


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(
    path: str,
    *,
    before: str | None,
    after: str,
    source_tool: str = "write_file",
) -> ChangeRecord:
    return ChangeRecord(
        path=path,
        before_content=before,
        before_hash=None if before is None else _hash_text(before),
        after_hash=_hash_text(after),
        source_tool=source_tool,
    )


def _make_directory_link(link: Path, target: Path) -> None:
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


def test_undo_restores_existing_file_and_updates_read_authorization(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    target.write_text("old\n", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.py")

    changed = file_tools.write_file(
        tool_call_id="write", path="sample.py", content="new\n"
    )
    undone = file_tools.undo_last_change()

    assert changed.ok is True and undone.ok is True
    assert undone.error_code is None
    assert undone.message == "文件修改已撤销"
    assert undone.path == "sample.py"
    assert undone.source_tool == "write_file"
    assert undone.remaining == 0
    assert target.read_text(encoding="utf-8") == "old\n"
    assert "-new" in undone.diff and "+old" in undone.diff
    rewritten = file_tools.write_file(
        tool_call_id="rewrite", path="sample.py", content="third\n"
    )
    assert rewritten.ok is True


def test_edit_file_records_its_source_tool_for_undo(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.py")

    edited = file_tools.edit_file(
        tool_call_id="edit",
        path="sample.py",
        old_text="value = 1",
        new_text="value = 2",
    )
    undone = file_tools.undo_last_change()

    assert edited.ok is True
    assert undone.ok is True
    assert undone.source_tool == "edit_file"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_undo_deletes_new_file_and_supports_two_consecutive_undos(
    tmp_path: Path,
) -> None:
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="one", path="one.txt", content="one")
    file_tools.write_file(tool_call_id="two", path="two.txt", content="two")

    assert file_tools.undo_last_change().path == "two.txt"
    assert not (tmp_path / "two.txt").exists()
    assert file_tools.undo_last_change().path == "one.txt"
    assert not (tmp_path / "one.txt").exists()


def test_undo_refuses_external_change_and_keeps_record(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    target.write_text("external", encoding="utf-8")

    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_CONFLICT"
    assert result.remaining == 1
    assert journal.depth == 1
    assert target.read_text(encoding="utf-8") == "external"


def test_noop_write_does_not_create_journal_record(tmp_path: Path) -> None:
    target = tmp_path / "same.txt"
    target.write_text("same", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="same.txt")

    result = file_tools.write_file(
        tool_call_id="write", path="same.txt", content="same"
    )

    assert result.ok is True
    assert journal.depth == 0


def test_reset_read_state_revokes_existing_overwrite_authorization(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old", encoding="utf-8")
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=ChangeJournal())
    file_tools.read_file(tool_call_id="read", path="sample.txt")

    file_tools.reset_read_state()
    result = file_tools.write_file(
        tool_call_id="write", path="sample.txt", content="new"
    )

    assert result.error_code == "READ_REQUIRED"
    assert target.read_text(encoding="utf-8") == "old"


def test_undo_empty_stack_has_exact_error_contract(tmp_path: Path) -> None:
    result = FileTools(
        ExecutionPolicy(tmp_path), journal=ChangeJournal()
    ).undo_last_change()

    assert result.ok is False
    assert result.error_code == "UNDO_EMPTY"
    assert result.message == "没有可撤销的文件修改"
    assert result.remaining == 0


@pytest.mark.parametrize("replacement", ["missing", "directory", "different"])
def test_undo_target_conflicts_keep_record_and_disk_state(
    tmp_path: Path, replacement: str
) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    if replacement == "missing":
        target.unlink()
    elif replacement == "directory":
        target.unlink()
        target.mkdir()
    else:
        target.write_text("external", encoding="utf-8")

    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_CONFLICT"
    assert result.remaining == 1
    assert journal.depth == 1
    if replacement == "directory":
        assert target.is_dir()
    elif replacement == "different":
        assert target.read_text(encoding="utf-8") == "external"
    else:
        assert not target.exists()


def test_undo_non_utf8_target_reports_read_failure_and_keeps_record(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    target.write_bytes(b"\xff\xfe")

    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_READ_FAILED"
    assert result.remaining == 1
    assert journal.depth == 1
    assert target.read_bytes() == b"\xff\xfe"


def test_undo_policy_denial_redacts_message_and_keeps_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "current-api-key"
    journal = ChangeJournal()
    journal.record(_record(".git/config", before=None, after="agent"))
    file_tools = FileTools(
        ExecutionPolicy(tmp_path),
        journal=journal,
        redactor=Redactor([secret]),
    )

    def deny_path(_path: object, *, for_write: bool = False) -> Path:
        assert for_write is True
        raise PathPolicyError(
            f"CODE_OPERATOR_API_KEY={secret}; Authorization: Bearer bearer-value;\x1b"
        )

    monkeypatch.setattr(file_tools.policy, "resolve", deny_path)

    result = file_tools.undo_last_change()

    assert result.error_code == "PATH_DENIED"
    assert result.remaining == 1
    assert secret not in result.message
    assert "bearer-value" not in result.message
    assert "Bearer <REDACTED>" in result.message
    assert "\x1b" not in result.message
    assert r"\x1b" in result.message
    assert journal.depth == 1


def test_undo_rejects_path_that_now_resolves_through_external_link(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "sample.txt").write_text("agent", encoding="utf-8")
    link = workspace / "linked-dir"
    _make_directory_link(link, outside)
    journal = ChangeJournal()
    journal.record(_record("linked-dir/sample.txt", before=None, after="agent"))
    file_tools = FileTools(ExecutionPolicy(workspace), journal=journal)

    result = file_tools.undo_last_change()

    assert result.error_code == "PATH_DENIED"
    assert result.remaining == 1
    assert journal.depth == 1
    assert (outside / "sample.txt").read_text(encoding="utf-8") == "agent"


def test_undo_restore_replace_failure_keeps_new_content_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    file_tools.write_file(tool_call_id="write", path="sample.txt", content="new")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_WRITE_FAILED"
    assert result.remaining == 1
    assert "replace failed" in result.message
    assert target.read_text(encoding="utf-8") == "new"
    assert journal.depth == 1


def test_undo_new_file_unlink_failure_keeps_file_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    original_unlink = Path.unlink

    def fail_target_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == target:
            raise OSError("unlink failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)
    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_WRITE_FAILED"
    assert result.remaining == 1
    assert "unlink failed" in result.message
    assert target.read_text(encoding="utf-8") == "agent"
    assert journal.depth == 1


def test_undo_reverse_diff_is_truncated_redacted_and_terminal_safe(
    tmp_path: Path,
) -> None:
    secret = "current-api-key"
    before = f"CODE_OPERATOR_API_KEY={secret}\nold\x1b[31m" + "a" * 8_000
    after = "Authorization: Bearer bearer-value\nnew\x00" + "b" * 8_000
    target = tmp_path / "sample.txt"
    target.write_text(before, encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(
        ExecutionPolicy(tmp_path),
        journal=journal,
        redactor=Redactor([secret]),
    )
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    file_tools.write_file(tool_call_id="write", path="sample.txt", content=after)

    result = file_tools.undo_last_change()

    assert result.ok is True
    assert result.diff_truncated is True
    assert len(result.diff) <= 12_000
    assert "original_chars=" in result.diff
    assert secret not in result.diff
    assert "bearer-value" not in result.diff
    assert "Bearer <REDACTED>" in result.diff
    assert "\x1b" not in result.diff
    assert "\x00" not in result.diff
    assert r"\x1b" in result.diff or r"\x00" in result.diff


def test_undo_completed_replace_then_keyboard_interrupt_finalizes_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    file_tools.write_file(tool_call_id="write", path="sample.txt", content="new")
    real_replace = os.replace

    def replace_then_interrupt(source: object, destination: object) -> None:
        real_replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", replace_then_interrupt)
    result = file_tools.undo_last_change()

    assert result.ok is True
    assert result.error_code is None
    assert "中止" in result.message and "完成" in result.message
    assert result.remaining == 0
    assert target.read_text(encoding="utf-8") == "old"
    monkeypatch.setattr(os, "replace", real_replace)
    rewritten = file_tools.write_file(
        tool_call_id="rewrite", path="sample.txt", content="third"
    )
    assert rewritten.ok is True


def test_undo_keyboard_interrupt_before_replace_keeps_record_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("old", encoding="utf-8")
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.read_file(tool_call_id="read", path="sample.txt")
    file_tools.write_file(tool_call_id="write", path="sample.txt", content="new")

    def interrupt_replace(_source: object, _destination: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt_replace)
    with pytest.raises(KeyboardInterrupt):
        file_tools.undo_last_change()

    assert journal.depth == 1
    assert target.read_text(encoding="utf-8") == "new"


def test_undo_completed_unlink_then_keyboard_interrupt_finalizes_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    real_unlink = Path.unlink

    def unlink_then_interrupt(path: Path, *args: object, **kwargs: object) -> None:
        real_unlink(path, *args, **kwargs)
        if path == target:
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "unlink", unlink_then_interrupt)
    result = file_tools.undo_last_change()

    assert result.ok is True
    assert "中止" in result.message and "完成" in result.message
    assert result.remaining == 0
    assert not target.exists()


@pytest.mark.parametrize("existing", [False, True], ids=["new-file", "existing-file"])
def test_undo_rejects_internal_parent_link_retarget_with_matching_hash(
    tmp_path: Path, existing: bool
) -> None:
    original_parent = tmp_path / "original"
    other_parent = tmp_path / "other"
    original_parent.mkdir()
    other_parent.mkdir()
    target = original_parent / "sample.txt"
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    if existing:
        target.write_text("old", encoding="utf-8")
        file_tools.read_file(tool_call_id="read", path="original/sample.txt")
        file_tools.write_file(
            tool_call_id="write", path="original/sample.txt", content="agent"
        )
    else:
        file_tools.write_file(
            tool_call_id="create", path="original/sample.txt", content="agent"
        )
    target.unlink()
    original_parent.rmdir()
    other_target = other_parent / "sample.txt"
    other_target.write_text("agent", encoding="utf-8")
    _make_directory_link(original_parent, other_parent)

    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_CONFLICT"
    assert result.remaining == 1
    assert journal.depth == 1
    assert other_target.read_text(encoding="utf-8") == "agent"


def test_undo_rejects_file_identity_retarget_with_matching_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = ChangeJournal()
    policy = ExecutionPolicy(tmp_path)
    file_tools = FileTools(policy, journal=journal)
    file_tools.write_file(tool_call_id="create", path="sample.txt", content="agent")
    original_target = tmp_path / "sample.txt"
    other_target = tmp_path / "other.txt"
    original_target.unlink()
    other_target.write_text("agent", encoding="utf-8")
    real_resolve = policy.resolve

    def redirect_file(path: object, *, for_write: bool = False) -> Path:
        if str(path) == "sample.txt":
            return other_target.resolve()
        return real_resolve(path, for_write=for_write)

    monkeypatch.setattr(policy, "resolve", redirect_file)
    result = file_tools.undo_last_change()

    assert result.error_code == "UNDO_CONFLICT"
    assert result.remaining == 1
    assert journal.depth == 1
    assert other_target.read_text(encoding="utf-8") == "agent"


def test_keyboard_interrupt_before_discard_reconciles_exact_record_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="earlier", path="earlier.txt", content="one")
    file_tools.write_file(tool_call_id="latest", path="latest.txt", content="two")
    latest = journal.peek()
    original_discard = journal.discard
    calls = 0

    def interrupt_once_before_discard(expected: ChangeRecord) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        original_discard(expected)

    monkeypatch.setattr(journal, "discard", interrupt_once_before_discard)
    result = file_tools.undo_last_change()

    assert result.ok is True
    assert "中止" in result.message and "完成" in result.message
    assert calls == 2
    assert journal.depth == 1
    assert journal.peek() is not latest
    assert journal.peek() is not None and journal.peek().path == "earlier.txt"
    assert not (tmp_path / "latest.txt").exists()
    assert (tmp_path / "earlier.txt").read_text(encoding="utf-8") == "one"
    monkeypatch.setattr(journal, "discard", original_discard)
    next_result = file_tools.undo_last_change()
    assert next_result.ok is True and next_result.path == "earlier.txt"
    assert journal.depth == 0


def test_keyboard_interrupt_after_discard_never_discards_earlier_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = ChangeJournal()
    file_tools = FileTools(ExecutionPolicy(tmp_path), journal=journal)
    file_tools.write_file(tool_call_id="earlier", path="earlier.txt", content="one")
    file_tools.write_file(tool_call_id="latest", path="latest.txt", content="two")
    latest = journal.peek()
    original_discard = journal.discard
    calls = 0

    def discard_then_interrupt(expected: ChangeRecord) -> None:
        nonlocal calls
        calls += 1
        original_discard(expected)
        raise KeyboardInterrupt

    monkeypatch.setattr(journal, "discard", discard_then_interrupt)
    result = file_tools.undo_last_change()

    assert result.ok is True
    assert "中止" in result.message and "完成" in result.message
    assert calls == 1
    assert journal.depth == 1
    assert journal.peek() is not latest
    assert journal.peek() is not None and journal.peek().path == "earlier.txt"
    assert not (tmp_path / "latest.txt").exists()
    assert (tmp_path / "earlier.txt").read_text(encoding="utf-8") == "one"
    monkeypatch.setattr(journal, "discard", original_discard)
    next_result = file_tools.undo_last_change()
    assert next_result.ok is True and next_result.path == "earlier.txt"
    assert journal.depth == 0
