"""Communication-channel adapters for the adaptive harness."""

from adaptive_harness.channels.base import (
    AgentChannelGateway,
    ApprovalDecision,
    ChannelPrincipal,
    InboundMessage,
    OutboundMessage,
)

__all__ = [
    "AgentChannelGateway",
    "ApprovalDecision",
    "ChannelPrincipal",
    "InboundMessage",
    "OutboundMessage",
]
