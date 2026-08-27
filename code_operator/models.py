from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_raw: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall]
    replay_fields: dict[str, object] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: Usage | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        reserved = {"role", "content", "tool_calls"}
        conflicts = reserved.intersection(self.replay_fields)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"replay_fields 与标准消息字段冲突：{names}")

    def to_replay_message(self) -> dict[str, object]:
        message: dict[str, object] = {
            "role": "assistant",
            "content": self.content,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_raw,
                    },
                }
                for call in self.tool_calls
            ]
        message.update(self.replay_fields)
        return message


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    ok: bool
    error_code: str | None
    message: str
    details: dict[str, object]

    def to_message_content(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "error_code": self.error_code,
                "message": self.message,
                "details": self.details,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class RunResult:
    status: str
    final_text: str
    model_rounds: int
    tool_calls: int
    provider_total_tokens: int | None
    estimated_context_tokens: int
