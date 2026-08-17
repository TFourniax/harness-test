from pathlib import Path


def patch(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"v0.8 integration anchor missing in {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


patch(
    "src/adaptive_harness/orchestration/hierarchical_service.py",
    "        self.child_runner = child_runner\n        self.cells = cells\n",
    "        self.child_runner = child_runner\n        self.cells = cells\n        # Optional v0.8 hook. None preserves the exact v0.7 single-leaf behavior.\n        self.panel_leaf_runner = None\n",
)
patch(
    "src/adaptive_harness/orchestration/hierarchical_service.py",
    "            planner=self.planner,\n            leaf_runner=self.leaf_runner,\n        )\n",
    "            planner=self.planner,\n            leaf_runner=self.leaf_runner,\n            panel_leaf_runner=self.panel_leaf_runner,\n        )\n",
)

patch(
    "src/adaptive_harness/orchestration/mission_dispatcher.py",
    "        self.lease_ttl_seconds = max(5, int(lease_ttl_seconds))\n        self.context_budget_tokens = max(64, int(context_budget_tokens))\n",
    "        self.lease_ttl_seconds = max(5, int(lease_ttl_seconds))\n        self.context_budget_tokens = max(64, int(context_budget_tokens))\n        # Optional v0.8 hook. Durable state semantics stay unchanged when no panel is injected.\n        self.panel_leaf_runner = None\n",
)
patch(
    "src/adaptive_harness/orchestration/mission_dispatcher.py",
    "                planner=planner,\n                leaf_runner=leaf_runner,\n            )\n",
    "                planner=planner,\n                leaf_runner=leaf_runner,\n                panel_leaf_runner=self.panel_leaf_runner,\n            )\n",
)

# The marginal-compute policy becomes part of the protected trust boundary before it is enabled.
security = Path("src/adaptive_harness/security.py")
text = security.read_text(encoding="utf-8")
entries = [
    '    "src/adaptive_harness/v08_cli.py",\n',
    '    "src/adaptive_harness/v08_config.py",\n',
    '    "src/adaptive_harness/orchestration/diversity_market.py",\n',
    '    "src/adaptive_harness/orchestration/panel_service.py",\n',
]
anchor = '    "src/adaptive_harness/orchestration/hierarchical_service.py",\n'
if anchor not in text:
    raise SystemExit("v0.8 trust-kernel anchor missing")
for entry in entries:
    if entry not in text:
        text = text.replace(anchor, anchor + entry, 1)
security.write_text(text, encoding="utf-8")

print("Applied minimal v0.8 panel integration")
