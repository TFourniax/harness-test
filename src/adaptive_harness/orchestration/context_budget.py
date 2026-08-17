from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from adaptive_harness.orchestration.state_plane import FactStatus, MissionSnapshot, TaskStatus
from adaptive_harness.orchestration.vector_cache import HashingVectorizer


@dataclass(frozen=True)
class ContextCandidate:
    key: str
    text: str
    kind: str
    priority: float = 0.5
    relevance: float = 0.0
    evidence_strength: float = 0.0
    freshness: float = 0.5
    required: bool = False


class BudgetedContext(BaseModel):
    text: str
    selected_keys: list[str] = Field(default_factory=list)
    used_tokens: int = 0
    budget_tokens: int = 0
    omitted_count: int = 0


class ContextBudgetAllocator:
    """Deterministic zero-service context packing by evidence/value per estimated token."""

    def __init__(self, dimensions: int = 384) -> None:
        self.vectorizer = HashingVectorizer(dimensions)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text.encode("utf-8")) / 3.5))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9À-ÿ_./:-]+", text.lower()))

    @classmethod
    def _overlap(cls, a: str, b: str) -> float:
        sa, sb = cls._tokens(a), cls._tokens(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def allocate(
        self, candidates: list[ContextCandidate], *, query: str, max_tokens: int
    ) -> BudgetedContext:
        budget = max(64, int(max_tokens))
        query_vec = self.vectorizer.encode(query)
        selected: list[ContextCandidate] = []
        used = 0
        required = [c for c in candidates if c.required]
        optional = [c for c in candidates if not c.required]
        for candidate in required:
            cost = self.estimate_tokens(candidate.text)
            remaining = budget - used
            if remaining <= 0:
                break
            if cost > remaining:
                char_budget = max(32, int(remaining * 3.5))
                candidate = ContextCandidate(
                    key=candidate.key,
                    text=candidate.text[:char_budget] + "\n[TRUNCATED_TO_CONTEXT_BUDGET]",
                    kind=candidate.kind,
                    priority=candidate.priority,
                    relevance=candidate.relevance,
                    evidence_strength=candidate.evidence_strength,
                    freshness=candidate.freshness,
                    required=True,
                )
                cost = min(remaining, self.estimate_tokens(candidate.text))
            selected.append(candidate)
            used += cost

        def score(candidate: ContextCandidate) -> float:
            vec_rel = max(
                0.0,
                self.vectorizer.cosine(query_vec, self.vectorizer.encode(candidate.text)),
            )
            relevance = max(candidate.relevance, vec_rel)
            base = (
                0.29 * max(0.0, min(1.0, candidate.priority))
                + 0.31 * max(0.0, min(1.0, relevance))
                + 0.30 * max(0.0, min(1.0, candidate.evidence_strength))
                + 0.10 * max(0.0, min(1.0, candidate.freshness))
            )
            redundancy = max(
                (self._overlap(candidate.text, selected_item.text) for selected_item in selected),
                default=0.0,
            )
            novelty = max(0.25, 1.0 - 0.70 * redundancy)
            return base * novelty / math.sqrt(self.estimate_tokens(candidate.text))

        remaining_candidates = list(optional)
        while remaining_candidates and used < budget:
            ranked = sorted(
                remaining_candidates, key=lambda candidate: (score(candidate), candidate.key), reverse=True
            )
            admitted = None
            for candidate in ranked:
                cost = self.estimate_tokens(candidate.text)
                if used + cost <= budget:
                    admitted = candidate
                    break
            if admitted is None:
                break
            selected.append(admitted)
            used += self.estimate_tokens(admitted.text)
            remaining_candidates.remove(admitted)
        text = "\n\n".join(f"[{item.kind}:{item.key}]\n{item.text}" for item in selected)
        return BudgetedContext(
            text=text,
            selected_keys=[item.key for item in selected],
            used_tokens=min(used, budget),
            budget_tokens=budget,
            omitted_count=max(0, len(candidates) - len(selected)),
        )


class MissionContextBuilder:
    def __init__(self, allocator: ContextBudgetAllocator | None = None) -> None:
        self.allocator = allocator or ContextBudgetAllocator()

    def build(
        self,
        snapshot: MissionSnapshot,
        *,
        focus: str = "",
        focus_task_id: str | None = None,
        max_tokens: int = 3000,
    ) -> BudgetedContext:
        query = "\n".join(
            part
            for part in (
                snapshot.goal,
                focus,
                next((task.description for task in snapshot.tasks if task.id == focus_task_id), ""),
            )
            if part
        )
        candidates: list[ContextCandidate] = [
            ContextCandidate(
                key="mission-goal",
                kind="mission",
                text=(
                    f"MISSION {snapshot.id} revision={snapshot.revision} status={snapshot.status.value}\n"
                    f"GOAL: {snapshot.goal}\nSUCCESS CRITERIA: {snapshot.success_criteria}"
                ),
                priority=1.0,
                relevance=1.0,
                evidence_strength=1.0,
                freshness=1.0,
                required=True,
            )
        ]
        done_ids = {task.id for task in snapshot.tasks if task.status == TaskStatus.DONE}
        active_count = len([task for task in snapshot.tasks if task.status == TaskStatus.ACTIVE])
        for task in snapshot.tasks:
            deps = ", ".join(task.dependencies) or "none"
            cert_strength = task.verification.evidence_strength if task.verification else 0.0
            ready = task.status == TaskStatus.PENDING and all(
                dep in done_ids for dep in task.dependencies
            )
            required = task.id == focus_task_id or task.status in {
                TaskStatus.ACTIVE,
                TaskStatus.BLOCKED,
            }
            if ready and active_count == 0:
                required = True
            text = (
                f"TASK {task.id} status={task.status.value} priority={task.priority:.2f} deps={deps}\n"
                f"{task.description}\nSUCCESS: {task.success_criteria}"
            )
            if task.note:
                text += f"\nNOTE: {task.note}"
            if task.output_summary:
                text += f"\nOUTPUT: {task.output_summary[:5000]}"
            candidates.append(
                ContextCandidate(
                    key=f"task:{task.id}",
                    kind="task",
                    text=text,
                    priority=max(task.priority, 0.85 if required else 0.0),
                    relevance=1.0 if task.id == focus_task_id else 0.75 if ready else 0.45,
                    evidence_strength=cert_strength,
                    freshness=0.9 if task.status != TaskStatus.DONE else 0.55,
                    required=required,
                )
            )
        for fact in snapshot.facts:
            prefix = (
                "REFUTED FACT — do not rely on as true"
                if fact.status == FactStatus.REFUTED
                else fact.status.value.upper()
            )
            candidates.append(
                ContextCandidate(
                    key=f"fact:{fact.id}",
                    kind="fact",
                    text=f"{prefix}: {fact.claim}\nEVIDENCE REFS: {fact.evidence_refs}",
                    priority=0.72 if fact.status != FactStatus.REFUTED else 0.88,
                    relevance=0.55,
                    evidence_strength=fact.evidence_strength,
                    freshness=0.65,
                )
            )
        return self.allocator.allocate(candidates, query=query, max_tokens=max_tokens)
