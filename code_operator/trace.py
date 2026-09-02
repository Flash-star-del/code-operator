from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping

from code_operator.colors import colorize
from code_operator.models import RunResult, ToolCall, ToolResult
from code_operator.redaction import Redactor

MAX_ARGUMENT_SUMMARY_CHARS = 500
MAX_TRACE_DETAIL_CHARS = 4000


def terminal_safe_text(value: object, *, multiline: bool = False) -> str:
    """Render untrusted text without executable terminal control characters."""
    escaped: list[str] = []
    named_controls = {
        0x08: r"\b",
        0x09: r"\t",
        0x0A: r"\n",
        0x0D: r"\r",
    }
    for character in str(value):
        codepoint = ord(character)
        if multiline and codepoint == 0x0A:
            escaped.append(character)
            continue
        if unicodedata.category(character) not in {"Cc", "Cf", "Zl", "Zp"}:
            escaped.append(character)
            continue
        if codepoint in named_controls:
            escaped.append(named_controls[codepoint])
        elif codepoint <= 0xFF:
            escaped.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(f"\\U{codepoint:08x}")
    return "".join(escaped)


def _truncate(text: str, limit: int, *, multiline: bool = True) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = f"... <truncated; original_chars={len(text)}> ..."
    if multiline:
        marker = f"\n{marker}\n"
    if len(marker) >= limit:
        return marker[:limit]
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + (text[-tail:] if tail else "")


class TerminalTrace:
    def __init__(self, redactor: Redactor, *, write: Callable[[str], None] = print) -> None:
        self.redactor = redactor
        self.write = write
        self.output_failed = False

    def _emit(self, text: str) -> None:
        if self.output_failed:
            return
        try:
            self.write(text)
        except Exception:
            self.output_failed = True

    def record_model_round(self, round_number: int, tool_call_count: int, usage_available: bool) -> None:
        usage = "available" if usage_available else "unavailable"
        self._emit(
            colorize(
                f"[模型 {round_number}] tool_calls={tool_call_count} usage={usage}",
                "cyan",
            )
        )

    def record_run(self, result: RunResult) -> None:
        status = terminal_safe_text(self.redactor.redact(result.status))
        usage = "available" if result.provider_total_tokens is not None else "unavailable"
        style = "green" if result.status == "COMPLETED" else "yellow"
        self._emit(colorize(f"[结束] stop_reason={status} usage={usage}", style))

    def _summary_value(self, value: object, key: str | None = None) -> object:
        if key in {"content", "old_text", "new_text"}:
            if isinstance(value, str):
                return f"<omitted; chars={len(value)}>"
            return "<omitted>"
        if isinstance(value, Mapping):
            return {
                self.redactor.redact(str(k)): (
                    "<REDACTED>"
                    if re.search(r"(?i)(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD)$", str(k))
                    else self._summary_value(v, str(k))
                )
                for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, list):
            return [self._summary_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._summary_value(item) for item in value]
        if isinstance(value, str):
            return self.redactor.redact(value)
        return value

    def _argument_summary(self, raw: object) -> str:
        if not isinstance(raw, str):
            return f"<unavailable; type={type(raw).__name__}>"
        try:
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                type_name = "array" if isinstance(decoded, list) else type(decoded).__name__
                return _truncate(
                    f"<json_type={type_name}; chars={len(raw)}>",
                    MAX_ARGUMENT_SUMMARY_CHARS,
                    multiline=False,
                )
            summary = json.dumps(self.redactor.redact_object(self._summary_value(decoded)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, ValueError, RecursionError, TypeError):
            return _truncate(
                f"<invalid_json; chars={len(raw)}>",
                MAX_ARGUMENT_SUMMARY_CHARS,
                multiline=False,
            )
        return _truncate(
            terminal_safe_text(summary),
            MAX_ARGUMENT_SUMMARY_CHARS,
            multiline=False,
        )

    def record_tool(self, call: ToolCall, result: ToolResult) -> None:
        name = call.name
        raw = call.arguments_raw
        tool_result = result
        clean_name = terminal_safe_text(self.redactor.redact(name))
        argument_summary = terminal_safe_text(self._argument_summary(raw))
        self._emit(colorize(f"[工具] {clean_name} 参数={argument_summary}", "cyan"))
        code = (
            "-"
            if tool_result.error_code is None
            else terminal_safe_text(self.redactor.redact(tool_result.error_code))
        )
        self._emit(
            colorize(
                f"[结果] {clean_name} ok={'true' if tool_result.ok else 'false'} error_code={code}",
                "green" if tool_result.ok else "red",
            )
        )
        if name in {"write_file", "edit_file"}:
            if not tool_result.ok:
                return
            diff = tool_result.details.get("diff") if isinstance(tool_result.details, Mapping) else None
            if isinstance(diff, str) and diff:
                safe_diff = terminal_safe_text(
                    self.redactor.redact(diff), multiline=True
                )
                truncated = _truncate(safe_diff, MAX_TRACE_DETAIL_CHARS)
                colored_lines = []
                for line in truncated.split(chr(10)):
                    if line.startswith("+"):
                        colored_lines.append(colorize(line, "green"))
                    elif line.startswith("-"):
                        colored_lines.append(colorize(line, "red"))
                    else:
                        colored_lines.append(line)
                self._emit(chr(10).join(colored_lines))
        elif name == "run_command":
            details = tool_result.details if isinstance(tool_result.details, Mapping) else {}
            exit_code = details.get("exit_code")
            exit_text = str(exit_code) if isinstance(exit_code, int) and not isinstance(exit_code, bool) else "-"
            timeout = details.get("timed_out")
            timeout_text = "true" if timeout is True else "false" if timeout is False else "-"
            self._emit(
                colorize(
                    f"[命令] exit_code={exit_text} timed_out={timeout_text}",
                    "green" if exit_code == 0 else "red",
                )
            )
            for field in ("stdout", "stderr"):
                value = details.get(field)
                self._emit(f"  {field}:")
                if isinstance(value, str):
                    shown = terminal_safe_text(
                        self.redactor.redact(value), multiline=True
                    ) or "<empty>"
                    for line in _truncate(shown, MAX_TRACE_DETAIL_CHARS).split(chr(10)):
                        self._emit(f"  {line}")
                else:
                    self._emit("  <unavailable>")
