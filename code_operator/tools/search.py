from __future__ import annotations

from pathlib import Path, PurePosixPath

from code_operator.models import ToolResult
from code_operator.policy import ExecutionPolicy, PathPolicyError
from code_operator.redaction import Redactor
from code_operator.tools.filesystem import MAX_FILE_BYTES, MAX_TOOL_OUTPUT_CHARS


MAX_SEARCH_FILES = 1_000


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n... <truncated; original_chars={len(text)}> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + text[-tail:], True


class SearchTools:
    def __init__(
        self,
        policy: ExecutionPolicy,
        *,
        redactor: Redactor | None = None,
    ) -> None:
        self.policy = policy
        self.redactor = redactor or Redactor([])

    def _failure(
        self, tool_call_id: str, code: str, message: str
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            name="grep",
            ok=False,
            error_code=code,
            message=self.redactor.redact(message),
            details={},
        )

    def grep(
        self,
        *,
        tool_call_id: str,
        query: str,
        path: str = ".",
        file_pattern: str | None = None,
        max_results: int = 100,
    ) -> ToolResult:
        try:
            root = self.policy.resolve_workspace_path(path)
        except PathPolicyError as error:
            return self._failure(tool_call_id, "PATH_DENIED", str(error))
        if not root.exists():
            return self._failure(tool_call_id, "PATH_NOT_FOUND", "搜索路径不存在")
        if not root.is_dir() and not root.is_file():
            return self._failure(
                tool_call_id, "INVALID_SEARCH_ROOT", "搜索路径必须是文件或目录"
            )

        normalized_pattern = (
            file_pattern.replace("\\", "/") if file_pattern is not None else None
        )
        pending = [root]
        files_scanned = 0
        matches: list[str] = []
        truncated = False

        while pending:
            candidate = pending.pop()
            try:
                checked = self.policy.resolve_workspace_path(candidate)
            except PathPolicyError:
                continue
            if checked.is_dir():
                try:
                    children = sorted(
                        checked.iterdir(),
                        key=lambda item: item.name.casefold(),
                        reverse=True,
                    )
                except OSError:
                    continue
                pending.extend(children)
                continue
            if not checked.is_file():
                continue

            relative_to_root = (
                checked.name
                if root.is_file()
                else checked.relative_to(root).as_posix()
            )
            if normalized_pattern is not None and not PurePosixPath(
                relative_to_root
            ).match(normalized_pattern):
                continue
            if files_scanned >= MAX_SEARCH_FILES:
                truncated = True
                break
            files_scanned += 1
            try:
                if checked.stat().st_size > MAX_FILE_BYTES:
                    continue
                raw = checked.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "\x00" in text:
                continue
            display_path = checked.relative_to(self.policy.workspace).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query not in line:
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(f"{display_path}:{line_number}: {line}")
            if truncated and len(matches) >= max_results:
                break

        output, char_truncated = _truncate(
            self.redactor.redact("\n".join(matches))
        )
        return ToolResult(
            tool_call_id=tool_call_id,
            name="grep",
            ok=True,
            error_code=None,
            message="搜索完成",
            details={
                "text": output,
                "truncated": truncated or char_truncated,
                "files_scanned": files_scanned,
                "matches": len(matches),
            },
        )
