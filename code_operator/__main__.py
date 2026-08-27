from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from code_operator.client import ModelClient
from code_operator.config import ConfigError, ProviderConfig, load_provider_config
from code_operator.loop import AgentLoop, ModelLike
from code_operator.models import RunResult, ToolResult
from code_operator.policy import (
    ApprovalCallback,
    CommandPolicy,
    PathPolicyError,
    WorkspacePolicy,
)
from code_operator.redaction import Redactor
from code_operator.tools.command import run_command
from code_operator.tools.filesystem import FileTools
from code_operator.tools.registry import ToolRegistry


def build_registry(
    config: ProviderConfig,
    workspace: str | os.PathLike[str],
    *,
    approve: ApprovalCallback,
    environment: Mapping[str, str] | None = None,
) -> ToolRegistry:
    workspace_policy = WorkspacePolicy(workspace)
    redactor = Redactor([config.api_key])
    file_tools = FileTools(workspace_policy, redactor=redactor)
    command_policy = CommandPolicy(workspace_policy.workspace, approve=approve)
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

    return ToolRegistry(
        {
            "read_file": file_tools.read_file,
            "write_file": file_tools.write_file,
            "run_command": command_handler,
        }
    )


def run_task(
    config: ProviderConfig,
    *,
    workspace: str | os.PathLike[str],
    task: str,
    approve: ApprovalCallback,
    client: ModelLike | None = None,
    environment: Mapping[str, str] | None = None,
) -> RunResult:
    registry = build_registry(
        config,
        workspace,
        approve=approve,
        environment=environment,
    )
    owned_client = ModelClient(config) if client is None else None
    selected_client = owned_client if owned_client is not None else client
    if selected_client is None:
        raise RuntimeError("ModelClient 初始化失败")
    try:
        return AgentLoop(
            selected_client,
            registry,
            max_model_rounds=config.max_model_rounds,
            max_tool_calls=config.max_tool_calls,
        ).run(task)
    finally:
        if owned_client is not None:
            owned_client.close()


def _interactive_approval(argv: list[str], cwd: Path) -> bool:
    print("\n命令需要人工批准：")
    print(f"  参数：{json.dumps(argv, ensure_ascii=False)}")
    print(f"  工作目录：{cwd}")
    answer = input("仅允许本次执行？[y/N] ").strip().casefold()
    return answer in {"y", "yes", "允许"}


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = " ".join(args.task).strip()
    if not task:
        task = input("请输入编码任务：").strip()
    if not task:
        print("任务不能为空。", file=sys.stderr)
        return 2
    try:
        config = load_provider_config(
            context_window=args.context_window,
            max_output_tokens=args.max_output_tokens,
            max_model_rounds=args.max_model_rounds,
            max_tool_calls=args.max_tool_calls,
        )
        result = run_task(
            config,
            workspace=args.workspace,
            task=task,
            approve=_interactive_approval,
        )
    except (ConfigError, PathPolicyError) as error:
        print(str(error), file=sys.stderr)
        return 2

    if result.final_text:
        print(result.final_text)
    print(
        f"状态={result.status} 模型轮次={result.model_rounds} "
        f"工具调用={result.tool_calls}"
    )
    if result.provider_total_tokens is None:
        print("供应商用量不完整")
    else:
        print(f"供应商总 token={result.provider_total_tokens}")
    if result.status == "COMPLETED":
        return 0
    if result.status == "USER_ABORTED":
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
