from adaptive_harness.improvement.engine import WeaknessMiner


def event(run_id, kind, payload=None):
    return {"run_id": run_id, "kind": kind, "payload": payload or {}}


def test_population_miner_requires_recurrence_across_runs():
    miner = WeaknessMiner()
    one_run = [event("r1", "run_failed", {"reason": "x"})]
    assert miner.mine_population(one_run) == []

    two_runs = [
        event("r1", "run_failed", {"reason": "x"}),
        event("r2", "run_failed", {"reason": "y"}),
    ]
    weaknesses = miner.mine_population(two_runs)
    assert weaknesses
    assert "2 runs" in weaknesses[0]


def test_population_tool_failures_need_distinct_runs():
    miner = WeaknessMiner()
    same = [
        event("r1", "tool_observation", {"ok": False}),
        event("r1", "tool_observation", {"ok": False}),
    ]
    assert miner.mine_population(same) == []
    spread = same + [event("r2", "tool_observation", {"ok": False})]
    assert any("Tool execution failures" in x for x in miner.mine_population(spread))
