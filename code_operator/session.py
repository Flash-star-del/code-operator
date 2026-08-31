from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from code_operator.audit import JsonlAudit
from code_operator.client import ModelClient
from code_operator.config import ProviderConfig
from code_operator.journal import ChangeJournal, UndoResult
from code_operator.loop import AgentLoop, ModelLike, TraceLike
from code_operator.models import RunResult, ToolResult
from code_operator.policy import (
    ApprovalCallback,
    CommandPolicy,
    WorkspacePolicy,
)
from code_operator.redaction import Redactor
from code_operator.tools.command import run_command
from code_operator.tools.filesystem import FileTools
from code_operator.tools.registry import ToolHandler, ToolRegistry
from code_operator.tools.search import SearchTools
from code_operator.trace import terminal_safe_text


MAX_UNDO_NOTICE_PATH_CHARS = 500


@dataclass(frozen=True)
class _Tooling:
    workspace_policy: WorkspacePolicy
    redactor: Redactor
    journal: ChangeJournal | None
    file_tools: FileTools
    search_tools: SearchTools
    command_policy: CommandPolicy
    command_handler: ToolHandler
    registry: ToolRegistry


def _bounded_text(text: str, limit: int = MAX_UNDO_NOTICE_PATH_CHARS) -> str:
    if len(text) <= limit:
        return text
    marker = f"... <truncated; original_chars={len(text)}> ..."
    if len(marker) >= limit:
        return marker[:limit]
    remaining = limit - len(marker)
    head = remaining // 2
    tail = remaining - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _build_tooling(
    config: ProviderConfig,
    workspace: str | os.PathLike[str],
    *,
    approve: ApprovalCallback,
    environment: Mapping[str, str] | None = None,
    ask_all: bool = False,
    auto_approve_tests: bool = False,
    journal: ChangeJournal | None = None,
) -> _Tooling:
    workspace_policy = WorkspacePolicy(workspace)
    redactor = Redactor([config.api_key])
    file_tools = FileTools(
        workspace_policy,
        redactor=redactor,
        journal=journal,
    )
    search_tools = SearchTools(workspace_policy, redactor=redactor)
    command_policy = CommandPolicy(
        workspace_policy.workspace,
        approve=approve,
        ask_all=ask_all,
        auto_approve_tests=auto_approve_tests,
    )
    source_environment = os.environ if environment is None else environment

    def command_handler(
        *,
        tool_call_id: str,
        argv: list[str],
        timeout_seconds: int = 30,
    ) -> ToolResult:
        return run_command(
            tool_call_id=tool_call_id,
            argv=argv,
            timeout_seconds=timeout_seconds,
            policy=command_policy,
            redactor=redactor,
            environment=source_environment,
        )

    registry = ToolRegistry(
        {
            "list_dir": file_tools.list_dir,
            "read_file": file_tools.read_file,
            "grep": search_tools.grep,
            "write_file": file_tools.write_file,
            "edit_file": file_tools.edit_file,
            "run_command": command_handler,
        }
    )
    return _Tooling(
        workspace_policy=workspace_policy,
        redactor=redactor,
        journal=journal,
        file_tools=file_tools,
        search_tools=search_tools,
        command_policy=command_policy,
        command_handler=command_handler,
        registry=registry,
    )


def build_tool_registry(
    config: ProviderConfig,
    workspace: str | os.PathLike[str],
    *,
    approve: ApprovalCallback,
    environment: Mapping[str, str] | None = None,
    ask_all: bool = False,
    auto_approve_tests: bool = False,
) -> ToolRegistry:
    """Build the historical one-shot registry through the shared assembly path."""
    return _build_tooling(
        config,
        workspace,
        approve=approve,
        environment=environment,
        ask_all=ask_all,
        auto_approve_tests=auto_approve_tests,
    ).registry


class AgentSession:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        workspace: str | os.PathLike[str],
        approve: ApprovalCallback,
        client: ModelLike | None = None,
        environment: Mapping[str, str] | None = None,
        ask_all: bool = False,
        auto_approve_tests: bool = False,
        trace: TraceLike | None = None,
    ) -> None:
        self._config = config
        self._journal = ChangeJournal()
        tooling = _build_tooling(
            config,
            workspace,
            approve=approve,
            environment=environment,
            ask_all=ask_all,
            auto_approve_tests=auto_approve_tests,
            journal=self._journal,
        )
        self._workspace_policy = tooling.workspace_policy
        self._redactor = tooling.redactor
        self._file_tools = tooling.file_tools
        self._search_tools = tooling.search_tools
        self._command_policy = tooling.command_policy
        self._command_handler = tooling.command_handler
        self._registry = tooling.registry
        self._owned_client = ModelClient(config) if client is None else None
        self._client = self._owned_client if self._owned_client is not None else client
        if self._client is None:
            raise RuntimeError("ModelClient 初始化失败")
        self._pending_events: list[str] = []
        self._closed = False
        try:
            self._audit = JsonlAudit(
                self._workspace_policy.workspace,
                redactor=self._redactor,
            )
            self._trace = trace
            self._loop = AgentLoop(
                self._client,
                self._registry,
                max_model_rounds=config.max_model_rounds,
                max_tool_calls=config.max_tool_calls,
                context_window=config.context_window,
                max_output_tokens=config.max_output_tokens,
                audit=self._audit,
                trace=trace,
            )
        except BaseException as error:
            try:
                self.close()
            except BaseException as close_error:
                self._add_close_failure_note(error, close_error)
            raise

    @property
    def file_tools(self) -> FileTools:
        return self._file_tools

    @property
    def undo_depth(self) -> int:
        return self._journal.depth

    @property
    def pending_event_count(self) -> int:
        return len(self._pending_events)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AgentSession 已关闭")

    def _add_close_failure_note(
        self, error: BaseException, close_error: BaseException
    ) -> None:
        try:
            detail = terminal_safe_text(self._redactor.redact(close_error))
            error.add_note(
                "关闭自有 ModelClient 时发生附加错误："
                f"{type(close_error).__name__}: {detail}"
            )
        except Exception:
            pass

    def run(self, task: str) -> RunResult:
        self._ensure_open()
        pending_path = self._pending_events.pop(0) if self._pending_events else None
        model_task = task
        if pending_path is not None:
            model_task = (
                "[本地会话事件]\n"
                f"用户已撤销 {pending_path} 的最近一次直接文件修改。"
                "该文件状态可能与此前对话不同，编辑前必须重新读取。\n\n"
                "[当前用户请求]\n"
                f"{task}"
            )
        result = self._loop.run(model_task)
        if (
            pending_path is not None
            and result.status == "CONTEXT_LIMIT"
            and result.model_rounds == 0
        ):
            self._pending_events.insert(0, pending_path)
        return result

    def undo(self) -> UndoResult:
        self._ensure_open()
        result = self._file_tools.undo_last_change()
        if result.ok:
            safe_path = terminal_safe_text(
                self._redactor.redact(result.path or "<unknown>")
            )
            self._pending_events.append(_bounded_text(safe_path))
        return result

    def reset(self) -> None:
        self._ensure_open()
        self._loop.reset()
        self._file_tools.reset_read_state()
        self._journal.clear()
        self._pending_events.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned_client is not None:
            self._owned_client.close()

    def __enter__(self) -> AgentSession:
        self._ensure_open()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        error: BaseException | None,
        _traceback: object,
    ) -> bool:
        if error is None:
            self.close()
        else:
            try:
                self.close()
            except BaseException as close_error:
                self._add_close_failure_note(error, close_error)
        return False
