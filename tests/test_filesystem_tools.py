from __future__ import annotations

from pathlib import Path

import pytest

from code_operator.policy import ExecutionPolicy
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
