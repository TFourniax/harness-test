from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, Enum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    PRIVILEGED = "privileged"


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    USER = "user"
    TOOL = "tool"
    UNTRUSTED_EXTERNAL = "untrusted_external"


class RunStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Goal(BaseModel):
    text: str
    session_id: str | None = None
    profile: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    max_steps: int = 40
    max_cost_usd: float | None = None
    model_role: Literal["auto", "primary", "cheap"] = "auto"


class EvidenceRequirement(BaseModel):
    id: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    satisfied: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel = RiskLevel.READ
    required_scopes: set[str] = Field(default_factory=set)
    idempotent: bool = True
    source: str = "builtin"


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    call_id: str
    tool_name: str
    ok: bool
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    trust: TrustLevel = TrustLevel.TOOL
    created_at: datetime = Field(default_factory=utcnow)


class ToolExecutionResult(BaseModel):
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    trust: TrustLevel | None = None


class ModelTurn(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    protocol_error: str | None = None
    raw: Any = None


class TraceEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utcnow)


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    action_type: str
    summary: str
    payload: dict[str, Any]
    fingerprint: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=utcnow)


class EvalResult(BaseModel):
    name: str
    passed: bool
    score: float = 0.0
    details: str = ""
    deterministic: bool = False


class ImprovementProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    weakness: str
    hypothesis: str
    patch: str
    target_files: list[str]
    eval_results: list[EvalResult] = Field(default_factory=list)
    baseline_score: float | None = None
    candidate_score: float | None = None
    regression_passed: bool = False
    security_passed: bool = False
    human_status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=utcnow)


class SkillProposal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    body: str
    source_run_ids: list[str] = Field(default_factory=list)
    source_goal_fingerprints: list[str] = Field(default_factory=list)
    quarantine_passed: bool = False
    quarantine_report: list[str] = Field(default_factory=list)
    human_status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=utcnow)
