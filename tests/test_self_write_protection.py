from pathlib import Path

import pytest

from adaptive_harness.contracts import ToolCall
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.tools.builtin import _path_is_protected, register_builtin_tools


def test_harness_source_cannot_be_written_via_normal_agent_tool(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaptive-agent-harness"\n', encoding="utf-8"
    )
    target = tmp_path / "src" / "adaptive_harness" / "runtime" / "policy.py"
    target.parent.mkdir(parents=True)
    target.write_text("safe", encoding="utf-8")
    registry = ToolRegistry()
    register_builtin_tools(registry, str(tmp_path))

    import asyncio
    obs = asyncio.run(
        registry.execute(
            ToolCall(name="fs_write", arguments={"path": "src/adaptive_harness/runtime/policy.py", "content": "pwn"})
        )
    )
    assert not obs.ok
    assert "protected" in obs.content.lower()
    assert target.read_text(encoding="utf-8") == "safe"


def test_target_project_normal_source_is_not_mistaken_for_harness(tmp_path: Path):
    registry = ToolRegistry()
    register_builtin_tools(registry, str(tmp_path))
    import asyncio
    obs = asyncio.run(
        registry.execute(
            ToolCall(name="fs_write", arguments={"path": "src/app.py", "content": "print(1)"})
        )
    )
    assert obs.ok
    assert (tmp_path / "src" / "app.py").exists()


def test_protected_path_match_is_boundary_aware():
    assert _path_is_protected(".harness/proposals/x.json", (".harness",))
    assert not _path_is_protected(".harness-notes.txt", (".harness",))
