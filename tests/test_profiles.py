from pathlib import Path

import pytest

from adaptive_harness.config import HarnessConfig, ModelRole
from adaptive_harness.contracts import Goal, ModelTurn, RiskLevel, RunStatus, ToolCall, ToolSpec
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.profiles import ProfileRegistry
from adaptive_harness.providers.fake import FakeProvider
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore


def write_profile(root: Path, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "generic.yaml").write_text(
        "name: generic\ntool_patterns: []\nallowed_risks: [read, reversible_write, external_side_effect, privileged]\n",
        encoding="utf-8",
    )
    (root / "advisory.yaml").write_text(text, encoding="utf-8")


def test_profile_registry_selects_task_family_and_explicit_override(tmp_path: Path):
    root = tmp_path / "profiles"
    write_profile(
        root,
        "name: advisory\nmatch_terms: [advice, conseil]\ntool_patterns: [fs_read]\nallowed_risks: [read]\n",
    )
    registry = ProfileRegistry(root)
    assert registry.select(Goal(text="I need advice")).name == "advisory"
    assert registry.select(Goal(text="anything", profile="advisory")).name == "advisory"


@pytest.mark.asyncio
async def test_profile_reduction_blocks_hidden_write_even_if_global_scope_allows_it(tmp_path: Path):
    root = tmp_path / "profiles"
    write_profile(
        root,
        "name: advisory\nmatch_terms: [advice]\ntool_patterns: [fs_read]\nallowed_risks: [read]\n",
    )
    provider = FakeProvider(
        [
            ModelTurn(tool_calls=[ToolCall(name="fs_write", arguments={"path": "x", "content": "bad"})]),
            ModelTurn(content="done"),
        ]
    )
    tools = ToolRegistry()
    tools.register(
        ToolSpec(name="fs_read", description="read", input_schema={"type": "object"}, risk=RiskLevel.READ),
        lambda _: "ok",
    )
    wrote = []
    tools.register(
        ToolSpec(
            name="fs_write",
            description="write",
            input_schema={"type": "object"},
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
        ),
        lambda a: wrote.append(a),
    )
    cfg = HarnessConfig(
        primary=ModelRole(model="fake"),
        workspace=str(tmp_path),
        allowed_scopes={"fs:write"},
    )
    traces = TraceStore(str(tmp_path / "trace.sqlite"))
    runtime = AgentRuntime(
        config=cfg,
        provider=provider,
        tools=tools,
        traces=traces,
        memory=MemoryStore(str(tmp_path / "memory.sqlite")),
        profiles=ProfileRegistry(root),
    )
    result = await runtime.run(Goal(text="give me advice", max_steps=3))
    assert result.status == RunStatus.SUCCEEDED
    assert wrote == []
    assert any(e["kind"] == "profile_tool_block" for e in traces.events(result.run_id))
