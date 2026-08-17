from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_required(path: str, old: str, new: str) -> None:
    text = read(path)
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"v0.6 finalizer anchor missing: {path}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


# Package identity / entrypoint. Keep this tolerant of earlier wording edits.
pyproject = read("pyproject.toml")
pyproject = pyproject.replace('version = "0.5.0"', 'version = "0.6.0"', 1)
pyproject = re.sub(
    r'^description = ".*"$',
    'description = "Model-agnostic, evidence-gated agent harness with sparse teams, adaptive compute and durable mission state."',
    pyproject,
    count=1,
    flags=re.MULTILINE,
)
pyproject = pyproject.replace(
    'adaptive-harness = "adaptive_harness.v05_cli:app"',
    'adaptive-harness = "adaptive_harness.v06_cli:app"',
    1,
)
if 'version = "0.6.0"' not in pyproject or 'adaptive_harness.v06_cli:app' not in pyproject:
    raise SystemExit("failed to finalize pyproject v0.6 identity")
write("pyproject.toml", pyproject)

init = read("src/adaptive_harness/__init__.py")
init = init.replace('__version__ = "0.5.0"', '__version__ = "0.6.0"', 1)
write("src/adaptive_harness/__init__.py", init)

# Trace evidence resolver: only observations already emitted by executed tools are eligible.
trace_path = "src/adaptive_harness/runtime/trace_store.py"
trace = read(trace_path)
if "Observation" not in trace.splitlines()[6]:
    trace = trace.replace(
        "from adaptive_harness.contracts import ApprovalRequest, TraceEvent\n",
        "from adaptive_harness.contracts import ApprovalRequest, Observation, TraceEvent\n",
        1,
    )
if "def find_observations(" not in trace:
    marker = "    def get_channel_cursor(self, channel: str) -> int | None:\n"
    if marker not in trace:
        raise SystemExit("trace_store insertion anchor missing")
    block = '''    def find_observations(self, call_ids: list[str]) -> list[Observation]:\n        \"\"\"Resolve prior tool observations by concrete call id across runs.\n\n        Only trace events emitted after actual tool execution are considered, so a model cannot\n        manufacture a call id and use it as mission evidence.\n        \"\"\"\n        wanted = {str(value) for value in call_ids if str(value)}\n        if not wanted:\n            return []\n        found: dict[str, Observation] = {}\n        with sqlite3.connect(self.path) as db:\n            rows = db.execute(\n                \"SELECT payload FROM events \"\n                \"WHERE kind IN ('tool_observation','approved_tool_observation') \"\n                \"ORDER BY created_at DESC\"\n            ).fetchall()\n        for (raw,) in rows:\n            try:\n                obs = Observation.model_validate(json.loads(raw))\n            except Exception:\n                continue\n            if obs.call_id in wanted and obs.call_id not in found:\n                found[obs.call_id] = obs\n                if len(found) == len(wanted):\n                    break\n        return [found[call_id] for call_id in call_ids if call_id in found]\n\n'''
    trace = trace.replace(marker, block + marker, 1)
write(trace_path, trace)

# Explicit v0.6 config model.
v06_cfg_path = "src/adaptive_harness/v06_config.py"
v06_cfg = read(v06_cfg_path)
v06_cfg = v06_cfg.replace("from pydantic import Field\n", "from pydantic import BaseModel, Field\n", 1)
v06_cfg = v06_cfg.replace("class LongHorizonConfig(BaseHarnessConfig.__base__):\n", "class LongHorizonConfig(BaseModel):\n", 1)
write(v06_cfg_path, v06_cfg)

# Canonical Mission State Plane: hard task ceiling and optimistic revisions remain authoritative.
state_path = "src/adaptive_harness/orchestration/state_plane.py"
state = read(state_path)
if "self.max_tasks" not in state:
    replace_required(
        state_path,
        "    def __init__(self, path: str | Path, *, done_evidence_strength: float = 0.60) -> None:\n        self.path = str(path)\n        self.done_evidence_strength = float(done_evidence_strength)\n",
        "    def __init__(\n        self,\n        path: str | Path,\n        *,\n        done_evidence_strength: float = 0.60,\n        max_tasks: int = 256,\n    ) -> None:\n        self.path = str(path)\n        self.done_evidence_strength = float(done_evidence_strength)\n        self.max_tasks = max(1, int(max_tasks))\n",
    )
    state = read(state_path)
if "mission task limit reached" not in state:
    replace_required(
        state_path,
        "            known = {str(row[0]) for row in db.execute(\"SELECT task_id FROM mission_tasks WHERE mission_id=?\", (mission_id,)).fetchall()}\n            unknown = set(deps) - known\n",
        "            known = {str(row[0]) for row in db.execute(\"SELECT task_id FROM mission_tasks WHERE mission_id=?\", (mission_id,)).fetchall()}\n            if len(known) >= self.max_tasks:\n                raise ValueError(f\"mission task limit reached: {self.max_tasks}\")\n            unknown = set(deps) - known\n",
    )

# State tools: evidence must resolve to real trace observations and be fresh for this mission/task.
tools_path = "src/adaptive_harness/tools/state_tools.py"
tools = read(tools_path)
if "from datetime import datetime" not in tools:
    tools = tools.replace("import json\nfrom typing import Any\n", "import json\nfrom datetime import datetime\nfrom typing import Any\n", 1)
