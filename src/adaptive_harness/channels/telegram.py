from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from adaptive_harness.channels.base import (
    AgentChannelGateway,
    ApprovalDecision,
    ChannelPrincipal,
    InboundMessage,
    OutboundMessage,
)
from adaptive_harness.runtime.trace_store import TraceStore


TELEGRAM_TEXT_LIMIT = 4096
CALLBACK_PREFIX_APPROVE = "ah:a:"
CALLBACK_PREFIX_REJECT = "ah:r:"


class TelegramAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramAccessPolicy:
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int] = frozenset()
    allow_group_chats: bool = False

    def authorized(self, *, user_id: int, chat_id: int, chat_type: str) -> bool:
        if not self.allowed_user_ids or user_id not in self.allowed_user_ids:
            return False
        if chat_type != "private" and not self.allow_group_chats:
            return False
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return False
        return True


class TelegramBotClient:
    """Small Bot API client using the harness' existing httpx dependency."""

    def __init__(self, token: str, *, poll_timeout_seconds: int = 30) -> None:
        if not token.strip():
            raise ValueError("Telegram bot token is empty")
        self.poll_timeout_seconds = poll_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(float(poll_timeout_seconds) + 15.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(f"/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramAPIError(f"Telegram {method} transport failure") from None
        if not body.get("ok"):
            code = body.get("error_code", "unknown")
            description = str(body.get("description", "Telegram API rejected request"))
            raise TelegramAPIError(f"Telegram {method} failed ({code}): {description}")
        return body.get("result")

    async def get_updates(self, *, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        approval_id: str | None = None,
    ) -> None:
        chunks = chunk_telegram_text(text)
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if approval_id and index == len(chunks) - 1:
                payload["reply_markup"] = approval_keyboard(approval_id)
            await self._call("sendMessage", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        await self._call("answerCallbackQuery", payload)


def chunk_telegram_text(text: str, limit: int = 3900) -> list[str]:
    if limit <= 0 or limit > TELEGRAM_TEXT_LIMIT:
        raise ValueError("invalid Telegram chunk limit")
    text = text or "(réponse vide)"
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    chunks.append(remaining)
    return chunks


def approval_keyboard(approval_id: str) -> dict[str, Any]:
    approve = CALLBACK_PREFIX_APPROVE + approval_id
    reject = CALLBACK_PREFIX_REJECT + approval_id
    if len(approve.encode("utf-8")) > 64 or len(reject.encode("utf-8")) > 64:
        raise ValueError("approval callback exceeds Telegram callback_data limit")
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approuver", "callback_data": approve},
                {"text": "❌ Refuser", "callback_data": reject},
            ]
        ]
    }


def principal_from_message(update_message: dict[str, Any]) -> tuple[ChannelPrincipal, str] | None:
    sender = update_message.get("from") or {}
    chat = update_message.get("chat") or {}
    if sender.get("is_bot"):
        return None
    text = update_message.get("text")
    if not isinstance(text, str):
        return None
    if "id" not in sender or "id" not in chat:
        return None
    principal = ChannelPrincipal(
        channel="telegram",
        conversation_id=str(chat["id"]),
        user_id=str(sender["id"]),
    )
    return principal, text


class TelegramChannelRunner:
    def __init__(
        self,
        *,
        client: TelegramBotClient,
        gateway: AgentChannelGateway,
        traces: TraceStore,
        access: TelegramAccessPolicy,
    ) -> None:
        self.client = client
        self.gateway = gateway
        self.traces = traces
        self.access = access

    async def run_forever(self) -> None:
        offset = self.traces.get_channel_cursor("telegram")
        try:
            while True:
                updates = await self.client.get_updates(offset=offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int):
                        continue
                    try:
                        await self.handle_update(update)
                    except Exception:
                        raise
                    offset = update_id + 1
                    self.traces.set_channel_cursor("telegram", offset)
        finally:
            await self.client.close()

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        parsed = principal_from_message(message)
        if parsed is None:
            return
        principal, text = parsed
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = int(sender["id"])
        chat_id = int(chat["id"])
        chat_type = str(chat.get("type", "private"))
        if not self.access.authorized(user_id=user_id, chat_id=chat_id, chat_type=chat_type):
            return

        if text.strip().lower() in {"/start", "/help"}:
            await self.client.send_message(
                chat_id,
                "Adaptive Agent Harness\n\n"
                "Envoie-moi directement une tâche. Les actions sensibles seront "
                "suspendues et présentées avec leur empreinte exacte avant exécution.",
            )
            return

        responses = await self.gateway.handle_message(InboundMessage(principal, text))
        await self._send_responses(chat_id, responses)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        data = callback.get("data")
        sender = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if not isinstance(callback_id, str) or not isinstance(data, str):
            return
        if "id" not in sender or "id" not in chat:
            await self.client.answer_callback_query(callback_id, "Action non disponible")
            return
        user_id = int(sender["id"])
        chat_id = int(chat["id"])
        chat_type = str(chat.get("type", "private"))
        if not self.access.authorized(user_id=user_id, chat_id=chat_id, chat_type=chat_type):
            await self.client.answer_callback_query(callback_id, "Non autorisé")
            return

        approved: bool | None = None
        approval_id = ""
        if data.startswith(CALLBACK_PREFIX_APPROVE):
            approved = True
            approval_id = data[len(CALLBACK_PREFIX_APPROVE) :]
        elif data.startswith(CALLBACK_PREFIX_REJECT):
            approved = False
            approval_id = data[len(CALLBACK_PREFIX_REJECT) :]
        if approved is None or not approval_id:
            await self.client.answer_callback_query(callback_id, "Bouton inconnu")
            return

        principal = ChannelPrincipal(
            channel="telegram",
            conversation_id=str(chat_id),
            user_id=str(user_id),
        )
        await self.client.answer_callback_query(
            callback_id, "Validation reçue" if approved else "Refus reçu"
        )
        responses = await self.gateway.handle_approval(
            ApprovalDecision(principal, approval_id, approved)
        )
        await self._send_responses(chat_id, responses)

    async def _send_responses(self, chat_id: int, responses: list[OutboundMessage]) -> None:
        for response in responses:
            await self.client.send_message(
                chat_id,
                response.text,
                approval_id=response.approval_id,
            )


async def run_telegram_channel(
    *,
    token: str,
    gateway: AgentChannelGateway,
    traces: TraceStore,
    access: TelegramAccessPolicy,
    poll_timeout_seconds: int = 30,
) -> None:
    client = TelegramBotClient(token, poll_timeout_seconds=poll_timeout_seconds)
    runner = TelegramChannelRunner(
        client=client,
        gateway=gateway,
        traces=traces,
        access=access,
    )
    await runner.run_forever()
