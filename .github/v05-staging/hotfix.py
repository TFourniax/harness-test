from pathlib import Path

replacements = {
    "src/adaptive_harness/orchestration/compute_market.py": [
        (
            "            elapsed_days = max(0.0, (self._now() - then).total_seconds() / 86400.0)\n"
            "        except Exception:\n"
            "            return 1.0\n"
            "        return 0.5 ** (elapsed_days / self.evidence_half_life_days)\n",
            "            elapsed_seconds = max(0.0, (self._now() - then).total_seconds())\n"
            "        except Exception:\n"
            "            return 1.0\n"
            "        # Sub-minute decay is economically meaningless at a day-scale half-life and only\n"
            "        # introduces floating-point drift into immediately-read evidence aggregates.\n"
            "        if elapsed_seconds < 60.0:\n"
            "            return 1.0\n"
            "        elapsed_days = elapsed_seconds / 86400.0\n"
            "        return 0.5 ** (elapsed_days / self.evidence_half_life_days)\n",
        )
    ],
    "src/adaptive_harness/orchestration/verification.py": [
        (
            "        text = f\"{task.task} {claim}\".lower()\n"
            "        terms = _KIND_TERMS.get(kind, ())\n"
            "        explicit = any(term in text for term in terms)\n"
            "        profile = (task.profile or \"generic\").lower()\n"
            "        if explicit:\n",
            "        task_text = task.task.lower()\n"
            "        terms = _KIND_TERMS.get(kind, ())\n"
            "        # A check description naturally names its own mechanism. Relevance must come\n"
            "        # from the assigned task (or a narrow profile prior), not from that self-description.\n"
            "        task_explicit = any(term in task_text for term in terms)\n"
            "        profile = (task.profile or \"generic\").lower()\n"
            "        if task_explicit:\n",
        )
    ],
    "tests/test_compute_market_v05.py": [
        (
            "    market = _market(tmp_path)\n"
            "    task = WorkItem(\n"
            "        id=\"critical-budget\",\n",
            "    market, _ = _market(tmp_path)\n"
            "    task = WorkItem(\n"
            "        id=\"critical-budget\",\n",
        )
    ],
    "tests/test_verification.py": [
        (
            "        metadata={\"verification_signal\": \"deterministic_fail\"},\n",
            "        metadata={\n"
            "            \"verification_signal\": \"deterministic_fail\",\n"
            "            \"verification_kind\": \"tests\",\n"
            "            \"verification_claim\": \"pytest suite passes\",\n"
            "        },\n",
        )
    ],
}

for filename, edits in replacements.items():
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        if old not in text:
            raise SystemExit(f"expected v0.5 hotfix anchor missing: {filename}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

print("Applied evidence-grounded v0.5 CI hotfixes")
