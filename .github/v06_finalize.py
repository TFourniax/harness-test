from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"v0.6 finalizer anchor missing: {path}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "pyproject.toml",
    'version = "0.5.0"',
    'version = "0.6.0"',
)
replace_once(
    "pyproject.toml",
    'description = "Model-agnostic, evidence-gated agent harness with sparse teams and adaptive compute economics."',
    'description = "Model-agnostic, evidence-gated agent harness with sparse teams, adaptive compute and durable mission state."',
)
replace_once(
    "pyproject.toml",
    'adaptive-harness = "adaptive_harness.v05_cli:app"',
    'adaptive-harness = "adaptive_harness.v06_cli:app"',
)
replace_once(
    "src/adaptive_harness/__init__.py",
    '__version__ = "0.5.0"',
    '__version__ = "0.6.0"',
)

# Extend TraceStore with a read-only resolver for evidence ids actually emitted by executed tools.
replace_once(
    "src/adaptive_harness/runtime/trace_store.py",
    "from adaptive_harness.contracts import ApprovalRequest, TraceEvent\n",
    "from adaptive_harness.contracts import ApprovalRequest, Observation, TraceEvent\n",
)
replace_once(
    "src/adaptive_harness/runtime/trace_store.py",
    "    def get_channel_cursor(self, channel: str) -> int | None:\n",
    '''    def find_observations(self, call_ids: list[str]) -> list[Observation]:\n        """Resolve concrete prior tool observations by call id across runs.\n\n        Only events emitted after actual tool execution are considered. Unknown/model-invented\n        ids therefore cannot become mission evidence.\n        """\n        wanted = {str(value) for value in call_ids if str(value)}\n        if not wanted:\n            return []\n        found: dict[str, Observation] = {}\n        with sqlite3.connect(self.path) as db:\n            rows = db.execute(\n                "SELECT payload FROM events "\n                "WHERE kind IN ('tool_observation','approved_tool_observation') "\n                "ORDER BY created_at DESC"\n            ).fetchall()\n        for (raw,) in rows:\n            try:\n                obs = Observation.model_validate(json.loads(raw))\n            except Exception:\n                continue\n            if obs.call_id in wanted and obs.call_id not in found:\n                found[obs.call_id] = obs\n                if len(found) == len(wanted):\n                    break\n        return [found[call_id] for call_id in call_ids if call_id in found]\n\n    def get_channel_cursor(self, channel: str) -> int | None:\n''',
)

# Make the v0.6 config explicit rather than relying on BaseHarnessConfig.__base__.
replace_once(
    "src/adaptive_harness/v06_config.py",
    "from pydantic import Field\n",
    "from pydantic import BaseModel, Field\n",
)
replace_once(
    "src/adaptive_harness/v06_config.py",
    "class LongHorizonConfig(BaseHarnessConfig.__base__):\n",
    "class LongHorizonConfig(BaseModel):\n",
)

# Enforce the configured maximum mission size in the canonical state store.
replace_once(
    "src/adaptive_harness/orchestration/state_plane.py",
    "    def __init__(self, path: str | Path, *, done_evidence_strength: float = 0.60) -> None:\n        self.path = str(path)\n        self.done_evidence_strength = float(done_evidence_strength)\n",
    "    def __init__(\n        self,\n        path: str | Path,\n        *,\n        done_evidence_strength: float = 0.60,\n        max_tasks: int = 256,\n    ) -> None:\n        self.path = str(path)\n        self.done_evidence_strength = float(done_evidence_strength)\n        self.max_tasks = max(1, int(max_tasks))\n",
)
replace_once(
    "src/adaptive_harness/orchestration/state_plane.py",
    "            known = {str(row[0]) for row in db.execute(\"SELECT task_id FROM mission_tasks WHERE mission_id=?\", (mission_id,)).fetchall()}\n            unknown = set(deps) - known\n",
    "            known = {str(row[0]) for row in db.execute(\"SELECT task_id FROM mission_tasks WHERE mission_id=?\", (mission_id,)).fetchall()}\n            if len(known) >= self.max_tasks:\n                raise ValueError(f\"mission task limit reached: {self.max_tasks}\")\n            unknown = set(deps) - known\n",
)

