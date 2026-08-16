from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from adaptive_harness.contracts import EvalResult


class PatchEvaluator:
    """Evaluates a proposed unified diff in an isolated copy of the repository.

    The production recommendation is an ephemeral VM/container. This reference
    implementation deliberately never applies a proposal to the live checkout.
    """

    def __init__(self, workspace: str, commands: list[list[str]]) -> None:
        self.workspace = Path(workspace).resolve()
        self.commands = commands

    def evaluate(self, patch: str) -> list[EvalResult]:
        results: list[EvalResult] = []
        with tempfile.TemporaryDirectory(prefix="harness-candidate-") as tmp:
            candidate = Path(tmp) / "candidate"
            shutil.copytree(
                self.workspace,
                candidate,
                ignore=shutil.ignore_patterns(".git", ".harness", "__pycache__", ".venv"),
            )
            try:
                apply = subprocess.run(
                    ["git", "apply", "--whitespace=error", "-"],
                    input=patch,
                    text=True,
                    cwd=candidate,
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                results.append(
                    EvalResult(
                        name="patch_apply",
                        passed=False,
                        score=0.0,
                        details="patch application timed out",
                        deterministic=True,
                    )
                )
                return results
            results.append(
                EvalResult(
                    name="patch_apply",
                    passed=apply.returncode == 0,
                    score=1.0 if apply.returncode == 0 else 0.0,
                    details=(apply.stderr or apply.stdout)[-4000:],
                    deterministic=True,
                )
            )
            if apply.returncode != 0:
                return results

            # The candidate is never allowed to grade itself by rewriting the baseline
            # tests/eval specs in its isolated copy. Restore them from the champion.
            for frozen in ("tests", "evals"):
                src = self.workspace / frozen
                dst = candidate / frozen
                if src.exists():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)

            for cmd in self.commands:
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=candidate,
                        text=True,
                        capture_output=True,
                        timeout=300,
                    )
                except subprocess.TimeoutExpired as exc:
                    output = ((exc.stdout or "") + "\n" + (exc.stderr or ""))[-8000:]
                    results.append(
                        EvalResult(
                            name="command:" + " ".join(cmd),
                            passed=False,
                            score=0.0,
                            details=("evaluation timed out\n" + output).strip(),
                            deterministic=True,
                        )
                    )
                    continue
                results.append(
                    EvalResult(
                        name="command:" + " ".join(cmd),
                        passed=proc.returncode == 0,
                        score=1.0 if proc.returncode == 0 else 0.0,
                        details=(proc.stdout + "\n" + proc.stderr)[-8000:],
                        deterministic=True,
                    )
                )
        return results
