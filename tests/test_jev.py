"""Deterministic transport/contract tests, not live JEV quality or latency evidence."""
import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from adaptive_harness.orchestration.jev import JevConfig, JevDecisionEngine, choice


@pytest.fixture(autouse=True)
def isolated_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unit-test-not-a-real-key")


def config(**values):
    return JevConfig(**{"mode": "active", "allow_remote_state": True, **values})


def response_for(body, chosen=None, tokens=100):
    answers = {}
    for name, question in body["questions"].items():
        options = question["criteria"]
        selected = (chosen or {}).get(name, next(k for k in options if k != "abstain"))
        probabilities = {k: 0.01 / (len(options) - 1) for k in options}
        probabilities[selected] = 0.99
        answers[name] = {"type": "choice", "choice": selected, "confidence": 0.95,
                         "probabilities": probabilities}
    return {"model": body["model"], "answers": answers,
            "usage": {"input_tokens": tokens, "output_tokens": 5}}


def engine(*, cfg=None, mutate=None, status=200, chosen=None, audit=None, handler=None):
    requests = []

    async def serve(request):
        requests.append(request)
        if handler:
            return await handler(request)
        body = json.loads(request.content)
        result = response_for(body, chosen)
        if mutate:
            mutate(result)
        return httpx.Response(status, json=result)

    return JevDecisionEngine(cfg or config(), transport=httpx.MockTransport(serve), audit=audit), requests


async def decide(e, **kwargs):
    return await e.decide(**{
        "state": {"task": "PRIVATE-TASK-SENT-ONLY-WITH-CONSENT"},
        "questions": {"route": choice("Select an allowed route", {"cheap": "simple", "primary": "hard"})},
        "defaults": {"route": "primary"}, "available_usd": 0.1, **kwargs,
    })


@pytest.mark.asyncio
async def test_wire_contract_pin_bearer_accounting_and_private_audit():
    events = []
    e, requests = engine(audit=events.append)
    result = await decide(e)
    assert result.reason == "accepted" and result.effective == {"route": "cheap"}
    assert result.cost_usd == pytest.approx(0.0000042)
    assert e.calls == 1 and e.reserved_usd == 0
    assert e.accounted_usd == result.cost_usd
    req = requests[0]
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone" and req.method == "POST"
    assert req.headers["authorization"] == "Bearer unit-test-not-a-real-key"
    body = json.loads(req.content)
    assert body["model"] == "jev-1.13.0"
    assert "abstain" in body["questions"]["route"]["criteria"]
    audit = json.dumps(events)
    assert "PRIVATE-TASK" not in audit and "unit-test-not-a-real-key" not in audit
    assert len(result.fingerprint) == 64 and result.latency_ms >= 0
    assert result.confidences["route"] == 0.95
    assert result.probabilities["route"]["cheap"] == 0.99


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"mode": "off"}, "disabled"),
    ({"allow_remote_state": False}, "remote_state_not_allowed"),
    ({"max_request_bytes": 512}, "state_too_large"),
])
async def test_configuration_can_prevent_all_network(changes, reason):
    e, requests = engine(cfg=config(**changes))
    result = await decide(e, state={"task": "X" * 1000})
    assert result.reason == reason and result.cost_usd == 0
    assert not requests and e.calls == 0


@pytest.mark.asyncio
async def test_missing_key_singleton_and_insufficient_budget_are_free(monkeypatch):
    e, requests = engine()
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert (await decide(e)).reason == "missing_or_invalid_key"
    assert (await decide(e, available_usd=0.002)).reason == "local_budget"
    single = await decide(e, questions={"route": choice("only", {"primary": "required"})})
    assert single.reason == "single_option"
    assert not requests


@pytest.mark.asyncio
async def test_shadow_observes_but_keeps_default():
    e, _ = engine(cfg=config(mode="shadow"))
    result = await decide(e)
    assert result.proposed == {"route": "cheap"}
    assert result.effective == {"route": "primary"} and result.reason == "shadow"
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_one_request_batches_variable_questions_and_retains_singletons():
    e, requests = engine()
    result = await decide(e, questions={
        "route": choice("role", {"cheap": "simple", "primary": "hard"}),
        "method": choice("method", {"tests": "tests", "invariant": "invariants"}),
        "permission": choice("fixed", {"unchanged": "not delegable"}),
    }, defaults={"route": "primary", "method": "invariant", "permission": "unchanged"})
    assert len(requests) == 1
    assert set(json.loads(requests[0].content)["questions"]) == {"route", "method"}
    assert result.effective["permission"] == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["confidence", "probability", "margin", "abstain"])
