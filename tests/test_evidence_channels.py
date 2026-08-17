import pytest

from adaptive_harness.contracts import Observation, RunStatus, TrustLevel
from adaptive_harness.orchestration.diversity_market import (
    IndependenceScorer,
    PanelAttempt,
)
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.v07_config import HarnessConfig


def _attempt(ref: str, channel: str, *, answer: str, method: str):
    return PanelAttempt(
        method_id=method,
        model_role="cheap",
        model_id="cheap/test",
        answer=answer,
        evidence_refs=[ref],
        evidence_channels=[channel],
    )


def test_distinct_call_ids_same_canonical_channel_have_zero_evidence_novelty():
    scorer = IndependenceScorer()
    first = _attempt("call-a", "same-tool-args", answer="first", method="direct")
    second = _attempt(
        "call-b", "same-tool-args", answer="different prose", method="counterexample"
    )
    result = scorer.pair(second, first)
    assert first.evidence_refs != second.evidence_refs
    assert result.evidence_novelty == 0.0
    assert result.score < 0.5


def test_canonical_channels_override_call_id_fallback_when_available():
    scorer = IndependenceScorer()
    first = PanelAttempt(
        method_id="direct",
        model_role="cheap",
        answer="a",
        evidence_refs=["same-call-id-looking-token"],
        evidence_channels=["channel-a"],
    )
    second = PanelAttempt(
        method_id="alternate",
        model_role="primary",
        answer="b",
        evidence_refs=["same-call-id-looking-token"],
        evidence_channels=["channel-b"],
    )
    result = scorer.pair(second, first)
    assert result.evidence_novelty == 1.0


def _cfg() -> HarnessConfig:
    return HarnessConfig.model_validate(
        {
            "primary": {"model": "primary/test"},
            "cheap": {"model": "cheap/test"},
            "team": {"enabled": True},
        }
    )


@pytest.mark.asyncio
async def test_panel_extracts_provenance_fingerprint_not_ephemeral_call_id():
    observation = Observation(
        call_id="ephemeral-call-123",
        tool_name="http_get",
        ok=True,
        content="body",
        trust=TrustLevel.UNTRUSTED_EXTERNAL,
        metadata={"provenance_fingerprint": "canonical-source-channel"},
    )

    async def child_runner(goal):
        return RunResult(
            run_id="run-1",
            status=RunStatus.SUCCEEDED,
            answer="answer",
            observations=[observation],
            reported_cost_usd=0.0,
        )

    class NoopMarket:
        pass

    panel = IndependenceAwarePanelRunner(
        config=_cfg(),
        child_runner=child_runner,
        market=NoopMarket(),
        max_panel_attempts=1,
    )
    _result, attempt = await panel._run_one(
        __import__("adaptive_harness.orchestration.cell_runtime", fromlist=["CellSpec"]).CellSpec(
            id="x", task="read source", profile="research", decomposable=False
        ),
        slot_index=1,
        method_id="primary-source",
        method_instruction="read",
        role="cheap",
        remaining_budget_usd=0.01,
    )
    assert attempt.evidence_refs == ["ephemeral-call-123"]
    assert attempt.evidence_channels == ["canonical-source-channel"]
