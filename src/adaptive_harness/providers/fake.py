from __future__ import annotations

from collections import deque
from typing import Any, Literal

from adaptive_harness.contracts import ModelTurn, ToolSpec
from adaptive_harness.providers.base import ModelProvider


class FakeProvider(ModelProvider):
    def __init__(self, turns: list[ModelTurn]):
        self.turns = deque(turns)

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_mode: Literal["auto", "native", "text"] = "auto",
        fallbacks: list[str] | None = None,
        timeout: float = 90.0,
        num_retries: int = 2,
    ) -> ModelTurn:
        if not self.turns:
            return ModelTurn(content="done")
        return self.turns.popleft()
