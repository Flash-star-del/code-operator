from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import code_operator.tools.search as search_module
from code_operator.policy import ExecutionPolicy
from code_operator.tools.search import SearchTools


def search_tools(tmp_path: Path) -> SearchTools:
    return SearchTools(ExecutionPolicy(tmp_path))


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


def test_grep_is_case_sensitive_literal_search(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text(
        "alpha\nAlpha\na.b\naxb\n", encoding="utf-8"
    )
    tool = search_tools(tmp_path)

    lower = tool.grep(tool_call_id="lower", query="alpha")
    literal = tool.grep(tool_call_id="literal", query="a.b")

    assert lower.ok is True
    assert lower.details["matches"] == 1
    assert "sample.txt:1: alpha" in lower.details["text"]
    assert "Alpha" not in lower.details["text"]
    assert literal.details["matches"] == 1
    assert "sample.txt:3: a.b" in literal.details["text"]
    assert "axb" not in literal.details["text"]


def test_grep_normalizes_windows_pattern_to_posix_relative_glob(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "src" / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "root.py").write_text("needle\n", encoding="utf-8")

    result = search_tools(tmp_path).grep(
        tool_call_id="pattern",
        query="needle",
        file_pattern="src\\*.py",
    )

    assert result.details["matches"] == 1
    assert result.details["text"] == "src/a.py:1: needle"


def test_grep_respects_result_limit_and_reports_core_contract(tmp_path: Path) -> None:
    (tmp_path / "many.txt").write_text("x\nx\nx\n", encoding="utf-8")

    result = search_tools(tmp_path).grep(
        tool_call_id="limit", query="x", max_results=2
    )

    assert result.ok is True
    assert set(result.details) == {
        "text",
        "truncated",
        "files_scanned",
        "matches",
    }
    assert result.details["matches"] == 2
    assert result.details["truncated"] is True
    assert len(result.details["text"].splitlines()) == 2


def test_grep_stops_at_scan_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ["a.txt", "b.txt", "c.txt"]:
        (tmp_path / name).write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(search_module, "MAX_SEARCH_FILES", 2)

    result = search_tools(tmp_path).grep(tool_call_id="scan", query="needle")

    assert result.details["files_scanned"] == 2
    assert result.details["matches"] == 2
    assert result.details["truncated"] is True


def test_grep_scan_limit_counts_binary_candidates_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.bin").write_bytes(b"\x00needle")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(search_module, "MAX_SEARCH_FILES", 1)

    result = search_tools(tmp_path).grep(tool_call_id="binary-limit", query="needle")

    assert result.details["files_scanned"] == 1
    assert result.details["matches"] == 0
    assert result.details["truncated"] is True


def test_grep_ignores_internal_directories_and_link_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secret.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".code-operator").mkdir()
    (tmp_path / ".code-operator" / "audit.jsonl").write_text(
        "needle\n", encoding="utf-8"
    )
    make_directory_link(tmp_path / "outside-link", outside)
    (tmp_path / "safe.txt").write_text("needle\n", encoding="utf-8")

    result = search_tools(tmp_path).grep(tool_call_id="safe", query="needle")

    assert result.details["matches"] == 1
    assert result.details["text"] == "safe.txt:1: needle"


def test_grep_rejects_external_root(tmp_path: Path) -> None:
    result = search_tools(tmp_path).grep(
        tool_call_id="denied", query="needle", path="../"
    )

    assert result.error_code == "PATH_DENIED"
