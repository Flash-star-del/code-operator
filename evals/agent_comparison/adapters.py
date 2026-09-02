"""Small, non-interactive subprocess adapters for the comparison harness.

The adapter deliberately knows nothing about any particular vendor CLI.  A
reviewed manifest supplies the complete command line; this module only
materializes its two frozen placeholders and records bounded, structured
outcomes.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from code_operator.redaction import sanitized_subprocess_environment
from evals.run_golden import run_process

from .schema import SystemConfig


@dataclass(frozen=True)
class AdapterResult:
    returncode: int | None
    timed_out: bool
    elapsed_seconds: float
    tests_observed: bool
    stop_reason: str
    usage: dict[str, int | float | str | None] | str


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MINIMAL_OS_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
    }
)
_EVENT_KINDS = frozenset({"tool", "command", "run", "result", "usage"})
_USAGE_KEYS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost",
        "cost_usd",
        "latency_seconds",
    }
)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _ordinary_workspace(workspace: Path) -> bool:
    if not workspace.is_absolute() or _is_link_or_reparse(workspace) or not workspace.is_dir():
        return False
    current = Path(workspace.anchor)
    for part in workspace.parts[1:]:
        current /= part
        if os.path.lexists(current) and _is_link_or_reparse(current):
            return False
    return True


def _replace_token(token: str, workspace: Path, task: str) -> str:
    # Scan the template before substitution, so braces introduced by task text
    # remain literal while every template brace is strictly accounted for.
    replacements = {"{workspace}": os.fspath(workspace), "{task}": task}
    pieces: list[str] = []
    index = 0
    while index < len(token):
        if token[index] == "{":
            match = next(
                (name for name in replacements if token.startswith(name, index)),
                None,
            )
            if match is None:
                raise ValueError("argv template contains an unknown or unresolved placeholder")
            pieces.append(replacements[match])
            index += len(match)
        elif token[index] == "}":
            raise ValueError("argv template contains an unknown or unresolved placeholder")
        else:
            pieces.append(token[index])
            index += 1
    value = "".join(pieces)
    if "\x00" in value:
        raise ValueError("argv contains NUL")
    return value


def _executable_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_argv(
    config: SystemConfig, *, workspace: Path, task: str
) -> tuple[str, ...]:
    """Validate and materialize a reviewed literal argv tuple."""
    if not isinstance(config, SystemConfig):
        raise TypeError("config must be SystemConfig")
    if not isinstance(workspace, Path) or not _ordinary_workspace(workspace):
        raise ValueError("workspace must be an absolute ordinary directory")
    if not isinstance(task, str):
        raise TypeError("task must be a string")
    if "\x00" in task:
        raise ValueError("task contains NUL")
    if not isinstance(config.argv_template, tuple) or not config.argv_template:
        raise ValueError("argv template must be a non-empty tuple")
    if any(not isinstance(item, str) for item in config.argv_template):
        raise ValueError("argv template values must be strings")

    argv = tuple(_replace_token(item, workspace, task) for item in config.argv_template)
    executable = Path(argv[0])
    if (
        not executable.is_absolute()
        or _is_link_or_reparse(executable)
        or not executable.is_file()
        or (os.name != "nt" and not os.access(executable, os.X_OK))
    ):
        raise ValueError("executable must be an absolute ordinary file")
    if _executable_sha256(executable) != config.executable_sha256:
        raise ValueError("executable hash mismatch")
    return argv


def _approved_environment(
    config: SystemConfig, source: Mapping[str, str]
) -> dict[str, str]:
    if not isinstance(config.environment_names, tuple):
        raise ValueError("environment names must be a tuple")
    for name in config.environment_names:
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError("invalid environment name")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in source.items()):
        raise ValueError("environment must map strings to strings")
    cleaned = sanitized_subprocess_environment(source)
    explicit = {name.upper() for name in config.environment_names}
    # sanitized_subprocess_environment supplies the intentionally tiny OS
    # allowlist.  The second filter admits only names explicitly reviewed in
    # the manifest in addition to that allowlist.
    result = {
        key: value
        for key, value in cleaned.items()
        if key.upper() in explicit or key.upper() in MINIMAL_OS_ENVIRONMENT_NAMES
    }
    # The shared sanitizer intentionally keeps only OS variables.  Add back
    # every explicitly approved source name, including provider credentials;
    # values are used only for this child process and never returned.
    for key, value in source.items():
        normalized = key.upper()
        if normalized in explicit:
            for existing in tuple(result):
                if existing.upper() == normalized:
                    del result[existing]
            result[key] = value
    return result


def _command_has_pytest(value: object) -> bool:
    if isinstance(value, (list, tuple)):
        tokens = [item for item in value if isinstance(item, str)]
        if any(Path(token).name.lower() in {"pytest", "pytest.exe"} for token in tokens):
            return True
        return any(
            token == "pytest"
            for token in tokens
        ) or any(
            tokens[index:index + 2] == ["-m", "pytest"]
            for index in range(len(tokens) - 1)
        )
    if isinstance(value, str):
        return bool(
            re.search(r"(?:^|[\s\"'])pytest(?:$|[\s\"'])", value, re.IGNORECASE)
            or re.search(r"(?:^|[\s\"'])-m[\s\"']+pytest(?:$|[\s\"'])", value, re.IGNORECASE)
        )
    return False


def _event_kind(event: dict[str, object]) -> str | None:
    values = [event.get(key) for key in ("event", "type") if key in event]
    if not values or any(not isinstance(value, str) for value in values):
        return None
    if len(values) == 2 and values[0] != values[1]:
        return None
    kind = str(values[0]).lower()
    return kind if kind in _EVENT_KINDS else None


def _valid_machine_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    kind = _event_kind(event)
    if kind is None:
        return False
    if kind == "tool":
        if not isinstance(event.get("tool"), str):
            return False
        arguments = event.get("arguments")
        return arguments is None or isinstance(arguments, str)
    if kind == "command":
        return any(
            isinstance(event.get(key), (str, list, tuple))
            for key in ("argv", "command", "cmd")
        )
    if kind == "run":
        return isinstance(event.get("stop_reason"), str) and isinstance(
            event.get("usage_available"), bool
        )
    if kind in {"result", "usage"}:
        return "usage" not in event or isinstance(event.get("usage"), dict)
    return False


def _event_has_pytest(event: dict[str, object]) -> bool:
    kind = _event_kind(event)
    if kind == "tool" and event.get("tool") == "run_command":
        arguments = event.get("arguments")
        if not isinstance(arguments, str):
            return False
        try:
            decoded = json.loads(arguments)
        except (TypeError, ValueError):
            return False
        return isinstance(decoded, dict) and _command_has_pytest(decoded.get("argv"))
    if kind == "command":
        return any(_command_has_pytest(event.get(key)) for key in ("argv", "command", "cmd"))
    return False


def _usage_from_event(event: object) -> dict[str, int | float | str | None] | None:
    if not isinstance(event, dict):
        return None
    if _event_kind(event) not in {"usage", "result"}:
        return None
    candidate: object | None = event.get("usage")
    if candidate is None and str(event.get("event", "")).lower() == "usage":
        candidate = event
    if not isinstance(candidate, dict):
        return None
    result: dict[str, int | float | str | None] = {}
    for key, value in candidate.items():
        if key not in _USAGE_KEYS:
            continue
        if value is None or isinstance(value, (int, float, str)) and not isinstance(value, bool):
            result[key] = value
    return result or None


def _numeric_usage(value: object) -> dict[str, int | float]:
    """Copy only preregistered numeric usage aggregates, never event text."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, int | float] = {}
    for key, item in value.items():
        if (
            key in _USAGE_KEYS
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
        ):
            result[key] = item
    return result


