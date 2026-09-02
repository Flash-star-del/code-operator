from __future__ import annotations

from pathlib import Path

from code_operator.client import ProviderError
from code_operator.config import ProviderConfig
from code_operator.loop import AgentLoop
from code_operator.models import AssistantTurn, Usage
from code_operator.prompts import SYSTEM_PROMPT
from code_operator.session import AgentSession
from code_operator.tools.registry import ToolRegistry

from tests.fakes import FakeModelClient


def config() -> ProviderConfig:
    return ProviderConfig(
        api_key="private-key",
        base_url="https://provider.example/v1",
        model="test-model",
    )


def summary_turn(content: str | None, total_tokens: int | None = 30) -> AssistantTurn:
    usage = (
        None
        if total_tokens is None
        else Usage(
            prompt_tokens=None, completion_tokens=None, total_tokens=total_tokens
        )
    )
    return AssistantTurn(
        content=content, tool_calls=[], finish_reason="stop", usage=usage
    )


def loop_with_history(client: FakeModelClient) -> AgentLoop:
    loop = AgentLoop(client, ToolRegistry({}))
    loop._messages.extend(
        [
            {"role": "user", "content": "修复 bug"},
            {"role": "assistant", "content": "已修复 main.py 并通过测试。"},
        ]
    )
    return loop


def test_compact_with_empty_history_is_local_no_model_call() -> None:
    client = FakeModelClient([])
    loop = AgentLoop(client, ToolRegistry({}))

    result = loop.compact()

    assert not result.ok
    assert "没有可压缩" in result.message
    assert client.calls == []


def test_compact_success_replaces_history_with_summary() -> None:
    client = FakeModelClient([summary_turn("目标：修复 bug；已完成。")])
    loop = loop_with_history(client)

    result = loop.compact()

    assert result.ok
    assert result.provider_total_tokens == 30
    assert len(loop._messages) == 2
    assert loop._messages[0]["role"] == "system"
    summary_message = loop._messages[1]
    assert summary_message["role"] == "user"
    assert "[前情摘要" in str(summary_message["content"])
    assert "目标：修复 bug" in str(summary_message["content"])
    request_messages, request_tools = client.calls[0]
    assert request_tools == []
    assert "修复 bug" in str(request_messages[1]["content"])


def test_compact_provider_error_keeps_history_unchanged() -> None:
    client = FakeModelClient([ProviderError("网络错误")])
    loop = loop_with_history(client)
    before = [dict(message) for message in loop._messages]

    result = loop.compact()

    assert not result.ok
    assert "历史保持不变" in result.message
    assert loop._messages == before


def test_compact_empty_summary_keeps_history_unchanged() -> None:
    client = FakeModelClient([summary_turn("   ")])
    loop = loop_with_history(client)
    before = [dict(message) for message in loop._messages]

    result = loop.compact()

    assert not result.ok
    assert loop._messages == before


def _session(workspace: Path) -> AgentSession:
    return AgentSession(
        config(),
        workspace=workspace,
        approve=lambda _argv, _cwd: False,
        client=FakeModelClient([]),
    )


def test_agent_md_is_appended_to_system_prompt(tmp_path: Path) -> None:
    (tmp_path / "AGENT.md").write_text(
        "# 项目说明\n用 pytest 跑测试。", encoding="utf-8"
    )

    with _session(tmp_path) as session:
        assert session.agent_md_loaded
        system_content = str(session._loop._messages[0]["content"])
        assert system_content.startswith(SYSTEM_PROMPT)
        assert "AGENT.md" in system_content
        assert "用 pytest 跑测试。" in system_content
        session.reset()
        assert "用 pytest 跑测试。" in str(session._loop._messages[0]["content"])


def test_missing_agent_md_leaves_system_prompt_unchanged(tmp_path: Path) -> None:
    with _session(tmp_path) as session:
        assert not session.agent_md_loaded
        assert session._loop._messages[0]["content"] == SYSTEM_PROMPT
