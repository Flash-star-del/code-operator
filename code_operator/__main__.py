from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from code_operator.config import ConfigError, ProviderConfig, load_provider_config
from code_operator.journal import UndoResult
from code_operator.loop import ModelLike, TraceLike
from code_operator.models import RunResult
from code_operator.policy import (
    ApprovalCallback,
    PathPolicyError,
)
from code_operator.prompts import INIT_TASK_PROMPT
from code_operator.redaction import Redactor
from code_operator.session import AgentSession, build_tool_registry
from code_operator.tools.registry import ToolRegistry
from code_operator.trace import TerminalTrace, _truncate, terminal_safe_text


MAX_UNDO_DISPLAY_CHARS = 4000


def build_registry(
    config: ProviderConfig,
    workspace: str | os.PathLike[str],
    *,
    approve: ApprovalCallback,
    environment: Mapping[str, str] | None = None,
    ask_all: bool = False,
    auto_approve_tests: bool = False,
) -> ToolRegistry:
    return build_tool_registry(
        config,
        workspace,
        approve=approve,
        environment=environment,
        ask_all=ask_all,
        auto_approve_tests=auto_approve_tests,
    )


def run_task(
    config: ProviderConfig,
    *,
    workspace: str | os.PathLike[str],
    task: str,
    approve: ApprovalCallback,
    client: ModelLike | None = None,
    environment: Mapping[str, str] | None = None,
    ask_all: bool = False,
    auto_approve_tests: bool = False,
    trace: TraceLike | None = None,
) -> RunResult:
    with AgentSession(
        config,
        workspace=workspace,
        approve=approve,
        client=client,
        environment=environment,
        ask_all=ask_all,
        auto_approve_tests=auto_approve_tests,
        trace=trace,
    ) as session:
        return session.run(task)


def _interactive_approval(
    argv: list[str], cwd: Path, *, redactor: Redactor | None = None
) -> bool:
    active_redactor = Redactor([]) if redactor is None else redactor
    redacted_argv = [
        terminal_safe_text(active_redactor.redact(item)) for item in argv
    ]
    redacted_cwd = terminal_safe_text(active_redactor.redact(cwd))
    print("\n命令需要人工批准：")
    print(f"  参数：{json.dumps(redacted_argv, ensure_ascii=False)}")
    print(f"  工作目录：{redacted_cwd}")
    answer = input("仅允许本次执行？[y/N] ").strip().casefold()
    allowed = answer in {"y", "yes", "允许"}
    print("[审批] ALLOW" if allowed else "[审批] DENY")
    return allowed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m code_operator",
        description="在受约束工作区内运行 code-operator。",
    )
    parser.add_argument("task", nargs="*", help="要完成的编码任务；省略时交互输入")
    parser.add_argument("--workspace", default=".", help="允许工具访问的工作区")
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-model-rounds", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    approval = parser.add_mutually_exclusive_group()
    approval.add_argument(
        "--ask-all",
        action="store_true",
        help="所有未被安全策略拒绝的命令都要求人工批准",
    )
    approval.add_argument(
        "--auto-approve-tests",
        action="store_true",
        help="只自动批准 pytest 或 python -m pytest",
    )
    return parser


def _print_run_result(result: RunResult, redactor: Redactor) -> int:
    if result.final_text:
        safe_final_text = terminal_safe_text(
            redactor.redact(result.final_text), multiline=True
        )
        print("[回答]")
        for line in safe_final_text.split(chr(10)):
            print(f"  {line}")
    safe_status = terminal_safe_text(redactor.redact(result.status))
    print(
        f"状态={safe_status} 模型轮次={result.model_rounds} "
        f"工具调用={result.tool_calls}"
    )
    if result.provider_total_tokens is None:
        print("供应商用量不完整")
    else:
        print(f"供应商总 token={result.provider_total_tokens}")
    print(f"本地估算 token={result.estimated_context_tokens}（非供应商真实用量）")
    if result.status == "COMPLETED":
        return 0
    if result.status == "USER_ABORTED":
        return 130
    return 1


def _confirm(message: str) -> bool:
    return input(message).strip().casefold() in {"y", "yes", "允许"}


_LOCAL_COMMANDS = {
    "/undo", "/new", "/help", "/status", "/init", "/compact", "/exit",
}

# 纯 ASCII，避免 Windows 控制台非 UTF-8 代码页下的编码错误。
_BANNER = (
    " [ ,-. ]\n"
    " [ `-> ]  code-operator"
)

_UNKNOWN_COMMAND_HINT = (
    "未知本地命令。支持：/undo、/new、/help、/status、/init、/compact、/exit。"
)


def _local_command(text: str) -> str | None:
    folded = text.strip().casefold()
    return folded if folded in _LOCAL_COMMANDS else None


def _print_help() -> None:
    print("本地命令：")
    print("  /help    显示本帮助")
    print("  /status  显示当前会话状态")
    print("  /init    分析工作区并生成/更新 AGENT.md（会调用模型）")
    print("  /compact 压缩会话历史为摘要（会调用模型）")
    print("  /undo    撤销最近一次直接文件修改")
    print("  /new     清空会话（不恢复文件）")
    print("  /exit    退出")


