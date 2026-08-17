from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from adaptive_harness.orchestration.distributed_control import (
    BudgetExceeded,
    DistributedControlStore,
)


class CellLimitExceeded(RuntimeError):
    pass


class CellStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class CellSpec(BaseModel):
    id: str
    task: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    profile: str | None = None
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    critical: bool = False
    decomposable: bool = True
    budget_weight: float = Field(default=1.0, gt=0.0)


class CellPlan(BaseModel):
    children: list[CellSpec] = Field(default_factory=list)
    planner_cost_usd: float = Field(default=0.0, ge=0.0)
    rationale: str = ""


class LeafOutcome(BaseModel):
    answer: str
    success: bool
    cost_usd: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)


class CellExecutionResult(BaseModel):
    cell_id: str
    # `success` is intentionally strict: True means the scheduled cell completed, not merely that
    # one useful partial branch exists. Callers may inspect `status=partial` for usable incomplete work.
    success: bool
    status: CellStatus
    answer: str
    cost_usd: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    children: list["CellExecutionResult"] = Field(default_factory=list)
    leaf_attempts: int = 0
    cells_opened: int = 0
    reason: str = ""
    blocked_reasons: list[str] = Field(default_factory=list)


class CellLimits(BaseModel):
    max_depth: int = Field(default=2, ge=0, le=8)
    max_cells: int = Field(default=8, ge=1, le=128)
    max_leaf_attempts: int = Field(default=6, ge=1, le=128)
    max_children_per_cell: int = Field(default=4, ge=2, le=16)
    min_child_budget_usd: float = Field(default=0.002, ge=0.0)


Planner = Callable[[CellSpec, int, int, float], Awaitable[CellPlan]]
LeafRunner = Callable[[CellSpec, float], Awaitable[LeafOutcome]]


@dataclass
class _Counters:
    cells: int = 0
    leaves: int = 0


