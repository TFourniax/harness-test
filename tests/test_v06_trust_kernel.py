from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION


def test_v06_state_and_context_control_plane_is_in_self_evolution_trust_boundary():
    protected = set(IMMUTABLE_FROM_SELF_EVOLUTION)
    required = {
        "src/adaptive_harness/v06_cli.py",
        "src/adaptive_harness/tools/state_tools.py",
        "src/adaptive_harness/orchestration/state_plane.py",
        "src/adaptive_harness/orchestration/context_budget.py",
        "src/adaptive_harness/orchestration/policy_lab.py",
        "src/adaptive_harness/runtime/trace_store.py",
    }
    assert required <= protected
