from __future__ import annotations

import json
from typing import Any

from adaptive_harness.contracts import RiskLevel, RunStatus, ToolSpec
from adaptive_harness.orchestration.context_budget import MissionContextBuilder
from adaptive_harness.orchestration.contracts import WorkItem
from adaptive_harness.orchestration.state_plane import TaskStateStore, TaskStatus
from adaptive_harness.orchestration.verification import VerificationEngine
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore


def _resolve_evidence(traces: TraceStore, call_ids: list[str]):
    ids = list(dict.fromkeys(str(value) for value in call_ids if str(value)))
    observations = traces.find_observations(ids)
    found = {obs.call_id for obs in observations}
    missing = [call_id for call_id in ids if call_id not in found]
    if missing:
        raise ValueError(f"unknown/non-executed evidence call ids: {missing}")
    if not observations:
        raise ValueError("at least one executed tool observation is required")
    return observations


def _certificate(
    verifier: VerificationEngine,
    *,
    description: str,
    profile: str | None,
    observations,
):
    work = WorkItem(id="mission-evidence", task=description, profile=profile)
    result = RunResult(
        run_id="mission-evidence",
        status=RunStatus.SUCCEEDED,
        answer="evidence-backed mission state update",
        observations=list(observations),
    )
    return verifier.certify(work, [result])


