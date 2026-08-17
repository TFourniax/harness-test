from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from adaptive_harness.contracts import Goal, RiskLevel, ToolSpec
from adaptive_harness.runtime.tool_registry import ToolRegistry

ChildRunner = Callable[[Goal], Awaitable[str]]


def register_parallel_delegation(
    registry: ToolRegistry,
    child_runner: ChildRunner,
    max_parallel: int = 4,
) -> None:
    async def delegate(args: dict[str, Any]) -> str:
        tasks = args["tasks"][:max_parallel]
        goals = [Goal(text=t, max_steps=min(int(args.get("max_steps", 20)), 30)) for t in tasks]
        results = await asyncio.gather(*(child_runner(g) for g in goals), return_exceptions=True)
        return "\n\n".join(
            f"SUBTASK {i+1}: {r if not isinstance(r, Exception) else 'ERROR: ' + str(r)}"
            for i, r in enumerate(results)
        )

    registry.register(
        ToolSpec(
            name="delegate_parallel",
            description=(
                "Delegate independent, breadth-first subtasks to isolated child agents in parallel. "
                "Use only when subtasks do not require tightly shared mutable context."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tasks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "max_steps": {"type": "integer", "default": 20},
                },
                "required": ["tasks"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
            required_scopes=set(),
            source="delegate",
        ),
        delegate,
    )
