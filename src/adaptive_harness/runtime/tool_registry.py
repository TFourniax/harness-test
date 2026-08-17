from __future__ import annotations

import asyncio

import jsonschema
from collections.abc import Awaitable, Callable
from typing import Any

from adaptive_harness.contracts import Observation, ToolCall, ToolExecutionResult, ToolSpec, TrustLevel

ToolFn = Callable[[dict[str, Any]], Awaitable[str | ToolExecutionResult] | str | ToolExecutionResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._fns: dict[str, ToolFn] = {}

    def register(self, spec: ToolSpec, fn: ToolFn) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec
        self._fns[spec.name] = fn

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)
        self._fns.pop(name, None)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    async def execute(self, call: ToolCall) -> Observation:
        spec = self.get(call.name)
        fn = self._fns[call.name]
        try:
            jsonschema.validate(instance=call.arguments, schema=spec.input_schema)
            value = fn(call.arguments)
            if asyncio.iscoroutine(value):
                value = await value
            default_trust = (
                TrustLevel.UNTRUSTED_EXTERNAL
                if spec.source in {"web", "mcp", "remote", "delegate"} or spec.source.startswith("mcp:")
                else TrustLevel.TOOL
            )
            if isinstance(value, ToolExecutionResult):
                return Observation(
                    call_id=call.id,
                    tool_name=call.name,
                    ok=True,
                    content=value.content,
                    metadata={"risk": spec.risk.value, "source": spec.source, **value.metadata},
                    trust=value.trust or default_trust,
                )
            return Observation(
                call_id=call.id,
                tool_name=call.name,
                ok=True,
                content=str(value),
                metadata={"risk": spec.risk.value, "source": spec.source},
                trust=default_trust,
            )
        except Exception as exc:  # tool failures are observations, not runtime crashes
            return Observation(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                content=f"{type(exc).__name__}: {exc}",
                metadata={"risk": spec.risk.value, "source": spec.source},
                trust=TrustLevel.TOOL,
            )
