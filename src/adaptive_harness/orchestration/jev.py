"""Opt-in, bounded System One decisions; never an authorization or evidence authority.

Wire contract checked against https://docs.typesafe.ai/api on 2026-09-18.
The HTTP endpoint is intentionally fixed. No model text, commands, or new capabilities
are accepted. Reported token costs are estimates at the configured tariff, not invoices.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
POLICY_VERSION = "jev-routing-v1"


class JevConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    mode: Literal["off", "shadow", "active"] = "off"
    allow_remote_state: bool = False
    planner_gate: bool = True
    panel_routing: bool = False
    model: str = Field(default="jev-1.13.0", pattern=r"^jev-\d+\.\d+\.\d+$")
    api_key_env: str = Field(default="TYPESAFE_API_KEY", pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: float = Field(default=1.5, gt=0, le=30)
    min_confidence: float = Field(default=0.80, ge=0, le=1)
    min_probability: float = Field(default=0.85, ge=0, le=1)
    min_margin: float = Field(default=0.25, ge=0, le=1)
    max_request_bytes: int = Field(default=16000, ge=512, le=64000)
    max_response_bytes: int = Field(default=65536, ge=512, le=262144)
    max_calls: int = Field(default=64, ge=1, le=10000)
    max_total_cost_usd: float = Field(default=0.03, gt=0, le=100)
    # Admission reservation; also charged when a sent request has unknown usage.
    # This is not a server-enforced billing cap. Overruns trip the circuit.
    reserve_per_call_usd: float = Field(default=0.003, gt=0, le=1)
    input_usd_per_million: float = Field(default=0.042, gt=0, le=1000)
    failure_threshold: int = Field(default=3, ge=1, le=20)


@dataclass
class JevDecision:
    effective: dict[str, str]
    reason: str
    proposed: dict[str, str] = field(default_factory=dict)
    mode: str = "off"
    model: str = ""
    fingerprint: str = ""
    confidences: dict[str, float] = field(default_factory=dict)
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    cost_usd: float = 0.0
    token_cost_usd: float | None = None
    input_tokens: int | None = None
    latency_ms: float = 0.0
    request_sent: bool = False
    policy_version: str = POLICY_VERSION


class JevDecisionEngine:
    """Shared process-lifetime call/spend admission, with failure-closed local fallback.

    No cache: two identical task texts need not describe the same external state.
    No retries: timeout/429/529 return to existing local orchestration immediately.
    Failed/cancelled calls keep their reservation when provider usage is unknown.
    """

    def __init__(
        self,
        config: JevConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        audit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.audit = audit
        self.events: deque[dict[str, Any]] = deque(maxlen=256)
        self.calls = 0
        self.accounted_usd = 0.0
        self.reserved_usd = 0.0
        self.failures = 0
        self.circuit_open = False
        self._lock = asyncio.Lock()

    def _record(self, result: JevDecision, defaults: dict[str, str]) -> JevDecision:
        event = asdict(result)
        # Only bounded local labels, fingerprint and metrics; never state/key/response text.
        if self.audit is not None:
            try:
                self.audit(event)
            except Exception:
                result.effective = dict(defaults)
                result.reason = "audit_failed"
                event = asdict(result)
        self.events.append(event)
        return result

    @staticmethod
    def _number(value: Any) -> float:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid probability")
        return float(value)

    def _answers(self, raw: Any, questions: dict) -> tuple[dict[str, str], bool]:
        if not isinstance(raw, dict) or raw.get("model") != self.config.model:
            raise ValueError("model mismatch")
        answers = raw.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("question mismatch")
        proposed: dict[str, str] = {}
        confident = True
        for key, question in questions.items():
            answer = answers[key]
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                raise ValueError("invalid answer type")
            probabilities = answer.get("probabilities")
            if not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"]):
                raise ValueError("candidate mismatch")
            values = {k: self._number(v) for k, v in probabilities.items()}
            if abs(sum(values.values()) - 1.0) > 1e-5:
                raise ValueError("probabilities must sum to one")
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in values:
                raise ValueError("invalid choice")
            ranked = sorted(values.values(), reverse=True)
            if values[choice] + 1e-9 < ranked[0]:
                raise ValueError("choice is not the maximum")
            confidence = self._number(answer.get("confidence"))
            confident = confident and (
                choice != "abstain"
                and confidence >= self.config.min_confidence
                and values[choice] >= self.config.min_probability
                and ranked[0] - ranked[1] >= self.config.min_margin
            )
            proposed[key] = choice
        return proposed, confident

    async def _post(self, body: bytes, key: str) -> dict:
        # Per-call client keeps CLI invocations/event-loop lifetimes independent and closes sockets.
        # End-to-end benchmarks must include connection setup, not only provider inference time.
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST", ENDPOINT, content=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.config.max_response_bytes:
                        raise ValueError("response too large")
        # NaN/Infinity, duplicate JSON fields, and invalid UTF-8 are rejected, not repaired.
        def no_constant(_value):
            raise ValueError("nonfinite JSON")

        def unique_object(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError("duplicate JSON key")
                result[k] = v
            return result

        return json.loads(data.decode("utf-8"), parse_constant=no_constant,
                          object_pairs_hook=unique_object)

    async def decide(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
        defaults: dict[str, str],
        available_usd: float,
    ) -> JevDecision:
        cfg = self.config
        defaults = dict(defaults)
        result = JevDecision(effective=dict(defaults), reason="disabled", mode=cfg.mode,
                             model=cfg.model)
        if cfg.mode == "off":
            return result
        if not cfg.allow_remote_state:
            result.reason = "remote_state_not_allowed"
            return self._record(result, defaults)
        if not math.isfinite(available_usd) or available_usd < cfg.reserve_per_call_usd:
            result.reason = "local_budget"
            return self._record(result, defaults)
        if set(questions) != set(defaults) or not 1 <= len(questions) <= 8:
            raise ValueError("invalid local decision contract")
        variable = {}
        for name, question in questions.items():
            choices = question.get("criteria", {})
            if (question.get("type") != "choice" or not 1 <= len(choices) <= 32
                    or defaults[name] not in choices or "abstain" in choices):
                raise ValueError("invalid local candidates")
            if len(choices) > 1:
                variable[name] = {**question, "criteria": {
                    **choices, "abstain": "Insufficient information; keep the local default."}}
        if not variable:
            result.reason = "single_option"
            return self._record(result, defaults)
        body = json.dumps({"model": cfg.model, "state": state, "questions": variable},
                          ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
        result.fingerprint = hashlib.sha256(POLICY_VERSION.encode() + body).hexdigest()
        if len(body) > cfg.max_request_bytes:
            result.reason = "state_too_large"
            return self._record(result, defaults)
        key = os.environ.get(cfg.api_key_env, "").strip()
        if not key or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            result.reason = "missing_or_invalid_key"
            return self._record(result, defaults)
        async with self._lock:
            if self.circuit_open:
                result.reason = "circuit_open"
            elif self.calls >= cfg.max_calls:
                result.reason = "call_limit"
            elif (self.accounted_usd + self.reserved_usd + cfg.reserve_per_call_usd
                  > cfg.max_total_cost_usd + 1e-12):
                result.reason = "decision_budget"
            else:
                self.calls += 1
                self.reserved_usd += cfg.reserve_per_call_usd
                result.request_sent = True
        if not result.request_sent:
            return self._record(result, defaults)

        started = time.perf_counter()
        failed = False
        open_circuit = False
        result.cost_usd = cfg.reserve_per_call_usd
        try:
            raw = await asyncio.wait_for(self._post(body, key), timeout=cfg.timeout_seconds)
            if not isinstance(raw, dict):
                raise ValueError("invalid response")
            usage = raw.get("usage")
            tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
            outputs = usage.get("output_tokens") if isinstance(usage, dict) else None
            if type(tokens) is not int or tokens < 0 or type(outputs) is not int or outputs < 0:
                raise ValueError("missing usage")
            result.input_tokens = tokens
            result.token_cost_usd = tokens * cfg.input_usd_per_million / 1_000_000
            if not math.isfinite(result.token_cost_usd):
                raise ValueError("invalid usage cost")
            # Never refund an uncertain request based on a malformed/mismatched response.
            proposed, confident = self._answers(raw, variable)
            result.cost_usd = result.token_cost_usd
            result.proposed = {**defaults, **proposed}
            result.confidences = {k: float(raw["answers"][k]["confidence"]) for k in variable}
            result.probabilities = {
                k: {option: float(value) for option, value in
                    raw["answers"][k]["probabilities"].items()} for k in variable
            }
            if result.cost_usd > cfg.reserve_per_call_usd + 1e-12:
                result.reason = "cost_overrun"
                failed = open_circuit = True
            elif not confident:
                result.reason = "uncertain"
            elif cfg.mode == "shadow":
                result.reason = "shadow"
            else:
                result.reason = "accepted"
                result.effective = dict(result.proposed)
        except asyncio.CancelledError:
            result.reason = "cancelled"
            failed = True
            raise
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, OverflowError) as exc:
            failed = True
            result.reason = "timeout" if isinstance(exc, TimeoutError) else "provider_error"
            if isinstance(exc, httpx.HTTPStatusError):
                result.reason = f"http_{exc.response.status_code}"
                open_circuit = exc.response.status_code in (401, 403)
            # Even an invalid answer can carry a larger valid usage estimate: do not undercount it.
            if result.token_cost_usd is not None:
                result.cost_usd = max(result.cost_usd, result.token_cost_usd)
                open_circuit = open_circuit or result.cost_usd > cfg.reserve_per_call_usd
        finally:
            result.latency_ms = (time.perf_counter() - started) * 1000
            async with self._lock:
                self.reserved_usd = max(0.0, self.reserved_usd - cfg.reserve_per_call_usd)
                self.accounted_usd += result.cost_usd
                self.failures = self.failures + 1 if failed else 0
                self.circuit_open = (self.circuit_open or open_circuit
                                     or self.failures >= cfg.failure_threshold)
            self._record(result, defaults)
        return result


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}
