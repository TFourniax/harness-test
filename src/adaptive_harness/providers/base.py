from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from adaptive_harness.contracts import ModelTurn, ToolSpec


class ModelProvider(ABC):
    @abstractmethod
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
        """Return one normalized model turn.

        tool_mode="text" provides a compatibility path for text-only models;
        "native" requires provider function calling; "auto" selects when known.
        """
        raise NotImplementedError
