from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"v0.6 contract-fix anchor missing: {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# Preserve the v0.5 `passed=` API while accepting the v0.6 Policy Lab's `success=` name.
replace_once(
    "src/adaptive_harness/orchestration/policy_arena.py",
    '''        evidence_strength: float,\n        passed: bool,\n        source: Literal["benchmark", "live", "shadow", "matched_action"] = "benchmark",\n        latency_ms: float = 0.0,\n    ) -> None:\n        self.db.execute(\n''',
    '''        evidence_strength: float,\n        passed: bool | None = None,\n        success: bool | None = None,\n        source: Literal["benchmark", "live", "shadow", "matched_action"] = "benchmark",\n        latency_ms: float = 0.0,\n    ) -> None:\n        if passed is None and success is None:\n            raise TypeError("record_trial requires either passed= or success=")\n        if passed is not None and success is not None and bool(passed) != bool(success):\n            raise ValueError("passed and success disagree for the same policy trial")\n        canonical_passed = bool(success) if passed is None else bool(passed)\n        self.db.execute(\n''',
)
replace_once(
    "src/adaptive_harness/orchestration/policy_arena.py",
    '''                int(passed),\n                max(0.0, float(latency_ms)),\n''',
    '''                int(canonical_passed),\n                max(0.0, float(latency_ms)),\n''',
)

# Keep a single canonical DB column. The Policy Lab may call the metric "success" in reports,
# but it reads the persisted v0.5-compatible `passed` field.
replace_once(
    "src/adaptive_harness/orchestration/policy_lab.py",
    "                SELECT task_key, AVG(quality), AVG(cost_usd), AVG(success)\n",
    "                SELECT task_key, AVG(quality), AVG(cost_usd), AVG(passed)\n",
)

print("Applied v0.6 policy contract compatibility fix")
