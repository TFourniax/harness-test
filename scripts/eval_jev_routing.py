"""Labelled routing-only evaluation, never executes agents/tools.

Default: validate the fixture offline. --live explicitly permits transmitting its state
and spending up to the local JEV admission budget. Provider overruns are reported, not hidden.
This is not an end-to-end harness quality/cost benchmark; use held-out real tasks for that.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path

from adaptive_harness.orchestration.jev import JevConfig, JevDecisionEngine, choice


def load_cases(path: Path) -> list[dict]:
    cases = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or case["id"] in seen or not isinstance(case.get("state"), dict)
                or not isinstance(case.get("options"), dict)
                or not 2 <= len(case["options"]) <= 32
                or "abstain" in case["options"]
                or case.get("expected") not in case["options"]
                or case.get("default") not in case["options"]):
            raise ValueError("Invalid or duplicate labelled case")
        seen.add(case["id"])
        cases.append(case)
    if not cases:
        raise ValueError("No cases provided")
    return cases


async def evaluate(cases: list[dict], budget: float) -> dict:
    engine = JevDecisionEngine(JevConfig(mode="shadow", allow_remote_state=True,
                                        max_total_cost_usd=budget))
    rows = []
    for case in cases:
        result = await engine.decide(
            state=case["state"], questions={"route": choice(case["instructions"], case["options"])},
            defaults={"route": case["default"]}, available_usd=budget,
        )
        selected = result.proposed.get("route")
        rows.append({"id": case["id"], "expected": case["expected"],
                     "proposed": selected, "confident": result.reason == "shadow",
                     "correct": selected == case["expected"], "reason": result.reason,
                     "accounted_usd": result.cost_usd, "token_cost_usd": result.token_cost_usd,
                     "latency_ms": result.latency_ms, "sent": result.request_sent})
    accepted = [r for r in rows if r["confident"]]
    latencies = [r["latency_ms"] for r in rows if r["sent"]]
    return {"mode": "live_routing_only", "model": engine.config.model,
            "cases": len(rows), "calls": engine.calls, "accounted_usd": engine.accounted_usd,
            "coverage": len(accepted) / len(rows),
            "accepted_accuracy": (sum(r["correct"] for r in accepted) / len(accepted)
                                  if accepted else None),
            "false_direct_leaf": sum(r["confident"] and r["proposed"] == "direct_leaf"
                                     and not r["correct"] for r in rows),
            "latency_median_ms": statistics.median(latencies) if latencies else None,
            "circuit_open": engine.circuit_open, "rows": rows,
            "warning": "Routing labels only; no worker quality, avoided-cost, or superiority proof."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="?", type=Path,
                        default=Path("evals/jev_routing_cases.jsonl"))
    parser.add_argument("--live", action="store_true",
                        help="Send case states to TypeSafe and use the key from TYPESAFE_API_KEY")
    parser.add_argument("--budget-usd", type=float, default=0.03)
    args = parser.parse_args()
    cases = load_cases(args.cases)
    result = (asyncio.run(evaluate(cases, args.budget_usd)) if args.live else
              {"mode": "offline_fixture_validation", "cases": len(cases), "calls": 0,
               "quality_measured": False, "latency_measured": False})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
