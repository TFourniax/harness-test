from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Freshness(str, Enum):
    IMMUTABLE = "immutable"
    STABLE = "stable"
    VOLATILE = "volatile"


class VerificationVerdict(str, Enum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    REFUTED = "refuted"


class VerificationCertificate(BaseModel):
    """Evidence certificate attached to one bounded worker result.

    A certificate is not a capability token and never grants permissions. ``evidence_strength``
    describes how much the compute learner may trust the outcome signal; it does not mean the
    entire natural-language answer is mathematically proven.
    """

    verdict: VerificationVerdict = VerificationVerdict.UNVERIFIED
    evidence_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    deterministic: bool = False
    scope_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    independent_sources: int = 0
    checks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def learning_success(self) -> float:
        if self.verdict == VerificationVerdict.VERIFIED:
            return 1.0
        if self.verdict == VerificationVerdict.REFUTED:
            return 0.0
        if self.verdict == VerificationVerdict.SUPPORTED:
            return min(0.8, max(0.0, self.score))
        return 0.5


class WorkItem(BaseModel):
    id: str
    task: str
    dependencies: list[str] = Field(default_factory=list)
    profile: str | None = None
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_value: float = Field(default=0.7, ge=0.0, le=1.0)
    critical: bool = False
    freshness: Freshness = Freshness.STABLE
    redundancy_group: str | None = None
    cacheable: bool = True


class WorkPlan(BaseModel):
    rationale: str = ""
    tasks: list[WorkItem] = Field(default_factory=list)

    def validate_dag(self) -> None:
        ids = {task.id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("work plan contains duplicate task ids")
        for task in self.tasks:
            unknown = set(task.dependencies) - ids
            if unknown:
                raise ValueError(f"task {task.id} has unknown dependencies: {sorted(unknown)}")
            if task.id in task.dependencies:
                raise ValueError(f"task {task.id} depends on itself")

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {task.id: task for task in self.tasks}

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError("work plan contains a dependency cycle")
            visiting.add(task_id)
            for dep in by_id[task_id].dependencies:
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)


class ContextCapsule(BaseModel):
    root_goal: str
    subtask: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    dependency_summaries: list[str] = Field(default_factory=list)
    reference_hint: str | None = None

    def render(self, max_dependency_chars: int = 8000) -> str:
        deps = "\n\n".join(self.dependency_summaries)
        deps = deps[:max_dependency_chars]
        reference = (
            "\n\nPRIOR VERIFIED ANALOGUE (hint only; re-check applicability):\n"
            + self.reference_hint[:4000]
            if self.reference_hint
            else ""
        )
        return (
            "You are a bounded specialist worker inside a larger orchestrated task.\n"
            "Solve only the assigned subtask. Do not broaden scope. Return a compact result that "
            "states the conclusion, evidence/observations used, uncertainty, and anything the parent "
            "orchestrator should verify. Prefer verify_workspace_command when a deterministic test, "
            "lint, build, typecheck, or compilation command is a genuine success postcondition; use "
            "source_fetch for source-grounded web evidence when available.\n\n"
            f"ROOT GOAL:\n{self.root_goal}\n\n"
            f"ASSIGNED SUBTASK:\n{self.subtask}\n\n"
            "SUCCESS CRITERIA:\n- "
            + "\n- ".join(self.success_criteria or ["Answer the assigned subtask correctly"])
            + "\n\nCONSTRAINTS:\n- "
            + "\n- ".join(self.constraints or ["None specified"])
            + ("\n\nDEPENDENCY RESULTS:\n" + deps if deps else "")
            + reference
        )


class AgentReport(BaseModel):
    task_id: str
    answer: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    role: Literal["cheap", "primary"] = "cheap"
    status: Literal["succeeded", "failed", "skipped", "cached"] = "succeeded"
    cost_usd: float = 0.0
    cache_status: Literal["miss", "exact", "semantic_reference"] = "miss"
    evidence_refs: list[str] = Field(default_factory=list)
    cache_key: str | None = None
    verification: VerificationCertificate | None = None
    attempt_count: int = 1

    def compact(self, max_chars: int = 5000) -> str:
        verify = "none"
        if self.verification is not None:
            verify = (
                f"{self.verification.verdict.value}:"
                f"{self.verification.evidence_strength:.2f}"
            )
        return (
            f"TASK {self.task_id} status={self.status} role={self.role} "
            f"confidence={self.confidence:.2f} cache={self.cache_status} "
            f"verification={verify} attempts={self.attempt_count}\n"
            + self.answer[:max_chars]
        )


class FollowUp(BaseModel):
    task: str
    profile: str | None = None
    difficulty: float = Field(default=0.6, ge=0.0, le=1.0)
    expected_value: float = Field(default=0.7, ge=0.0, le=1.0)
    freshness: Freshness = Freshness.STABLE


class SynthesisDecision(BaseModel):
    answer: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    should_continue: bool = False
    unresolved: list[str] = Field(default_factory=list)
    followups: list[FollowUp] = Field(default_factory=list)
