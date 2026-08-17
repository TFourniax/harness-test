import pytest

from adaptive_harness.contracts import RiskLevel, ToolCall, ToolSpec
from adaptive_harness.runtime.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_tool_arguments_are_schema_validated():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="one_arg",
            description="x",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
        ),
        lambda a: str(a["value"]),
    )
    obs = await registry.execute(ToolCall(name="one_arg", arguments={"value": "not-int"}))
    assert not obs.ok
    assert "ValidationError" in obs.content
