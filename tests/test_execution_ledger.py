import pytest

from adaptive_harness.config import HarnessConfig, ModelRole
from adaptive_harness.contracts import Goal, ModelTurn, RiskLevel, ToolCall, ToolSpec
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.providers.fake import FakeProvider
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore


@pytest.mark.asyncio
async def test_non_idempotent_tool_is_not_executed_twice(tmp_path):
    calls = {"n": 0}

    def effect(_):
        calls["n"] += 1
        return "done"

    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="effect",
            description="effect",
            input_schema={"type": "object", "additionalProperties": False},
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"effect"},
            idempotent=False,
        ),
        effect,
    )
    traces = TraceStore(str(tmp_path / "t.db"))
    runtime = AgentRuntime(
        config=HarnessConfig(primary=ModelRole(model="fake"), allowed_scopes={"effect"}),
        provider=FakeProvider([]),
        tools=tools,
        traces=traces,
        memory=MemoryStore(str(tmp_path / "m.db")),
    )
    call = ToolCall(name="effect", arguments={})
    spec = tools.get("effect")
    first = await runtime._execute_guarded("r", spec, call)
    second = await runtime._execute_guarded("r", spec, call)
    assert first.ok and second.ok
    assert calls["n"] == 1
    assert second.metadata["deduplicated"] is True


@pytest.mark.asyncio
async def test_ambiguous_started_effect_is_blocked(tmp_path):
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="effect",
            description="effect",
            input_schema={"type": "object"},
            risk=RiskLevel.EXTERNAL_SIDE_EFFECT,
            required_scopes={"effect"},
            idempotent=False,
        ),
        lambda _: "should not run",
    )
    traces = TraceStore(str(tmp_path / "t.db"))
    runtime = AgentRuntime(
        config=HarnessConfig(primary=ModelRole(model="fake"), allowed_scopes={"effect"}),
        provider=FakeProvider([]),
        tools=tools,
        traces=traces,
        memory=MemoryStore(str(tmp_path / "m.db")),
    )
    call = ToolCall(name="effect", arguments={})
    from adaptive_harness.runtime.policy import action_fingerprint

    fp = action_fingerprint(call)
    traces.begin_execution(fp, "r", "effect", call.model_dump(mode="json"))
    obs = await runtime._execute_guarded("r", tools.get("effect"), call)
    assert not obs.ok
    assert obs.metadata["blocked_by"] == "execution_ledger"
