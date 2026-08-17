from __future__ import annotations

import json
from typing import Any, Literal

from adaptive_harness.contracts import ModelTurn, ToolCall, ToolSpec
from adaptive_harness.providers.base import ModelProvider


class LiteLLMProvider(ModelProvider):
    """Provider-neutral gateway with a text-only tool compatibility protocol.

    Native function calling remains preferred. `tool_mode="text"` lets any sufficiently
    capable text-in/text-out model operate tools through a strict JSON envelope, which is
    useful for Replicate/OpenAI-compatible/local models that do not expose tool calls.
    """

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        out = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
        hidden = getattr(response, "_hidden_params", None) or {}
        cost = hidden.get("response_cost")
        if cost is not None:
            try:
                out["cost_usd"] = float(cost)
            except (TypeError, ValueError):
                pass
        return out

    @staticmethod
    def _text_protocol_messages(
        messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> list[dict[str, Any]]:
        catalog = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        protocol = (
            "You are using a text-only tool protocol. Return EXACTLY one JSON object and no "
            "markdown. To call a tool: "
            '{"type":"tool_call","name":"TOOL_NAME","arguments":{...}}. '
            "To finish: "
            '{"type":"final","content":"your answer"}. '
            "Never invent a tool name. Tool catalog:\n" + json.dumps(catalog, ensure_ascii=False)
        )
        return [{"role": "system", "content": protocol}, *messages]

    @staticmethod
    def _parse_text_tool_turn(content: str, usage: dict[str, Any], raw: Any) -> ModelTurn:
        try:
            payload = json.loads(content.strip())
        except json.JSONDecodeError:
            return ModelTurn(
                protocol_error=(
                    "text-only tool mode returned invalid JSON; the model must retry using "
                    "the strict tool envelope"
                ),
                usage=usage,
                raw=raw,
            )
        if payload.get("type") == "tool_call":
            name = payload.get("name")
            args = payload.get("arguments", {})
            if not isinstance(name, str) or not isinstance(args, dict):
                return ModelTurn(
                    protocol_error="malformed text tool_call envelope", usage=usage, raw=raw
                )
            return ModelTurn(tool_calls=[ToolCall(name=name, arguments=args)], usage=usage, raw=raw)
        if payload.get("type") == "final":
            return ModelTurn(content=str(payload.get("content", "")), usage=usage, raw=raw)
        return ModelTurn(
            protocol_error="unknown text tool envelope type", usage=usage, raw=raw
        )

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_mode: Literal["auto", "native", "text"] = "auto",
        fallbacks: list[str] | None = None,
        timeout: float = 90.0,
        num_retries: int = 2,
    ) -> ModelTurn:
        try:
            from litellm import acompletion
        except ImportError as exc:
            raise RuntimeError(
                "LiteLLM is not installed. Install the project dependencies with `pip install -e .` "
                "or `pip install -e '.[dev]'`."
            ) from exc

        selected = tool_mode
        if selected == "auto" and tools:
            # LiteLLM exposes capability introspection for many models. Unknown capability
            # defaults to native; operators can force text mode per role when necessary.
            try:
                from litellm import supports_function_calling

                selected = "native" if supports_function_calling(model=model) else "text"
            except Exception:
                selected = "native"

        if tools and selected == "text":
            response = await acompletion(
                model=model,
                messages=self._text_protocol_messages(messages, tools),
                temperature=temperature,
                max_tokens=max_tokens,
                fallbacks=fallbacks or None,
                timeout=timeout,
                num_retries=num_retries,
            )
            msg = response.choices[0].message
            return self._parse_text_tool_turn(
                getattr(msg, "content", None) or "", self._usage(response), response
            )

        tool_payload = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        response = await acompletion(
            model=model,
            messages=messages,
            tools=tool_payload or None,
            tool_choice="auto" if tool_payload else None,
            temperature=temperature,
            max_tokens=max_tokens,
            fallbacks=fallbacks or None,
            timeout=timeout,
            num_retries=num_retries,
        )
        msg = response.choices[0].message
        calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    return ModelTurn(
                        content=getattr(msg, "content", None),
                        usage=self._usage(response),
                        protocol_error=f"native tool call {tc.function.name!r} returned invalid JSON arguments",
                        raw=response,
                    )
            if not isinstance(args, dict):
                return ModelTurn(
                    content=getattr(msg, "content", None),
                    usage=self._usage(response),
                    protocol_error=f"native tool call {tc.function.name!r} returned non-object arguments",
                    raw=response,
                )
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args or {}))
        return ModelTurn(
            content=getattr(msg, "content", None),
            tool_calls=calls,
            usage=self._usage(response),
            raw=response,
        )
