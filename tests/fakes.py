from __future__ import annotations

from collections.abc import Mapping, Sequence

from code_operator.models import AssistantTurn


class FakeModelClient:
    def __init__(self, turns: Sequence[AssistantTurn | Exception]) -> None:
        self._turns = list(turns)
        self.calls: list[
            tuple[list[dict[str, object]], list[dict[str, object]]]
        ] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
    ) -> AssistantTurn:
        self.calls.append(
            ([dict(message) for message in messages], [dict(tool) for tool in tools])
        )
        if not self._turns:
            raise AssertionError("FakeModelClient 没有剩余响应")
        next_item = self._turns.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item
