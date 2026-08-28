from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor


MAX_ARGUMENT_SUMMARY_CHARS = 500
_FILE_BODY_ARGUMENTS = {"content", "old_text", "new_text"}


class JsonlAudit:
    def __init__(
        self,
        workspace: str | Path,
        *,
        redactor: Redactor,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._workspace = Path(workspace).resolve(strict=True)
        self.path = self._workspace / ".code-operator" / "audit.jsonl"
        self._redactor = redactor
        self._clock = clock
        self.write_failed = False

    def _timestamp(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def _argument_summary(self, arguments_raw: str) -> str:
        try:
            decoded = json.loads(arguments_raw)
        except (json.JSONDecodeError, TypeError):
            cleaned = f"<invalid-json; chars={len(arguments_raw)}>"
        else:
            if not isinstance(decoded, dict):
                cleaned = f"<non-object-json; type={type(decoded).__name__}>"
            else:
                decoded = {
                    key: (
                        f"<omitted; chars={len(value)}>"
                        if key in _FILE_BODY_ARGUMENTS and isinstance(value, str)
                        else "<omitted>"
                        if key in _FILE_BODY_ARGUMENTS
                        else value
                    )
                    for key, value in decoded.items()
                }
                cleaned = json.dumps(
                    self._redactor.redact_object(decoded),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
        if len(cleaned) <= MAX_ARGUMENT_SUMMARY_CHARS:
            return cleaned
        return cleaned[:MAX_ARGUMENT_SUMMARY_CHARS] + "...<truncated>"

    def _write(self, record: dict[str, object]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = self.path.parent.resolve(strict=True)
            resolved_parent.relative_to(self._workspace)
            resolved_path = (resolved_parent / self.path.name).resolve(strict=False)
            resolved_path.relative_to(self._workspace)
            with resolved_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        self._redactor.redact_object(record),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        except (OSError, RuntimeError, TypeError, ValueError):
            self.write_failed = True

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        exit_code = result.details.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
        self._write(
            {
                "timestamp": self._timestamp(),
                "event": "tool",
                "tool": self._redactor.redact(call.name),
                "arguments": self._argument_summary(call.arguments_raw),
                "ok": result.ok,
                "error_code": result.error_code,
                "exit_code": exit_code,
            }
        )

    def record_run(self, result: RunResult) -> None:
        self._write(
            {
                "timestamp": self._timestamp(),
                "event": "run",
                "usage_available": result.provider_total_tokens is not None,
                "stop_reason": result.status,
            }
        )