if "not_before: str | None = None" not in tools:
    tools = tools.replace(
        "def _resolve_evidence(traces: TraceStore, call_ids: list[str]):\n    ids = list(dict.fromkeys(str(value) for value in call_ids if str(value)))\n    observations = traces.find_observations(ids)\n",
        "def _resolve_evidence(\n    traces: TraceStore,\n    call_ids: list[str],\n    *,\n    not_before: str | None = None,\n):\n    ids = list(dict.fromkeys(str(value) for value in call_ids if str(value)))\n    observations = traces.find_observations(ids)\n",
        1,
    )
    tools = tools.replace(
        "    if not observations:\n        raise ValueError(\"at least one executed tool observation is required\")\n    return observations\n",
        "    if not observations:\n        raise ValueError(\"at least one executed tool observation is required\")\n    if not_before:\n        boundary = datetime.fromisoformat(not_before)\n        stale = [obs.call_id for obs in observations if obs.created_at < boundary]\n        if stale:\n            raise ValueError(f\"evidence predates mission/task creation: {stale}\")\n    return observations\n",
        1,
    )
if "boundary = source_task.created_at if source_task else snap.created_at" not in tools:
    tools = tools.replace(
        "    def record_fact(args: dict[str, Any]) -> str:\n        observations = _resolve_evidence(traces, args[\"evidence_call_ids\"])\n        cert = _certificate(\n",
        "    def record_fact(args: dict[str, Any]) -> str:\n        snap = store.snapshot(args[\"mission_id\"])\n        source_task = next(\n            (task for task in snap.tasks if task.id == args.get(\"source_task_id\")),\n            None,\n        )\n        boundary = source_task.created_at if source_task else snap.created_at\n        observations = _resolve_evidence(\n            traces, args[\"evidence_call_ids\"], not_before=boundary\n        )\n        cert = _certificate(\n",
        1,
    )
if "not_before=task.created_at" not in tools:
    tools = tools.replace(
        "        observations = _resolve_evidence(traces, args[\"evidence_call_ids\"])\n        cert = _certificate(\n            verifier,\n            description=task.description,\n",
        "        observations = _resolve_evidence(\n            traces, args[\"evidence_call_ids\"], not_before=task.created_at\n        )\n        cert = _certificate(\n            verifier,\n            description=task.description,\n",
        1,
    )
write(tools_path, tools)

# Wire hard mission-size ceiling through v0.6 factory.
v06_cli_path = "src/adaptive_harness/v06_cli.py"
v06_cli = read(v06_cli_path)
if "max_tasks=cfg.long_horizon.max_tasks_per_mission" not in v06_cli:
    v06_cli = v06_cli.replace(
        "        done_evidence_strength=cfg.long_horizon.task_done_evidence_strength,\n    )\n",
        "        done_evidence_strength=cfg.long_horizon.task_done_evidence_strength,\n        max_tasks=cfg.long_horizon.max_tasks_per_mission,\n    )\n",
        1,
    )
write(v06_cli_path, v06_cli)

# Protect State/Context/Policy-Lab surfaces from autonomous self-promotion.
security_path = "src/adaptive_harness/security.py"
security = read(security_path)
insertions = [
    ('    "src/adaptive_harness/v05_cli.py",\n', '    "src/adaptive_harness/v06_cli.py",\n'),
    ('    "src/adaptive_harness/tools/evidence_tools.py",\n', '    "src/adaptive_harness/tools/state_tools.py",\n'),
    ('    "src/adaptive_harness/orchestration/policy_arena.py",\n', '    "src/adaptive_harness/orchestration/policy_lab.py",\n'),
    ('    "src/adaptive_harness/orchestration/benchmark.py",\n', '    "src/adaptive_harness/orchestration/state_plane.py",\n    "src/adaptive_harness/orchestration/context_budget.py",\n'),
]
for anchor, extra in insertions:
    first_extra_line = extra.splitlines()[0].strip()
    if first_extra_line not in security:
        if anchor not in security:
            raise SystemExit(f"v0.6 security anchor missing: {anchor!r}")
        security = security.replace(anchor, anchor + extra, 1)
write(security_path, security)

# Local-first default config; no external state/vector service required.
example_path = "config/harness.example.yaml"
example = read(example_path)
if "\nlong_horizon:\n" not in example:
    marker = "\n# Optional private Telegram control channel."
    if marker not in example:
        marker = "\n\n# Optional human communication channel."
    if marker not in example:
        raise SystemExit("config example insertion marker missing")
    block = """

# Durable Mission Plane (v0.6): canonical task/fact state lives outside model context.
# Workers can read budgeted slices; only the governed parent receives write tools.
long_horizon:
  enabled: true
  state_db: .harness/task-state.sqlite3
  context_budget_tokens: 3000
  task_done_evidence_strength: 0.60
  fact_evidence_strength: 0.25
  max_tasks_per_mission: 256
"""
    example = example.replace(marker, block + marker, 1)
write(example_path, example)

print("Applied idempotent v0.6 direct-source finalization")
