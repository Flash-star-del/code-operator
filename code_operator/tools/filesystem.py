from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from pathlib import Path

from code_operator.models import ToolResult
from code_operator.policy import PathPolicyError, WorkspacePolicy
from code_operator.redaction import Redactor


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
    ) -> None:
        self.policy = policy
        self.redactor = redactor or Redactor([])
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