def _print_status(
    session: AgentSession | None,
    *,
    workspace: str,
    provider_tokens: int,
    usage_incomplete: bool,
) -> None:
    print("[状态]")
    if session is None:
        safe_workspace = _safe_single_line(Path(workspace).resolve(), Redactor([]))
        print(f"  workspace={safe_workspace}")
        print("  会话尚未初始化（首个任务开始时才会加载配置）。")
        return
    redactor = _session_redactor(session)
    print(f"  workspace={_safe_single_line(session.workspace, redactor)}")
    print(f"  model={_safe_single_line(session.model_name, redactor)}")
    print(f"  agent_md={'loaded' if session.agent_md_loaded else 'none'}")
    print(f"  undo_depth={session.undo_depth}")
    print(f"  pending_events={session.pending_event_count}")
    if usage_incomplete:
        print(f"  累计供应商 token={provider_tokens}（部分轮次用量缺失）")
    else:
        print(f"  累计供应商 token={provider_tokens}")


def _safe_single_line(value: object, redactor: Redactor) -> str:
    return _truncate(
        terminal_safe_text(redactor.redact(value)),
        MAX_UNDO_DISPLAY_CHARS,
        multiline=False,
    )


def _print_undo_result(result: UndoResult, redactor: Redactor) -> None:
    print("[撤销] OK" if result.ok else "[撤销] ERROR")
    tool = "-" if result.source_tool is None else _safe_single_line(
        result.source_tool, redactor
    )
    path = "-" if result.path is None else _safe_single_line(result.path, redactor)
    print(f"  tool={tool} path={path} remaining={result.remaining}")
    if result.error_code is not None:
        print(f"  error_code={_safe_single_line(result.error_code, redactor)}")
    print(f"  message={_safe_single_line(result.message, redactor)}")
    if result.ok and result.diff:
        safe_diff = terminal_safe_text(redactor.redact(result.diff), multiline=True)
        print("  reverse_diff:")
        for line in _truncate(safe_diff, MAX_UNDO_DISPLAY_CHARS).split(chr(10)):
            print(f"  {line}")


def _load_config(args: argparse.Namespace) -> ProviderConfig:
    return load_provider_config(
        context_window=args.context_window,
        max_output_tokens=args.max_output_tokens,
        max_model_rounds=args.max_model_rounds,
        max_tool_calls=args.max_tool_calls,
    )


def _create_session(args: argparse.Namespace) -> AgentSession:
    config = _load_config(args)
    redactor = Redactor([config.api_key])
    try:
        return AgentSession(
            config,
            workspace=args.workspace,
            approve=lambda command_argv, command_cwd: _interactive_approval(
                command_argv, command_cwd, redactor=redactor
            ),
            ask_all=args.ask_all,
            auto_approve_tests=args.auto_approve_tests,
            trace=TerminalTrace(redactor),
        )
    except ConfigError as error:
        raise ConfigError(redactor.redact(error)) from error
    except PathPolicyError as error:
        raise PathPolicyError(redactor.redact(error)) from error
    except OSError as error:
        raise ConfigError(redactor.redact(error)) from error
    except Exception as error:
        raise RuntimeError(redactor.redact(error)) from error


def _session_redactor(session: AgentSession) -> Redactor:
    candidate = getattr(session, "_redactor", None)
    return candidate if isinstance(candidate, Redactor) else Redactor([])


def _print_error(error: BaseException, redactor: Redactor | None = None) -> None:
    active_redactor = Redactor([]) if redactor is None else redactor
    print(
        terminal_safe_text(active_redactor.redact(error)),
        file=sys.stderr,
    )


def _run_one_shot(args: argparse.Namespace, task: str) -> int:
    redactor: Redactor | None = None
    try:
        config = _load_config(args)
        redactor = Redactor([config.api_key])
        trace = TerminalTrace(redactor)
        result = run_task(
            config,
            workspace=args.workspace,
            task=task,
            approve=lambda command_argv, command_cwd: _interactive_approval(
                command_argv, command_cwd, redactor=redactor
            ),
            ask_all=args.ask_all,
            auto_approve_tests=args.auto_approve_tests,
            trace=trace,
        )
    except (ConfigError, PathPolicyError) as error:
        _print_error(error, redactor)
        return 2
    except Exception as error:
        _print_error(error, redactor)
        return 1

    return _print_run_result(result, redactor)


def _discard_warning(depth: int) -> str:
    return (
        f"警告：{depth} 条撤销记录将永久消失，文件保持当前状态且不会自动恢复。"
    )


