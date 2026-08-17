from __future__ import annotations

from adaptive_harness.contracts import EvidenceRequirement, Observation, ToolCall, ToolSpec
from adaptive_harness.runtime.policy import COMMITMENT_RISKS


class EvidenceGate:
    """ECLoop-inspired pre-commit gate.

    The gate is intentionally deterministic at execution time: a model/planner may
    create requirements, but only explicit evidence references mark them satisfied.
    """

    def __init__(self, requirements: list[EvidenceRequirement] | None = None) -> None:
        self.requirements = requirements or []

    def relevant_gaps(self, spec: ToolSpec, call: ToolCall) -> list[EvidenceRequirement]:
        if spec.risk not in COMMITMENT_RISKS:
            return []
        return [
            r
            for r in self.requirements
            if not r.satisfied and (not r.applies_to or call.name in r.applies_to)
        ]

    def update_from_observation(self, obs: Observation) -> None:
        """Conservative default: only explicit `satisfies` metadata closes evidence gaps."""
        for req_id in obs.metadata.get("satisfies", []):
            for req in self.requirements:
                if req.id == req_id:
                    req.satisfied = True
                    req.evidence_refs.append(obs.call_id)