def _merge_usage(
    current: dict[str, int | float], candidate: object
) -> dict[str, int | float]:
    merged = dict(current)
    merged.update(_numeric_usage(candidate))
    return merged


def _decode_arguments(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _parse_kimi_messages(
    events: list[object],
) -> tuple[bool, bool, dict[str, int | float] | str]:
    """Parse the narrow Kimi Code 0.39.1 JSONL Message contract."""
    if not events:
        return False, False, "unavailable"
    tests_observed = False
    usage: dict[str, int | float] = {}
    saw_assistant = False
    last_role: str | None = None
    pending_tool_calls: set[str] = set()
    seen_tool_calls: set[str] = set()
    for event in events:
        if (
            not isinstance(event, dict)
            or "type" in event
            or "event" in event
            or event.get("role") not in {"assistant", "tool"}
        ):
            return False, False, "unavailable"
        role = event["role"]
        if last_role is None and role != "assistant":
            return False, False, "unavailable"
        last_role = role
        if role == "tool":
            tool_call_id = event.get("tool_call_id")
            if (
                not isinstance(tool_call_id, str)
                or tool_call_id not in pending_tool_calls
                or not isinstance(event.get("content"), str)
            ):
                return False, False, "unavailable"
            pending_tool_calls.remove(tool_call_id)
            continue

        if pending_tool_calls:
            return False, False, "unavailable"
        saw_assistant = True
        content = event.get("content")
        if content is not None and not isinstance(content, str):
            return False, False, "unavailable"
        usage = _merge_usage(usage, event.get("usage"))
        tool_calls = event.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            return False, False, "unavailable"
        for tool_call in tool_calls:
            if (
                not isinstance(tool_call, dict)
                or not isinstance(tool_call.get("id"), str)
                or tool_call["id"] in seen_tool_calls
                or tool_call.get("type") != "function"
                or not isinstance(tool_call.get("function"), dict)
            ):
                return False, False, "unavailable"
            function = tool_call["function"]
            tool_call_id = tool_call["id"]
            seen_tool_calls.add(tool_call_id)
            pending_tool_calls.add(tool_call_id)
            if not isinstance(function.get("name"), str):
                return False, False, "unavailable"
            arguments = _decode_arguments(function.get("arguments"))
            if arguments is None:
                return False, False, "unavailable"
            tests_observed = tests_observed or any(
                _command_has_pytest(arguments.get(key))
                for key in ("argv", "command", "cmd")
            )
    valid = saw_assistant and last_role == "assistant" and not pending_tool_calls
    return valid, tests_observed if valid else False, usage or "unavailable"


def _parse_claude_events(
    events: list[object],
) -> tuple[bool, bool, dict[str, int | float] | str]:
    """Parse the narrow Claude Code 2.1.131 stream-json envelope contract."""
    if not events:
        return False, False, "unavailable"
    tests_observed = False
    usage: dict[str, int | float] = {}
    saw_assistant = False
    saw_result = False
    expected = "system"
    for index, event in enumerate(events):
        if not isinstance(event, dict) or "role" in event:
            return False, False, "unavailable"
        kind = event.get("type")
        if kind not in {"system", "assistant", "user", "result"}:
            return False, False, "unavailable"
        if saw_result:
            return False, False, "unavailable"
        if kind == "system":
            if expected != "system" or not isinstance(event.get("subtype"), str):
                return False, False, "unavailable"
            expected = "assistant"
            continue
        if kind == "result":
            if (
                expected != "result"
                or index != len(events) - 1
                or not isinstance(event.get("subtype"), str)
            ):
                return False, False, "unavailable"
            if "is_error" in event and not isinstance(event.get("is_error"), bool):
                return False, False, "unavailable"
            usage = _merge_usage(usage, event.get("usage"))
            cost = event.get("total_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                usage["cost_usd"] = cost
            saw_result = True
            continue

        message = event.get("message")
        expected_role = "assistant" if kind == "assistant" else "user"
        if (
            kind != expected
            or not isinstance(message, dict)
            or message.get("role") != expected_role
        ):
            return False, False, "unavailable"
        content = message.get("content")
        if not isinstance(content, list):
            return False, False, "unavailable"
        if kind == "assistant":
            saw_assistant = True
            usage = _merge_usage(usage, message.get("usage"))
            used_tool = False
            for block in content:
                if not isinstance(block, dict) or block.get("type") not in {
                    "text",
                    "tool_use",
                }:
                    return False, False, "unavailable"
                if block["type"] == "text":
                    if not isinstance(block.get("text"), str):
                        return False, False, "unavailable"
                    continue
                used_tool = True
                if (
                    not isinstance(block.get("name"), str)
                    or not isinstance(block.get("input"), dict)
                ):
                    return False, False, "unavailable"
                if block["name"] == "Bash":
                    tests_observed = tests_observed or _command_has_pytest(
                        block["input"].get("command")
                    )
            expected = "user" if used_tool else "result"
        else:
            for block in content:
                if (
                    not isinstance(block, dict)
                    or block.get("type") != "tool_result"
                    or not isinstance(block.get("tool_use_id"), str)
                    or not isinstance(block.get("content"), (str, list))
                ):
                    return False, False, "unavailable"
            expected = "assistant"
    valid = saw_assistant and saw_result
    return valid, tests_observed if valid else False, usage or "unavailable"


def _parse_machine_output(
    output: str, secrets: tuple[str, ...], system_id: str = "code-operator"
) -> tuple[bool, bool, dict[str, int | float | str | None] | str]:
    events: list[object] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except (TypeError, ValueError):
            return False, False, "unavailable"
    if system_id == "kimi-code":
        return _parse_kimi_messages(events)
    if system_id == "claude-code":
        return _parse_claude_events(events)
    if system_id != "code-operator":
        return False, False, "unavailable"

    parsed = False
    tests_observed = False
    usage: dict[str, int | float | str | None] | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return False, False, "unavailable"
        if not _valid_machine_event(event):
            return False, False, "unavailable"
        parsed = True
        tests_observed = tests_observed or _event_has_pytest(event)  # type: ignore[arg-type]
        candidate = _usage_from_event(event)
        if candidate is not None and usage is None:
            safe: dict[str, int | float | str | None] = {}
            for key, value in candidate.items():
                if isinstance(value, str) and any(secret and secret in value for secret in secrets):
                    continue
                safe[key] = value
            usage = safe or None
    return parsed, tests_observed, usage if usage is not None else "unavailable"


def _parse_audit(
    path: Path,
) -> tuple[bool, bool, str | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False, False, None
    parsed = False
    tests_observed = False
    stop_reason: str | None = None
    allowed_stop_reasons = {
        "COMPLETED", "CONTEXT_LIMIT", "USER_ABORTED",
        "PROVIDER_PROTOCOL_ERROR", "PROVIDER_ERROR", "TOOL_CALL_LIMIT",
        "REPEATED_CALL", "CONSECUTIVE_TOOL_FAILURES", "OUTPUT_TRUNCATED",
        "CONTENT_FILTERED", "EMPTY_RESPONSE", "MODEL_ROUND_LIMIT",
        "NONZERO_EXIT", "TIMEOUT", "INVALID_OUTPUT", "INFRA_ERROR",
    }
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            return False, False, None
        if not isinstance(record, dict) or not isinstance(record.get("event"), str):
            return False, False, None
        kind = record["event"]
        if kind == "tool":
            required = {"timestamp", "event", "tool", "arguments", "ok", "error_code", "exit_code"}
            if set(record) != required or not isinstance(record["timestamp"], str) or not isinstance(record["tool"], str) or not isinstance(record["arguments"], str) or not isinstance(record["ok"], bool):
                return False, False, None
            if record["error_code"] is not None and not isinstance(record["error_code"], str):
                return False, False, None
            if record["exit_code"] is not None and (not isinstance(record["exit_code"], int) or isinstance(record["exit_code"], bool)):
                return False, False, None
            if record["tool"] == "run_command":
                try:
                    arguments = json.loads(record["arguments"])
                except (TypeError, ValueError):
                    return False, False, None
                if not isinstance(arguments, dict) or not isinstance(arguments.get("argv"), list) or not all(isinstance(item, str) for item in arguments["argv"]):
                    return False, False, None
                # An audit entry proves an invocation only when the command
                # reached execution and has a real integer exit status.  A
                # denied command may still carry the requested argv, but it
                # was never a test observation.
                if isinstance(record["exit_code"], int) and not isinstance(record["exit_code"], bool):
                    tests_observed = tests_observed or _command_has_pytest(arguments["argv"])
        elif kind == "run":
            required = {"timestamp", "event", "usage_available", "stop_reason"}
            if set(record) != required or not isinstance(record["timestamp"], str) or not isinstance(record["usage_available"], bool) or not isinstance(record["stop_reason"], str) or record["stop_reason"] not in allowed_stop_reasons:
                return False, False, None
            stop_reason = record["stop_reason"]
        else:
            return False, False, None
        parsed = True
    return parsed and stop_reason is not None, tests_observed, stop_reason


def _safe_runtime_directory(path: Path) -> bool:
    if _is_link_or_reparse(path):
        return False
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            if _is_link_or_reparse(current_path / name):
                return False
    return True


def _cleanup_runtime(workspace: Path) -> None:
    runtime = workspace / ".code-operator"
    if not runtime.exists():
        return
    if not _safe_runtime_directory(runtime):
        raise OSError("unsafe runtime directory")
    shutil.rmtree(runtime)


def run_adapter(
    config: SystemConfig,
    *,
    workspace: Path,
    task: str,
    timeout_seconds: int,
    source_environment: Mapping[str, str],
) -> AdapterResult:
    """Run one reviewed command and return only stable aggregate metadata."""
    started = time.monotonic()
    audit_mode = (
        isinstance(config, SystemConfig)
        and config.system_id == "code-operator"
        and config.output_mode == "code-operator-audit-v1"
    )
    result: AdapterResult | None = None
    try:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        argv = materialize_argv(config, workspace=workspace, task=task)
        environment = _approved_environment(config, source_environment)
        secrets = tuple(value for value in source_environment.values() if isinstance(value, str) and value)
        # run_process owns process-group/Job Object termination.  Pass the
        # already-sanitized mapping directly; it must not consult os.environ.
        completed = run_process(
            argv,
            cwd=workspace,
            timeout=float(timeout_seconds),
            environment=environment,
        )
        elapsed = time.monotonic() - started
        if completed.timed_out:
            result = AdapterResult(None, True, elapsed, False, "TIMEOUT", "unavailable")
        elif completed.returncode is None:
            result = AdapterResult(None, False, elapsed, False, "INFRA_ERROR", "unavailable")
        else:
            if audit_mode:
                audit_path = workspace / ".code-operator" / "audit.jsonl"
                parsed, tests_observed, audit_reason = (
                    _parse_audit(audit_path)
                    if audit_path.is_file() and not _is_link_or_reparse(audit_path)
                    else (False, False, None)
                )
                usage: dict[str, int | float | str | None] | str = "unavailable"
                reason = (
                    "NONZERO_EXIT"
                    if completed.returncode != 0
                    else audit_reason
                    if parsed and audit_reason is not None
                    else "INVALID_OUTPUT"
                )
            else:
                parsed, tests_observed, usage = _parse_machine_output(
                    completed.stdout, secrets, config.system_id
                )
                reason = (
                    "NONZERO_EXIT"
                    if completed.returncode != 0
                    else "COMPLETED"
                    if parsed
                    else "INVALID_OUTPUT"
                )
            result = AdapterResult(completed.returncode, False, elapsed, tests_observed, reason, usage)
    except Exception:
        result = AdapterResult(
            None,
            False,
            time.monotonic() - started,
            False,
            "INFRA_ERROR",
            "unavailable",
        )
    finally:
        if audit_mode:
            try:
                _cleanup_runtime(workspace)
            except Exception:
                result = AdapterResult(
                    None,
                    False,
                    time.monotonic() - started,
                    False,
                    "INFRA_ERROR",
                    "unavailable",
                )
    assert result is not None
    return result


__all__ = [
    "AdapterResult",
    "MINIMAL_OS_ENVIRONMENT_NAMES",
    "materialize_argv",
    "run_adapter",
]