def _run_interactive(args: argparse.Namespace) -> int:
    print(_BANNER)
    session: AgentSession | None = None
    exit_code = 0
    primary_error = False
    provider_tokens_total = 0
    usage_incomplete = False
    try:
        while True:
            try:
                task = input("code-operator> ")
            except KeyboardInterrupt:
                print("\n已取消当前输入，可继续输入任务。")
                continue
            except EOFError:
                if session is not None and session.undo_depth > 0:
                    print(_discard_warning(session.undo_depth))
                break

            stripped = task.strip()
            if not stripped:
                print("任务不能为空，请继续输入。")
                continue

            command = _local_command(task)
            if command is None and stripped.startswith("/"):
                print(_UNKNOWN_COMMAND_HINT)
                continue

            if command == "/help":
                _print_help()
                continue

            if command == "/status":
                _print_status(
                    session,
                    workspace=args.workspace,
                    provider_tokens=provider_tokens_total,
                    usage_incomplete=usage_incomplete,
                )
                continue

            if command == "/exit":
                if session is not None and session.undo_depth > 0:
                    print(_discard_warning(session.undo_depth))
                    try:
                        if not _confirm("确认退出？[y/N] "):
                            print("已取消退出。")
                            continue
                    except KeyboardInterrupt:
                        print("\n已取消退出。")
                        continue
                    except EOFError:
                        print(_discard_warning(session.undo_depth))
                break

            if command == "/undo":
                if session is None:
                    result = UndoResult(
                        False,
                        "UNDO_EMPTY",
                        "当前会话没有可撤销的直接文件修改。",
                    )
                    _print_undo_result(result, Redactor([]))
                else:
                    try:
                        undo_result = session.undo()
                    except KeyboardInterrupt:
                        print("\n撤销操作已取消；未确认恢复完成，请检查文件状态后重试。")
                        continue
                    _print_undo_result(undo_result, _session_redactor(session))
                continue

            if command == "/new":
                if session is None:
                    print("[新会话] 当前会话尚未初始化，无需重置。")
                    continue
                if session.undo_depth > 0:
                    print(
                        f"新建会话将丢失 {session.undo_depth} 条撤销记录，"
                        "但不会恢复文件。"
                    )
                    try:
                        if not _confirm("确认新建会话？[y/N] "):
                            print("已取消新建会话。")
                            continue
                    except (KeyboardInterrupt, EOFError):
                        print("\n已取消新建会话。")
                        continue
                session.reset()
                provider_tokens_total = 0
                usage_incomplete = False
                print("[新会话] 已清空对话、读取状态和撤销记录；文件保持当前状态。")
                continue

            if command == "/compact":
                if session is None:
                    print("[压缩] 会话尚未初始化，无需压缩。")
                    continue
                try:
                    compact_result = session.compact()
                except KeyboardInterrupt:
                    print("\n压缩已取消；历史保持不变。")
                    continue
                redactor = _session_redactor(session)
                print("[压缩] OK" if compact_result.ok else "[压缩] ERROR")
                print(
                    f"  message={_safe_single_line(compact_result.message, redactor)}"
                )
                if compact_result.ok:
                    print(
                        f"  估算 token：{compact_result.before_tokens}"
                        f" -> {compact_result.after_tokens}"
                    )
                    if compact_result.provider_total_tokens is None:
                        usage_incomplete = True
                    else:
                        provider_tokens_total += compact_result.provider_total_tokens
                continue

            if command == "/init":
                task = INIT_TASK_PROMPT
                print("[init] 已提交项目初始化任务（生成/更新 AGENT.md）。")

            if session is None:
                try:
                    session = _create_session(args)
                except (ConfigError, PathPolicyError) as error:
                    _print_error(error)
                    exit_code = 2
                    primary_error = True
                    break

            redactor = _session_redactor(session)
            try:
                result = session.run(task)
            except KeyboardInterrupt:
                result = RunResult(
                    status="USER_ABORTED",
                    final_text="",
                    model_rounds=0,
                    tool_calls=0,
                    provider_total_tokens=None,
                    estimated_context_tokens=0,
                )
                usage_incomplete = True
                _print_run_result(result, redactor)
                continue
            if result.provider_total_tokens is None:
                usage_incomplete = True
            else:
                provider_tokens_total += result.provider_total_tokens
            _print_run_result(result, redactor)
    except Exception as error:
        primary_error = True
        _print_error(error, None if session is None else _session_redactor(session))
        exit_code = 1
    finally:
        if session is not None:
            try:
                session.close()
            except Exception as close_error:
                _print_error(close_error, _session_redactor(session))
                if not primary_error:
                    exit_code = 1
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.task:
        return _run_interactive(args)

    task = " ".join(args.task).strip()
    if not task:
        print("任务不能为空。", file=sys.stderr)
        return 2
    command = _local_command(task)
    if command == "/exit":
        return 0
    if command == "/help":
        _print_help()
        return 0
    if command in {"/undo", "/new", "/status", "/compact"}:
        print(f"{command} 仅交互模式可用。", file=sys.stderr)
        return 2
    if command == "/init":
        task = INIT_TASK_PROMPT
    return _run_one_shot(args, task)


if __name__ == "__main__":
    raise SystemExit(main())