def register_state_tools(
    registry: ToolRegistry,
    *,
    store: TaskStateStore,
    traces: TraceStore,
    context_builder: MissionContextBuilder | None = None,
    writable: bool = True,
    default_context_tokens: int = 3000,
    fact_evidence_strength: float = 0.25,
) -> None:
    context_builder = context_builder or MissionContextBuilder()
    verifier = VerificationEngine()

    def mission_read(args: dict[str, Any]) -> str:
        snap = store.snapshot(args["mission_id"])
        budgeted = context_builder.build(
            snap,
            focus=args.get("focus", ""),
            focus_task_id=args.get("focus_task_id"),
            max_tokens=int(args.get("budget_tokens", default_context_tokens)),
        )
        return json.dumps(
            {
                "mission_id": snap.id,
                "revision": snap.revision,
                "status": snap.status.value,
                "ready_tasks": [task.id for task in store.ready_tasks(snap.id)],
                "selected_state_keys": budgeted.selected_keys,
                "context_tokens_estimate": budgeted.used_tokens,
                "context_budget": budgeted.budget_tokens,
                "omitted_state_items": budgeted.omitted_count,
                "context": budgeted.text,
            },
            ensure_ascii=False,
        )

    registry.register(
        ToolSpec(
            name="mission_state_read",
            description=(
                "Read a token-budgeted slice of durable mission state. Use for long/multi-session "
                "tasks instead of copying the full historical transcript. Returns the current "
                "mission revision; pass that revision on later writes to prevent lost updates."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "minLength": 1},
                    "focus": {"type": "string"},
                    "focus_task_id": {"type": ["string", "null"]},
                    "budget_tokens": {"type": "integer", "minimum": 64, "maximum": 16000},
                },
                "required": ["mission_id"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
            required_scopes={"fs:read"},
            idempotent=True,
            source="builtin",
        ),
        mission_read,
    )

    registry.register(
        ToolSpec(
            name="mission_state_audit",
            description=(
                "Audit durable mission-state invariants: dependency order, evidence-backed DONE "
                "tasks, mission completion consistency and fact provenance. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {"mission_id": {"type": "string", "minLength": 1}},
                "required": ["mission_id"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
            required_scopes={"fs:read"},
            idempotent=True,
            source="builtin",
        ),
        lambda args: json.dumps(
            [issue.model_dump(mode="json") for issue in store.audit(args["mission_id"])],
            ensure_ascii=False,
        ),
    )

    if not writable:
        return

    registry.register(
        ToolSpec(
            name="mission_state_create",
            description=(
                "Create or recover durable canonical state for a long-horizon goal. A stable "
                "external_key enables restart recovery."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "minLength": 1},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "external_key": {"type": ["string", "null"]},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
            idempotent=False,
            source="builtin",
        ),
        lambda args: store.create_mission(
            goal=args["goal"],
            success_criteria=args.get("success_criteria", []),
            external_key=args.get("external_key"),
        ).model_dump_json(),
    )

    registry.register(
        ToolSpec(
            name="mission_state_add_task",
            description=(
                "Add one durable mission task. Dependencies must exist. Stale expected_revision "
                "writes are rejected so parallel agents must reload/reconcile."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "minLength": 1},
                    "task_id": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "profile": {"type": ["string", "null"]},
                    "priority": {"type": "number", "minimum": 0, "maximum": 1},
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["mission_id", "task_id", "description", "expected_revision"],
                "additionalProperties": False,
            },
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
            idempotent=False,
            source="builtin",
        ),
        lambda args: store.add_task(
            mission_id=args["mission_id"],
            task_id=args["task_id"],
            description=args["description"],
            dependencies=args.get("dependencies", []),
            success_criteria=args.get("success_criteria", []),
            profile=args.get("profile"),
            priority=float(args.get("priority", 0.5)),
            expected_revision=int(args["expected_revision"]),
        ).model_dump_json(),
    )

    registry.register(
        ToolSpec(
            name="mission_state_transition",
            description=(
                "Move a mission task between pending/active/blocked/failed with optimistic revision "
                "control. DONE is deliberately forbidden here; use mission_state_finalize with real "
                "executed evidence call ids."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "minLength": 1},
                    "task_id": {"type": "string", "minLength": 1},
                    "target": {
                        "type": "string",
                        "enum": ["pending", "active", "blocked", "failed"],
                    },
                    "note": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["mission_id", "task_id", "target", "expected_revision"],
                "additionalProperties": False,
            },
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
            idempotent=False,
            source="builtin",
        ),
        lambda args: store.transition_task(
            mission_id=args["mission_id"],
            task_id=args["task_id"],
            target=TaskStatus(args["target"]),
            note=args.get("note", ""),
            expected_revision=int(args["expected_revision"]),
        ).model_dump_json(),
    )

    def record_fact(args: dict[str, Any]) -> str:
        observations = _resolve_evidence(traces, args["evidence_call_ids"])
        cert = _certificate(
            verifier,
            description=args["claim"],
            profile=args.get("profile") or "research",
            observations=observations,
        )
        return store.record_fact(
            mission_id=args["mission_id"],
            claim=args["claim"],
            certificate=cert,
            source_task_id=args.get("source_task_id"),
            expected_revision=int(args["expected_revision"]),
            min_evidence_strength=fact_evidence_strength,
        ).model_dump_json()

    registry.register(
        ToolSpec(
            name="mission_state_record_fact",
            description=(
                "Record a canonical mission fact only from call ids resolving to prior executed "
                "tool observations. Evidence strength is derived by the harness, never supplied by "
                "the model. Invented/non-executed ids fail."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "minLength": 1},
                    "claim": {"type": "string", "minLength": 1},
                    "evidence_call_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "source_task_id": {"type": ["string", "null"]},
                    "profile": {"type": ["string", "null"]},
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["mission_id", "claim", "evidence_call_ids", "expected_revision"],
                "additionalProperties": False,
            },
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
            idempotent=False,
            source="builtin",
        ),
        record_fact,
    )

    def finalize(args: dict[str, Any]) -> str:
        snap = store.snapshot(args["mission_id"])
        task = next((item for item in snap.tasks if item.id == args["task_id"]), None)
        if task is None:
            raise KeyError(f"unknown mission task: {args['task_id']}")
        observations = _resolve_evidence(traces, args["evidence_call_ids"])
        cert = _certificate(
            verifier,
            description=task.description,
            profile=task.profile,
            observations=observations,
        )
        return store.finalize_task(
            mission_id=snap.id,
            task_id=task.id,
            certificate=cert,
            output_summary=args.get("output_summary", ""),
            expected_revision=int(args["expected_revision"]),
        ).model_dump_json()

    registry.register(
        ToolSpec(
            name="mission_state_finalize",
            description=(
                "Finalize a mission task only from concrete prior executed evidence call ids. The "
                "harness resolves observations, builds a scope-aware certificate, and enforces "
                "dependencies/evidence thresholds; the model cannot mark DONE by assertion."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "minLength": 1},
                    "task_id": {"type": "string", "minLength": 1},
                    "evidence_call_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "output_summary": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["mission_id", "task_id", "evidence_call_ids", "expected_revision"],
                "additionalProperties": False,
            },
            risk=RiskLevel.REVERSIBLE_WRITE,
            required_scopes={"fs:write"},
            idempotent=False,
            source="builtin",
        ),
        finalize,
    )