class HierarchicalCellRuntime:
    """Bounded recursive cognition with separate cell, leaf-attempt and dollar ceilings.

    A cell is an orchestration node, not a model rollout by definition. Only actual leaf executions
    consume `max_leaf_attempts`. Dollar envelopes are delegated through durable child escrows, so
    sibling cells cannot spend the same remaining budget.

    Completion semantics are deliberately fail-closed. A scheduled branch that never ran because a
    hard resource ceiling was reached makes the parent BLOCKED, not successful. Analytical failures
    may produce PARTIAL output, but PARTIAL never aliases COMPLETE for durable mission finalization.
    """

    def __init__(
        self,
        control: DistributedControlStore,
        *,
        limits: CellLimits | None = None,
    ) -> None:
        self.control = control
        self.limits = limits or CellLimits()
        self._counter_lock = asyncio.Lock()
        self._counters = _Counters()

    async def _reserve_cell(self) -> None:
        async with self._counter_lock:
            if self._counters.cells >= self.limits.max_cells:
                raise CellLimitExceeded("cell count ceiling exhausted")
            self._counters.cells += 1

    async def _reserve_leaf(self) -> None:
        async with self._counter_lock:
            if self._counters.leaves >= self.limits.max_leaf_attempts:
                raise CellLimitExceeded("leaf-attempt ceiling exhausted")
            self._counters.leaves += 1

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    async def execute(
        self,
        root: CellSpec,
        *,
        owner: str,
        budget_usd: float,
        planner: Planner,
        leaf_runner: LeafRunner,
    ) -> CellExecutionResult:
        self._counters = _Counters()
        root_escrow = self.control.create_root_escrow(
            scope=f"cell:{root.id}", owner=owner, allocated_usd=max(0.0, float(budget_usd))
        )
        try:
            result = await self._run_cell(
                root,
                owner=owner,
                depth=0,
                escrow_id=root_escrow.escrow_id,
                planner=planner,
                leaf_runner=leaf_runner,
            )
            self.control.settle_escrow(root_escrow.escrow_id)
        except Exception:
            # Leave an open escrow only when spend/reservations make safe cancellation impossible.
            # Its durable state then exposes the interrupted execution for reconciliation.
            current = self.control.escrow(root_escrow.escrow_id)
            if current.spent_usd == 0 and current.reserved_usd == 0:
                self.control.cancel_escrow(root_escrow.escrow_id)
            raise
        final = self.control.escrow(root_escrow.escrow_id)
        result.cost_usd = final.spent_usd
        result.cells_opened = self._counters.cells
        result.leaf_attempts = self._counters.leaves
        return result

    async def _run_cell(
        self,
        spec: CellSpec,
        *,
        owner: str,
        depth: int,
        escrow_id: str,
        planner: Planner,
        leaf_runner: LeafRunner,
    ) -> CellExecutionResult:
        await self._reserve_cell()
        escrow = self.control.escrow(escrow_id)

        may_decompose = (
            spec.decomposable
            and depth < self.limits.max_depth
            and self._counters.cells < self.limits.max_cells
            and escrow.available_usd >= 2 * self.limits.min_child_budget_usd
        )
        if may_decompose:
            plan = await planner(
                spec,
                depth,
                self.limits.max_children_per_cell,
                escrow.available_usd,
            )
            if plan.planner_cost_usd:
                self.control.charge(escrow_id, plan.planner_cost_usd)
            children = plan.children[: self.limits.max_children_per_cell]
            # One child adds no structural value; execute this cell directly instead.
            if len(children) >= 2:
                current = self.control.escrow(escrow_id)
                min_total = len(children) * self.limits.min_child_budget_usd
                if current.available_usd >= min_total:
                    return await self._run_children(
                        spec,
                        children,
                        owner=owner,
                        depth=depth,
                        escrow_id=escrow_id,
                        planner=planner,
                        leaf_runner=leaf_runner,
                        rationale=plan.rationale,
                    )

        await self._reserve_leaf()
        allowance = self.control.escrow(escrow_id).available_usd
        outcome = await leaf_runner(spec, allowance)
        if outcome.cost_usd > allowance + 1e-12:
            raise BudgetExceeded(
                f"leaf {spec.id} reported ${outcome.cost_usd:.6f} above its "
                f"${allowance:.6f} escrow allowance"
            )
        if outcome.cost_usd:
            self.control.charge(escrow_id, outcome.cost_usd)
        status = CellStatus.COMPLETE if outcome.success else CellStatus.FAILED
        return CellExecutionResult(
            cell_id=spec.id,
            success=status == CellStatus.COMPLETE,
            status=status,
            answer=outcome.answer,
            cost_usd=outcome.cost_usd,
            confidence=outcome.confidence,
            evidence_refs=self._dedupe(outcome.evidence_refs),
            reason="leaf execution",
        )

    async def _run_children(
        self,
        parent: CellSpec,
        children: list[CellSpec],
        *,
        owner: str,
        depth: int,
        escrow_id: str,
        planner: Planner,
        leaf_runner: LeafRunner,
        rationale: str,
    ) -> CellExecutionResult:
        current = self.control.escrow(escrow_id)
        weights = [max(1e-9, child.budget_weight) for child in children]
        total_weight = sum(weights)
        child_escrows: list[tuple[CellSpec, str]] = []

        # Allocate sequentially inside SQLite transactions. Rounding residue remains with the parent.
        for child, weight in zip(children, weights):
            share = current.available_usd * (weight / total_weight)
            share = max(self.limits.min_child_budget_usd, share)
            available = self.control.escrow(escrow_id).available_usd
            share = min(share, available)
            if share + 1e-12 < self.limits.min_child_budget_usd:
                break
            child_escrow = self.control.allocate_child_escrow(
                escrow_id,
                scope=f"cell:{child.id}",
                owner=owner,
                allocated_usd=share,
            )
            child_escrows.append((child, child_escrow.escrow_id))

        if len(child_escrows) < 2:
            for _child, child_escrow_id in child_escrows:
                self.control.cancel_escrow(child_escrow_id)
            await self._reserve_leaf()
            allowance = self.control.escrow(escrow_id).available_usd
            outcome = await leaf_runner(parent, allowance)
            if outcome.cost_usd > allowance + 1e-12:
                raise BudgetExceeded("fallback leaf exceeded parent escrow")
            if outcome.cost_usd:
                self.control.charge(escrow_id, outcome.cost_usd)
            status = CellStatus.COMPLETE if outcome.success else CellStatus.FAILED
            return CellExecutionResult(
                cell_id=parent.id,
                success=status == CellStatus.COMPLETE,
                status=status,
                answer=outcome.answer,
                confidence=outcome.confidence,
                evidence_refs=self._dedupe(outcome.evidence_refs),
                reason="insufficient useful child budget; executed parent leaf",
            )

        async def run_child(child: CellSpec, child_escrow_id: str) -> CellExecutionResult:
            try:
                result = await self._run_cell(
                    child,
                    owner=owner,
                    depth=depth + 1,
                    escrow_id=child_escrow_id,
                    planner=planner,
                    leaf_runner=leaf_runner,
                )
                self.control.settle_escrow(child_escrow_id)
                return result
            except Exception:
                state = self.control.escrow(child_escrow_id)
                if state.spent_usd == 0 and state.reserved_usd == 0:
                    self.control.cancel_escrow(child_escrow_id)
                raise

        results = await asyncio.gather(
            *(run_child(child, eid) for child, eid in child_escrows),
            return_exceptions=True,
        )
        child_results: list[CellExecutionResult] = []
        failures: list[str] = []
        blocked_reasons: list[str] = []
        for (child, _eid), result in zip(child_escrows, results):
            if isinstance(result, Exception):
                failure = f"{child.id}: {type(result).__name__}: {result}"
                failures.append(failure)
                if isinstance(result, (CellLimitExceeded, BudgetExceeded)):
                    blocked_reasons.append(failure)
            else:
                child_results.append(result)
                blocked_reasons.extend(result.blocked_reasons)

        evidence = self._dedupe(
            [ref for result in child_results for ref in result.evidence_refs]
        )
        complete_children = [
            result for result in child_results if result.status == CellStatus.COMPLETE
        ]
        child_blocked = any(
            result.status == CellStatus.BLOCKED for result in child_results
        )
        child_incomplete = any(
            result.status in {CellStatus.PARTIAL, CellStatus.FAILED}
            for result in child_results
        )

        # Resource/scheduler failures are not analytical disagreement; they mean promised work did not
        # happen and therefore block finalization. If all scheduled branches ran but some failed, the
        # result may be PARTIAL and useful to a parent replan, never silently COMPLETE.
        if blocked_reasons or child_blocked:
            status = CellStatus.BLOCKED
        elif len(complete_children) == len(child_escrows):
            status = CellStatus.COMPLETE
        elif complete_children or child_incomplete:
            status = CellStatus.PARTIAL
        else:
            status = CellStatus.FAILED

        confidence = min(
            (result.confidence for result in complete_children), default=0.0
        )
        answer_parts = [
            f"[{result.cell_id}] {result.answer}" for result in child_results
        ]
        if failures:
            answer_parts.append("FAILED CELLS: " + " | ".join(failures))
        return CellExecutionResult(
            cell_id=parent.id,
            success=status == CellStatus.COMPLETE,
            status=status,
            answer="\n\n".join(answer_parts),
            confidence=confidence,
            evidence_refs=evidence,
            children=child_results,
            reason=rationale or "hierarchical decomposition",
            blocked_reasons=self._dedupe(blocked_reasons),
        )