async def test_all_three_uncertainty_gates_and_abstention(kind):
    def mutate(raw):
        a = raw["answers"]["route"]
        if kind == "confidence":
            a["confidence"] = 0.1
        elif kind in ("probability", "margin"):
            a["probabilities"] = {"cheap": 0.6, "primary": 0.39, "abstain": 0.01}
        else:
            a["choice"] = "abstain"
            a["probabilities"] = {"cheap": 0.005, "primary": 0.005, "abstain": 0.99}
    cfg = config(min_probability=0.5) if kind == "margin" else config()
    e, _ = engine(mutate=mutate, cfg=cfg)
    result = await decide(e)
    assert result.reason == "uncertain" and result.effective == {"route": "primary"}
    assert result.cost_usd == pytest.approx(0.0000042)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [
    "model", "question", "type", "choice", "keys", "sum", "nan", "infinity", "boolean",
    "argmax", "missing_usage", "boolean_tokens", "negative_tokens", "confidence_nan",
])
async def test_malformed_responses_never_affect_default_and_keep_reservation(kind):
    def mutate(raw):
        a = raw["answers"]["route"]
        if kind == "model": raw["model"] = "jev-latest"
        elif kind == "question": raw["answers"]["extra"] = a
        elif kind == "type": a["type"] = "text"
        elif kind == "choice": a["choice"] = "execute_shell"
        elif kind == "keys": a["probabilities"]["execute_shell"] = 0
        elif kind == "sum": a["probabilities"]["cheap"] = 0.8
        elif kind == "nan": a["probabilities"]["cheap"] = float("nan")
        elif kind == "infinity": a["probabilities"]["cheap"] = float("inf")
        elif kind == "boolean": a["probabilities"]["cheap"] = True
        elif kind == "argmax": a["choice"] = "primary"
        elif kind == "missing_usage": del raw["usage"]
        elif kind == "boolean_tokens": raw["usage"]["input_tokens"] = True
        elif kind == "negative_tokens": raw["usage"]["input_tokens"] = -1
        elif kind == "confidence_nan": a["confidence"] = float("nan")
    async def serve(request):
        raw = response_for(json.loads(request.content))
        mutate(raw)
        return httpx.Response(200, content=json.dumps(raw).encode())
    e, _ = engine(handler=serve)
    result = await decide(e)
    assert result.reason == "provider_error" and result.effective == {"route": "primary"}
    assert result.cost_usd == e.config.reserve_per_call_usd and e.reserved_usd == 0
    assert "execute_shell" not in json.dumps(list(e.events))


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b'{"model":"one","model":"two"}', b'\xff', b'X' * 2048])
async def test_duplicate_keys_utf8_and_response_bound(content):
    async def serve(_): return httpx.Response(200, content=content)
    e, _ = engine(cfg=config(max_response_bytes=512), handler=serve)
    result = await decide(e)
    assert result.reason == "provider_error"
    assert result.cost_usd == e.config.reserve_per_call_usd


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 422, 429, 529])
async def test_errors_never_retry_and_auth_opens_circuit(status):
    e, requests = engine(status=status)
    result = await decide(e)
    assert result.reason == f"http_{status}" and len(requests) == 1
    assert result.cost_usd == e.config.reserve_per_call_usd
    if status in (401, 403):
        assert e.circuit_open
        assert (await decide(e)).reason == "circuit_open"
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_total_timeout_and_consecutive_failures_trip_circuit():
    async def serve(_):
        await asyncio.sleep(1)
        return httpx.Response(200)
    e, requests = engine(cfg=config(timeout_seconds=0.01, failure_threshold=2), handler=serve)
    assert (await decide(e)).reason == "timeout"
    assert (await decide(e)).reason == "timeout"
    assert (await decide(e)).reason == "circuit_open"
    assert len(requests) == 2 and e.accounted_usd == pytest.approx(0.006)


@pytest.mark.asyncio
async def test_atomic_concurrent_budget_and_call_admission():
    started, release = asyncio.Event(), asyncio.Event()
    async def serve(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=response_for(json.loads(request.content)))
    e, requests = engine(cfg=config(max_total_cost_usd=0.003, max_calls=1), handler=serve)
    first = asyncio.create_task(decide(e))
    await started.wait()
    blocked = await decide(e)
    assert blocked.reason == "call_limit"
    assert e.reserved_usd == 0.003
    release.set()
    await first
    assert len(requests) == 1 and e.reserved_usd == 0


@pytest.mark.asyncio
async def test_budget_gate_is_independent_of_call_ceiling():
    e, requests = engine(cfg=config(max_total_cost_usd=0.003), status=529)
    await decide(e)
    assert (await decide(e)).reason == "decision_budget" and len(requests) == 1


@pytest.mark.asyncio
async def test_cancelled_inflight_request_keeps_charge_and_releases_reservation():
    started = asyncio.Event()
    async def serve(_):
        started.set()
        await asyncio.sleep(10)
    e, _ = engine(handler=serve)
    task = asyncio.create_task(decide(e))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert e.reserved_usd == 0 and e.accounted_usd == e.config.reserve_per_call_usd
    assert e.events[-1]["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_usage_overrun_is_accounted_not_hidden_and_circuit_opens():
    e, _ = engine(mutate=lambda raw: raw["usage"].update(input_tokens=1_000_000))
    result = await decide(e)
    assert result.reason == "cost_overrun" and result.effective == {"route": "primary"}
    assert result.cost_usd == 0.042 and e.accounted_usd == 0.042 and e.circuit_open


@pytest.mark.asyncio
async def test_audit_failure_prevents_use_of_prediction():
    def broken(_): raise OSError("disk unavailable")
    e, _ = engine(audit=broken)
    result = await decide(e)
    assert result.reason == "audit_failed" and result.effective == {"route": "primary"}
    assert e.accounted_usd > 0


@pytest.mark.parametrize("values", [
    {"model": "jev-latest"}, {"mode": "enabled"}, {"timeout_seconds": 0},
    {"input_usd_per_million": float("nan")}, {"endpoint": "http://localhost"},
    {"min_probability": 1.1},
])
def test_configuration_rejects_aliases_invalid_numbers_and_custom_endpoint(values):
    with pytest.raises(ValidationError): config(**values)
