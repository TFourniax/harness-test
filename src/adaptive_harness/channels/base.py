from __future__ import annotations

from dataclasses import dataclass

from adaptive_harness.contracts import Goal, RunStatus
from adaptive_harness.runtime.agent import AgentRuntime, RunResult
from adaptive_harness.runtime.trace_store import TraceStore


@dataclass(frozen=True)
class ChannelPrincipal:
    """Stable human/conversation identity supplied by an untrusted transport."""

    channel: str
    conversation_id: str
    user_id: str

    @property
    def session_id(self) -> str:
        # Delimit each component so one channel can never collide with another.
        return f"channel:{self.channel}:{self.conversation_id}:{self.user_id}"


@dataclass(frozen=True)
class InboundMessage:
    principal: ChannelPrincipal
    text: str


@dataclass(frozen=True)
class ApprovalDecision:
    principal: ChannelPrincipal
    approval_id: str
    approved: bool


@dataclass(frozen=True)
class OutboundMessage:
    conversation_id: str
    text: str
    approval_id: str | None = None


class AgentChannelGateway:
    """Transport-neutral bridge between a human channel and AgentRuntime.

    Transport adapters are deliberately not allowed to bypass runtime approval,
    evidence, or capability policy. They can only submit a goal or resolve an
    already-created exact approval request.
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        traces: TraceStore,
        max_steps: int = 40,
        max_cost_usd: float | None = None,
        profile: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.traces = traces
        self.max_steps = max_steps
        self.max_cost_usd = max_cost_usd
        self.profile = profile

    async def handle_message(self, message: InboundMessage) -> list[OutboundMessage]:
        text = message.text.strip()
        if not text:
            return []
        result = await self.runtime.run(
            Goal(
                text=text,
                session_id=message.principal.session_id,
                profile=self.profile,
                max_steps=self.max_steps,
                max_cost_usd=self.max_cost_usd,
            )
        )
        return self._render_result(message.principal, result)

    async def handle_approval(self, decision: ApprovalDecision) -> list[OutboundMessage]:
        approval = self.traces.get_approval(decision.approval_id)
        if approval is None:
            return [
                OutboundMessage(
                    decision.principal.conversation_id,
                    "Cette demande d’approbation est inconnue ou n’existe plus.",
                )
            ]

        # Channel approvals are scoped to the exact session that created them.
        # An authorized user must not be able to approve another user's run by
        # obtaining/guessing its approval UUID.
        checkpoint = self.traces.load_checkpoint(approval.run_id)
        if not checkpoint:
            return [
                OutboundMessage(
                    decision.principal.conversation_id,
                    "Le run associé n’a plus de checkpoint reprenable.",
                )
            ]
        goal = Goal.model_validate(checkpoint["goal"])
        if goal.session_id != decision.principal.session_id:
            return [
                OutboundMessage(
                    decision.principal.conversation_id,
                    "Refus : cette approbation appartient à une autre session.",
                )
            ]

        if approval.status != "pending":
            return [
                OutboundMessage(
                    decision.principal.conversation_id,
                    f"Cette action a déjà été traitée ({approval.status}).",
                )
            ]

        self.traces.set_approval(
            approval.id, "approved" if decision.approved else "rejected"
        )
        result = await self.runtime.resume(approval.run_id, approval.id)
        return self._render_result(decision.principal, result)

    def _render_result(
        self, principal: ChannelPrincipal, result: RunResult
    ) -> list[OutboundMessage]:
        if result.status == RunStatus.WAITING_APPROVAL and result.approval_id:
            approval = self.traces.get_approval(result.approval_id)
            if approval is None:
                return [
                    OutboundMessage(
                        principal.conversation_id,
                        "Le run attend une approbation, mais la demande est introuvable.",
                    )
                ]
            payload = approval.payload
            text = (
                "⚠️ Action sensible à valider\n\n"
                f"{approval.summary}\n\n"
                f"Type : {approval.action_type}\n"
                f"Empreinte : {approval.fingerprint}\n"
                f"Payload exact : {payload}"
            )
            return [
                OutboundMessage(
                    principal.conversation_id,
                    text,
                    approval_id=approval.id,
                )
            ]

        if result.answer:
            return [OutboundMessage(principal.conversation_id, result.answer)]

        return [
            OutboundMessage(
                principal.conversation_id,
                f"Run terminé avec le statut : {result.status.value}",
            )
        ]