# Bind mission evidence to observations created after the relevant mission/task existed.
replace_once(
    "src/adaptive_harness/tools/state_tools.py",
    "import json\nfrom typing import Any\n",
    "import json\nfrom datetime import datetime\nfrom typing import Any\n",
)
replace_once(
    "src/adaptive_harness/tools/state_tools.py",
    "def _resolve_evidence(traces: TraceStore, call_ids: list[str]):\n    ids = list(dict.fromkeys(str(value) for value in call_ids if str(value)))\n    observations = traces.find_observations(ids)\n",
    "def _resolve_evidence(\n    traces: TraceStore,\n    call_ids: list[str],\n    *,\n    not_before: str | None = None,\n):\n    ids = list(dict.fromkeys(str(value) for value in call_ids if str(value)))\n    observations = traces.find_observations(ids)\n",
)
replace_once(
    "src/adaptive_harness/tools/state_tools.py",
    "    if not observations:\n        raise ValueError(\"at least one executed tool observation is required\")\n    return observations\n",
    "    if not observations:\n        raise ValueError(\"at least one executed tool observation is required\")\n    if not_before:\n        boundary = datetime.fromisoformat(not_before)\n        stale = [obs.call_id for obs in observations if obs.created_at < boundary]\n        if stale:\n            raise ValueError(f\"evidence predates mission/task creation: {stale}\")\n    return observations\n",
)
replace_once(
    "src/adaptive_harness/tools/state_tools.py",
    "    def record_fact(args: dict[str, Any]) -> str:\n        observations = _resolve_evidence(traces, args[\"evidence_call_ids\"])\n        cert = _certificate(\n",
    "    def record_fact(args: dict[str, Any]) -> str:\n        snap = store.snapshot(args[\"mission_id\"])\n        source_task = next(\n            (task for task in snap.tasks if task.id == args.get(\"source_task_id\")),\n            None,\n        )\n        boundary = source_task.created_at if source_task else snap.created_at\n        observations = _resolve_evidence(\n            traces, args[\"evidence_call_ids\"], not_before=boundary\n        )\n        cert = _certificate(\n",
)
replace_once(
    "src/adaptive_harness/tools/state_tools.py",
    "        observations = _resolve_evidence(traces, args[\"evidence_call_ids\"])\n        cert = _certificate(\n            verifier,\n            description=task.description,\n",
    "        observations = _resolve_evidence(\n            traces, args[\"evidence_call_ids\"], not_before=task.created_at\n        )\n        cert = _certificate(\n            verifier,\n            description=task.description,\n",
)

# Pass the hard mission-size ceiling through the runtime factory.
replace_once(
    "src/adaptive_harness/v06_cli.py",
    "        done_evidence_strength=cfg.long_horizon.task_done_evidence_strength,\n    )\n",
    "        done_evidence_strength=cfg.long_horizon.task_done_evidence_strength,\n        max_tasks=cfg.long_horizon.max_tasks_per_mission,\n    )\n",
)

# Extend the immutable self-evolution boundary with state/context/policy-lab components.
security = Path("src/adaptive_harness/security.py")
security_text = security.read_text(encoding="utf-8")
anchor = '    "src/adaptive_harness/v05_cli.py",\n'
if '    "src/adaptive_harness/v06_cli.py",\n' not in security_text:
    if anchor not in security_text:
        raise SystemExit("v0.6 security anchor missing")
    security_text = security_text.replace(
        anchor,
        anchor + '    "src/adaptive_harness/v06_cli.py",\n',
        1,
    )
for previous, extra in [
    ('    "src/adaptive_harness/tools/evidence_tools.py",\n', '    "src/adaptive_harness/tools/state_tools.py",\n'),
    ('    "src/adaptive_harness/orchestration/policy_arena.py",\n', '    "src/adaptive_harness/orchestration/policy_lab.py",\n'),
    ('    "src/adaptive_harness/orchestration/benchmark.py",\n', '    "src/adaptive_harness/orchestration/state_plane.py",\n    "src/adaptive_harness/orchestration/context_budget.py",\n'),
]:
    if extra.strip() not in security_text:
        if previous not in security_text:
            raise SystemExit(f"v0.6 security anchor missing: {previous!r}")
        security_text = security_text.replace(previous, previous + extra, 1)
security.write_text(security_text, encoding="utf-8")

# Extend example config without requiring any external backend/service.
example = Path("config/harness.example.yaml")
example_text = example.read_text(encoding="utf-8")
if "\nlong_horizon:\n" not in example_text:
    marker = "\n\n# Optional human communication channel."
    block = """

# Durable Mission Plane (v0.6): canonical task/fact state lives outside model context.
# Workers can read budgeted slices; only the governed parent receives the write tools.
long_horizon:
  enabled: true
  state_db: .harness/task-state.sqlite3
  context_budget_tokens: 3000
  task_done_evidence_strength: 0.60
  fact_evidence_strength: 0.25
  max_tasks_per_mission: 256
"""
    if marker not in example_text:
        raise SystemExit("config example insertion marker missing")
    example_text = example_text.replace(marker, block + marker, 1)
example.write_text(example_text, encoding="utf-8")

print("Applied v0.6 direct-source finalization")
