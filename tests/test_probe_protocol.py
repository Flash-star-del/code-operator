from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from code_operator.config import ProviderConfig
from scripts.probe_protocol import (
    ProbeError,
    completion_limit_evidence,
    run_protocol_probe,
)


def test_completion_limit_evidence_requires_usage_not_to_exceed_limit() -> None:
    assert completion_limit_evidence(
        {"usage": {"completion_tokens": 8}}, maximum=8
    ) == {
        "usage_available": True,
        "completion_tokens": 8,
        "maximum": 8,
        "verified": True,
    }

    with pytest.raises(ProbeError, match="超过请求上限"):
        completion_limit_evidence(
            {"usage": {"completion_tokens": 9}}, maximum=8
        )


def test_probe_runs_text_tool_and_replay_without_leaking_sensitive_content(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []
    responses = [
        {
            "id": "provider-text-id",
            "model": "provider-model-name",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "sensitive-text-answer",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
            },
        },
        {
            "id": "provider-tool-id",
            "model": "provider-model-name",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "sensitive-reasoning",
                        "tool_calls": [
                            {
                                "id": "provider-call-id",
                                "type": "function",
                                "function": {
                                    "name": "return_probe_token",
                                    "arguments": '{"probe_token":"PROBE_OK"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 5,
                "total_tokens": 9,
                "prompt_tokens_details": {"cached_tokens": 1},
            },
        },
        {
            "id": "provider-final-id",
            "model": "provider-model-name",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "sensitive-final-answer",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 6,
                "completion_tokens": 7,
                "total_tokens": 13,
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key-value"
        return httpx.Response(
            200,
            json=responses[len(requests) - 1],
            headers={"x-request-id": f"request-{len(requests)}"},
        )

    config = ProviderConfig(
        api_key="test-key-value",
        base_url="https://api.moonshot.cn/v1",
        model="test-model",
    )
    fixture_path = tmp_path / "provider_tool_call.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        evidence = run_protocol_probe(config, client, fixture_path)

    assert len(requests) == 3
    assert requests[0]["n"] == 1
    assert requests[0]["max_tokens"] == 8
    assert "tools" not in requests[0]

    assert requests[1]["n"] == 1
    assert requests[1]["max_tokens"] == 256
    assert requests[1]["thinking"] == {"type": "disabled"}
    assert requests[1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "return_probe_token"},
    }

    replay_messages = requests[2]["messages"]
    assert requests[2]["thinking"] == {"type": "disabled"}
    assistant_message = replay_messages[-2]
    tool_message = replay_messages[-1]
    assert "reasoning_content" not in assistant_message
    assert set(assistant_message) == {"role", "content", "tool_calls"}
    assert tool_message["tool_call_id"] == "provider-call-id"
    assert tool_message["name"] == "return_probe_token"

    serialized_evidence = json.dumps(evidence, ensure_ascii=False)
    assert evidence["output_limit"]["verified"] is True
    assert "test-key-value" not in serialized_evidence
    assert "sensitive-reasoning" not in serialized_evidence
    assert "sensitive-text-answer" not in serialized_evidence
    assert "sensitive-final-answer" not in serialized_evidence

    fixture_text = fixture_path.read_text(encoding="utf-8")
    assert "test-key-value" not in fixture_text
    assert "sensitive-reasoning" not in fixture_text
    assert "provider-call-id" not in fixture_text
    fixture = json.loads(fixture_text)
    assert fixture["choices"][0]["message"]["tool_calls"][0]["id"] == (
        "probe_call_1"
    )
    assert fixture["usage"]["prompt_tokens_details"] == {"cached_tokens": 0}
