from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from adaptive_harness.channels.base import (
    AgentChannelGateway,
    ApprovalDecision,
    ChannelPrincipal,
)
from adaptive_harness.channels.telegram import (
    TelegramAccessPolicy,
    approval_keyboard,
    chunk_telegram_text,
    principal_from_message,
)
from adaptive_harness.contracts import ApprovalRequest, Goal, RunStatus
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.trace_store import TraceStore


class ResumeOnlyRuntime:
    async def resume(self, run_id: str, approval_id: str) -> RunResult:
        return RunResult(run_id=run_id, status=RunStatus.SUCCEEDED, answer="done")


@pytest.fixture()
def traces(tmp_path: Path) -> TraceStore:
    return TraceStore(str(tmp_path / "traces.sqlite3"))


def test_telegram_access_is_default_deny_and_private_by_default():
    policy = TelegramAccessPolicy(allowed_user_ids=frozenset({42}))
    assert policy.authorized(user_id=42, chat_id=42, chat_type="private")
    assert not policy.authorized(user_id=43, chat_id=43, chat_type="private")
    assert not policy.authorized(user_id=42, chat_id=-1001, chat_type="supergroup")
    assert not TelegramAccessPolicy(frozenset()).authorized(
        user_id=42, chat_id=42, chat_type="private"
    )


def test_telegram_optional_chat_allowlist_is_additional_constraint():
    policy = TelegramAccessPolicy(
        allowed_user_ids=frozenset({42}),
        allowed_chat_ids=frozenset({99}),
        allow_group_chats=True,
    )
    assert policy.authorized(user_id=42, chat_id=99, chat_type="group")
    assert not policy.authorized(user_id=42, chat_id=100, chat_type="group")


def test_approval_callbacks_fit_telegram_limit():
    keyboard = approval_keyboard(str(uuid4()))
    for button in keyboard["inline_keyboard"][0]:
        assert len(button["callback_data"].encode("utf-8")) <= 64


def test_telegram_text_chunking_stays_under_api_limit():
    text = "x" * 9000
    chunks = chunk_telegram_text(text)
    assert "".join(chunks) == text
    assert len(chunks) == 3
    assert all(len(chunk) <= 3900 for chunk in chunks)


def test_message_principal_is_stable():
    parsed = principal_from_message(
        {
            "from": {"id": 7, "is_bot": False},
            "chat": {"id": 11, "type": "private"},
            "text": "hello",
        }
    )
    assert parsed is not None
    principal, text = parsed
    assert text == "hello"
    assert principal.session_id == "channel:telegram:11:7"


def test_channel_cursor_is_durable(traces: TraceStore):
    assert traces.get_channel_cursor("telegram") is None
    traces.set_channel_cursor("telegram", 123)
    assert traces.get_channel_cursor("telegram") == 123


@pytest.mark.asyncio
async def test_channel_approval_cannot_cross_sessions(traces: TraceStore):
    approval = ApprovalRequest(
        run_id="run-1",
        action_type="tool",
        summary="sensitive",
        payload={"x": 1},
        fingerprint="abc",
    )
    traces.save_approval(approval)
    traces.save_checkpoint(
        "run-1",
        {
            "goal": Goal(text="do it", session_id="channel:telegram:1:10").model_dump(),
            "observations": [],
            "notes": [],
            "pending_call": {"id": "call", "name": "x", "arguments": {}},
            "reported_cost_usd": 0.0,
        },
    )
    gateway = AgentChannelGateway(
        runtime=ResumeOnlyRuntime(),  # type: ignore[arg-type]
        traces=traces,
    )
    other = ChannelPrincipal("telegram", "2", "20")
    responses = await gateway.handle_approval(
        ApprovalDecision(other, approval.id, approved=True)
    )
    assert "autre session" in responses[0].text
    assert traces.get_approval(approval.id).status == "pending"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_channel_approval_resumes_own_session(traces: TraceStore):
    principal = ChannelPrincipal("telegram", "1", "10")
    approval = ApprovalRequest(
        run_id="run-1",
        action_type="tool",
        summary="sensitive",
        payload={"x": 1},
        fingerprint="abc",
    )
    traces.save_approval(approval)
    traces.save_checkpoint(
        "run-1",
        {
            "goal": Goal(text="do it", session_id=principal.session_id).model_dump(),
            "observations": [],
            "notes": [],
            "pending_call": {"id": "call", "name": "x", "arguments": {}},
            "reported_cost_usd": 0.0,
        },
    )
    gateway = AgentChannelGateway(
        runtime=ResumeOnlyRuntime(),  # type: ignore[arg-type]
        traces=traces,
    )
    responses = await gateway.handle_approval(
        ApprovalDecision(principal, approval.id, approved=True)
    )
    assert responses[0].text == "done"
    assert traces.get_approval(approval.id).status == "approved"  # type: ignore[union-attr]
