from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from pathlib import Path

from code_operator.journal import ChangeJournal, ChangeRecord, UndoResult
from code_operator.models import ToolResult
from code_operator.policy import PathPolicyError, WorkspacePolicy
from code_operator.redaction import Redactor
from code_operator.trace import terminal_safe_text


MAX_FILE_BYTES = 1_000_000
MAX_READ_LINES = 2_000
MAX_TOOL_OUTPUT_CHARS = 12_000


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n... <truncated; original_chars={len(text)}> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + text[-tail:], True


class FileTools:
    def __init__(
        self,
        policy: WorkspacePolicy,
        *,
        redactor: Redactor | None = None,
        journal: ChangeJournal | None = None,
    ) -> None:
        self.policy = policy
        self.redactor = redactor or Redactor([])
        self.journal = journal
        self._complete_read_hashes: dict[Path, str] = {}

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.policy.workspace).as_posix()

    def _failure(
        self,
        tool_call_id: str,
        name: str,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name=name,
            ok=False,
            error_code=code,
            message=self.redactor.redact(message),
            details={} if details is None else details,
        )

    def reset_read_state(self) -> None:
        self._complete_read_hashes.clear()

    def _undo_failure(
        self,
        change: ChangeRecord,
        code: str,
        message: object,
    ) -> UndoResult:
        remaining = 0 if self.journal is None else self.journal.depth
        return UndoResult(
            ok=False,
            error_code=code,
            message=terminal_safe_text(self.redactor.redact(message)),
            path=change.path,
            source_tool=change.source_tool,
            remaining=remaining,
        )

    def _reverse_diff(
        self,
        change: ChangeRecord,
        current: str,
        restored: str,
    ) -> tuple[str, bool]:
        diff = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                restored.splitlines(keepends=True),
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            )
        )
        safe_diff = terminal_safe_text(
            self.redactor.redact(diff), multiline=True
        )
        return _truncate(safe_diff)

    @staticmethod
    def _atomic_restore(path: Path, content: str) -> None:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _has_expected_restored_state(path: Path, change: ChangeRecord) -> bool:
        if change.before_content is None:
            return not path.exists()
        if not path.exists() or not path.is_file() or change.before_hash is None:
            return False
        try:
            return _sha256(path.read_bytes()) == change.before_hash
        except OSError:
            return False

    @staticmethod
    def _normalized_path_identity(path: Path) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _has_original_path_identity(self, change: ChangeRecord, resolved: Path) -> bool:
        lexical = self.policy.workspace / Path(change.path)
        return self._normalized_path_identity(
            lexical
        ) == self._normalized_path_identity(resolved)

    def _apply_restored_read_state(
        self, change: ChangeRecord, resolved: Path
    ) -> None:
        if change.before_hash is None:
            self._complete_read_hashes.pop(resolved, None)
        else:
            self._complete_read_hashes[resolved] = change.before_hash

    def _undo_success(
        self,
        change: ChangeRecord,
        *,
        clean_diff: str,
        diff_truncated: bool,
        interrupted: bool,
    ) -> UndoResult:
        assert self.journal is not None
        message = (
            "检测到中止，但文件撤销已经完成"
            if interrupted
            else "文件修改已撤销"
        )
        return UndoResult(
            ok=True,
            error_code=None,
            message=message,
            path=change.path,
            source_tool=change.source_tool,
            diff=clean_diff,
            diff_truncated=diff_truncated,
            remaining=self.journal.depth,
        )

    def _complete_undo(
        self,
        change: ChangeRecord,
        *,
        resolved: Path,
        clean_diff: str,
        diff_truncated: bool,
        interrupted: bool = False,
    ) -> UndoResult:
        self._apply_restored_read_state(change, resolved)
        assert self.journal is not None
        self.journal.discard(change)
        return self._undo_success(
            change,
            clean_diff=clean_diff,
            diff_truncated=diff_truncated,
            interrupted=interrupted,
        )

    def _reconcile_interrupted_undo(
        self,
        change: ChangeRecord,
        *,
        resolved: Path,
        clean_diff: str,
        diff_truncated: bool,
        interruption: KeyboardInterrupt,
    ) -> UndoResult:
        if not self._has_expected_restored_state(resolved, change):
            raise interruption
        self._apply_restored_read_state(change, resolved)
        assert self.journal is not None
        if self.journal.peek() is change:
            self.journal.discard(change)
        return self._undo_success(
            change,
            clean_diff=clean_diff,
            diff_truncated=diff_truncated,
            interrupted=True,
        )

    def undo_last_change(self) -> UndoResult:
        change = None if self.journal is None else self.journal.peek()
        if change is None:
            return UndoResult(False, "UNDO_EMPTY", "没有可撤销的文件修改")
        try:
            resolved = self.policy.resolve(change.path, for_write=True)
        except PathPolicyError as error:
            return self._undo_failure(change, "PATH_DENIED", error)
        if not self._has_original_path_identity(change, resolved):
            return self._undo_failure(
                change,
                "UNDO_CONFLICT",
                "撤销目标的路径身份已发生变化",
            )

        if not resolved.exists() or not resolved.is_file():
            return self._undo_failure(
                change,
                "UNDO_CONFLICT",
                "撤销目标不存在或已不再是普通文件",
            )
        try:
            current_raw = resolved.read_bytes()
            current = current_raw.decode("utf-8")
        except (UnicodeDecodeError, OSError) as error:
            return self._undo_failure(change, "UNDO_READ_FAILED", error)
        if _sha256(current_raw) != change.after_hash:
            return self._undo_failure(
                change,
                "UNDO_CONFLICT",
                "撤销目标在记录后已发生变化",
            )

        restored = "" if change.before_content is None else change.before_content
        clean_diff, diff_truncated = self._reverse_diff(change, current, restored)
        try:
            if change.before_content is None:
                resolved.unlink()
            else:
                if change.before_hash is None:
                    return self._undo_failure(
                        change,
                        "UNDO_CONFLICT",
                        "撤销记录缺少原文件摘要",
                    )
                self._atomic_restore(resolved, change.before_content)
            return self._complete_undo(
                change,
                resolved=resolved,
                clean_diff=clean_diff,
                diff_truncated=diff_truncated,
            )
        except KeyboardInterrupt as interruption:
            return self._reconcile_interrupted_undo(
                change,
                resolved=resolved,
                clean_diff=clean_diff,
                diff_truncated=diff_truncated,
                interruption=interruption,
            )
        except OSError as error:
            return self._undo_failure(change, "UNDO_WRITE_FAILED", error)

    def list_dir(
        self,
        *,
        tool_call_id: str,
        path: str = ".",
        max_depth: int = 2,
        max_entries: int = 200,
    ) -> ToolResult:
        try:
            resolved = self.policy.resolve_workspace_path(path)
        except PathPolicyError as error:
            return self._failure(
                tool_call_id, "list_dir", "PATH_DENIED", str(error)
            )
        if not resolved.exists() or not resolved.is_dir():
            return self._failure(
                tool_call_id,
                "list_dir",
                "NOT_A_DIRECTORY",
                "目标目录不存在或不是目录",
            )

        lines: list[str] = []
        entry_limit_reached = False

        def visit(directory: Path, depth: int) -> None:
            nonlocal entry_limit_reached
            if entry_limit_reached:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                return
            for entry in entries:
                try:
                    checked = self.policy.resolve_workspace_path(entry)
                except PathPolicyError:
                    continue
                if len(lines) >= max_entries:
                    entry_limit_reached = True
                    return
                is_directory = checked.is_dir()
                suffix = "/" if is_directory else ""
                lines.append(f"{self._relative(checked)}{suffix}")
                if is_directory and depth < max_depth:
                    visit(checked, depth + 1)
                    if entry_limit_reached:
                        return

        visit(resolved, 0)
        text, char_truncated = _truncate(self.redactor.redact("\n".join(lines)))
        return ToolResult(
            tool_call_id=tool_call_id,
            name="list_dir",
            ok=True,
            error_code=None,
            message="目录列出完成",
            details={
                "path": self._relative(resolved),
                "text": text,
                "entries": len(lines),
                "truncated": entry_limit_reached or char_truncated,
            },
        )

    def read_file(
        self,
        *,
        tool_call_id: str,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ToolResult:
        try:
            resolved = self.policy.resolve(path)
        except PathPolicyError as error:
            return self._failure(
                tool_call_id, "read_file", "PATH_DENIED", str(error)
            )
        if not resolved.exists() or not resolved.is_file():
            return self._failure(
                tool_call_id, "read_file", "FILE_NOT_FOUND", "文件不存在"
            )
        try:
            size = resolved.stat().st_size
            if size > MAX_FILE_BYTES:
                return self._failure(
                    tool_call_id,
                    "read_file",
                    "FILE_TOO_LARGE",
                    "文件超过读取大小上限",
                )
            raw = resolved.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._failure(
                tool_call_id, "read_file", "BINARY_FILE", "仅支持 UTF-8 文本文件"
            )
        except OSError as error:
            return self._failure(
                tool_call_id, "read_file", "READ_FAILED", str(error)
            )
        if "\x00" in text:
            return self._failure(
                tool_call_id, "read_file", "BINARY_FILE", "拒绝读取二进制文件"
            )

        lines = text.splitlines()
        total_lines = len(lines)
        first_index = min(start_line - 1, total_lines)
        requested_end = total_lines if end_line is None else min(end_line, total_lines)
        bounded_end = min(requested_end, first_index + MAX_READ_LINES)
        selected = lines[first_index:bounded_end]
        numbered = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start_line)
        )
        clean_content, char_truncated = _truncate(self.redactor.redact(numbered))
        line_truncated = bounded_end < requested_end
        complete = (
            start_line == 1
            and end_line is None
            and bounded_end >= total_lines
            and not line_truncated
            and not char_truncated
        )
        if complete:
            self._complete_read_hashes[resolved] = _sha256(raw)
        actual_end = bounded_end if selected else 0
        return ToolResult(
            tool_call_id=tool_call_id,
            name="read_file",
            ok=True,
            error_code=None,
            message="文件读取完成",
            details={
                "path": self._relative(resolved),
                "content": clean_content,
                "start_line": start_line,
                "end_line": actual_end,
                "total_lines": total_lines,
                "truncated": line_truncated or char_truncated,
                "complete": complete,
            },
        )

    def write_file(
        self,
        *,
        tool_call_id: str,
        path: str,
        content: str,
    ) -> ToolResult:
        try:
            resolved = self.policy.resolve(path, for_write=True)
        except PathPolicyError as error:
            return self._failure(
                tool_call_id, "write_file", "PATH_DENIED", str(error)
            )
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            return self._failure(
                tool_call_id,
                "write_file",
                "FILE_TOO_LARGE",
                "写入内容超过大小上限",
            )
        if not resolved.parent.exists() or not resolved.parent.is_dir():
            return self._failure(
                tool_call_id,
                "write_file",
                "PARENT_NOT_FOUND",
                "目标父目录不存在",
            )

        before = ""
        before_hash: str | None = None
        if resolved.exists():
            if not resolved.is_file():
                return self._failure(
                    tool_call_id, "write_file", "NOT_A_FILE", "目标不是普通文件"
                )
            try:
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    return self._failure(
                        tool_call_id,
                        "write_file",
                        "FILE_TOO_LARGE",
                        "已有文件超过覆盖大小上限",
                    )
                current = resolved.read_bytes()
                before = current.decode("utf-8")
            except UnicodeDecodeError:
                return self._failure(
                    tool_call_id,
                    "write_file",
                    "BINARY_FILE",
                    "拒绝覆盖非 UTF-8 文件",
                )
            except OSError as error:
                return self._failure(
                    tool_call_id, "write_file", "READ_FAILED", str(error)
                )
            before_hash = _sha256(current)
            expected_hash = self._complete_read_hashes.get(resolved)
            if expected_hash is None:
                return self._failure(
                    tool_call_id,
                    "write_file",
                    "READ_REQUIRED",
                    "覆盖已有文件前必须先完整读取",
                )
            if before_hash != expected_hash:
                return self._failure(
                    tool_call_id,
                    "write_file",
                    "STALE_FILE",
                    "文件在读取后已发生变化，请重新读取",
                )

        relative = self._relative(resolved)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        clean_diff, diff_truncated = _truncate(self.redactor.redact(diff))
        try:
            if before_hash is None:
                with resolved.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
            else:
                temporary_name: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        newline="",
                        dir=resolved.parent,
                        prefix=f".{resolved.name}.",
                        suffix=".tmp",
                        delete=False,
                    ) as stream:
                        temporary_name = stream.name
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary_name, resolved)
                    temporary_name = None
                finally:
                    if temporary_name is not None:
                        Path(temporary_name).unlink(missing_ok=True)
        except FileExistsError:
            return self._failure(
                tool_call_id,
                "write_file",
                "FILE_EXISTS",
                "目标在创建前已存在，请先读取",
            )
        except OSError as error:
            return self._failure(
                tool_call_id, "write_file", "WRITE_FAILED", str(error)
            )

        after_hash = _sha256(encoded)
        self._complete_read_hashes[resolved] = after_hash
        if self.journal is not None and before_hash != after_hash:
            self.journal.record(
                ChangeRecord(
                    path=relative,
                    before_content=None if before_hash is None else before,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    source_tool="write_file",
                )
            )
        return ToolResult(
            tool_call_id=tool_call_id,
            name="write_file",
            ok=True,
            error_code=None,
            message="文件写入完成",
            details={
                "path": relative,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "diff": clean_diff,
                "diff_truncated": diff_truncated,
            },
        )

    def edit_file(
        self,
        *,
        tool_call_id: str,
        path: str,
        old_text: str,
        new_text: str,
    ) -> ToolResult:
        try:
            resolved = self.policy.resolve_workspace_path(path, for_write=True)
        except PathPolicyError as error:
            return self._failure(
                tool_call_id, "edit_file", "PATH_DENIED", str(error)
            )
        if not resolved.exists() or not resolved.is_file():
            return self._failure(
                tool_call_id, "edit_file", "FILE_NOT_FOUND", "文件不存在"
            )
        try:
            if resolved.stat().st_size > MAX_FILE_BYTES:
                return self._failure(
                    tool_call_id,
                    "edit_file",
                    "FILE_TOO_LARGE",
                    "文件超过编辑大小上限",
                )
            raw = resolved.read_bytes()
            before = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._failure(
                tool_call_id,
                "edit_file",
                "BINARY_FILE",
                "拒绝编辑非 UTF-8 文件",
            )
        except OSError as error:
            return self._failure(
                tool_call_id, "edit_file", "READ_FAILED", str(error)
            )

        before_hash = _sha256(raw)
        expected_hash = self._complete_read_hashes.get(resolved)
        if expected_hash is None:
            return self._failure(
                tool_call_id,
                "edit_file",
                "READ_REQUIRED",
                "编辑已有文件前必须先完整读取",
            )
        if expected_hash != before_hash:
            return self._failure(
                tool_call_id,
                "edit_file",
                "STALE_FILE",
                "文件在读取后已发生变化，请重新读取",
            )

        occurrences = before.count(old_text)
        if occurrences == 0:
            return self._failure(
                tool_call_id,
                "edit_file",
                "OLD_TEXT_NOT_FOUND",
                "old_text 在文件中未找到",
            )
        if occurrences != 1:
            return self._failure(
                tool_call_id,
                "edit_file",
                "OLD_TEXT_NOT_UNIQUE",
                "old_text 在文件中不是唯一匹配",
            )
        after = before.replace(old_text, new_text, 1)
        encoded = after.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            return self._failure(
                tool_call_id,
                "edit_file",
                "FILE_TOO_LARGE",
                "编辑后的文件超过大小上限",
            )

        relative = self._relative(resolved)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        clean_diff, diff_truncated = _truncate(self.redactor.redact(diff))
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=resolved.parent,
                prefix=f".{resolved.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(after)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, resolved)
            temporary_name = None
        except OSError as error:
            return self._failure(
                tool_call_id, "edit_file", "WRITE_FAILED", str(error)
            )
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

        after_hash = _sha256(encoded)
        self._complete_read_hashes[resolved] = after_hash
        if self.journal is not None and before_hash != after_hash:
            self.journal.record(
                ChangeRecord(
                    path=relative,
                    before_content=before,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    source_tool="edit_file",
                )
            )
        return ToolResult(
            tool_call_id=tool_call_id,
            name="edit_file",
            ok=True,
            error_code=None,
            message="文件编辑完成",
            details={
                "path": relative,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "diff": clean_diff,
                "diff_truncated": diff_truncated,
            },
        )
