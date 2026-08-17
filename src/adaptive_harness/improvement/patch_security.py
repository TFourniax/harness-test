from __future__ import annotations

from dataclasses import dataclass, field


from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION
FORBIDDEN_ADDITIONS = (
    "--privileged",
    "chmod 777",
    "network_mode: host",
    "--network=host",
    "--cap-add=ALL",
    "allowed_scopes = {'*'}",
    'allowed_scopes = {"*"}',
)


@dataclass(slots=True)
class PatchSecurityVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    touched_paths: set[str] = field(default_factory=set)


class PatchSecurityAnalyzer:
    """Non-model checks for candidate self-modifications.

    The strategy shell may evolve. The root-of-trust files and the candidate's
    exam cannot be rewritten through the same self-promotion mechanism.
    """

    @staticmethod
    def touched_paths(patch: str) -> set[str]:
        paths: set[str] = set()
        for line in patch.splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                raw = line[4:].split("\t", 1)[0].strip()
                if raw == "/dev/null":
                    continue
                if raw.startswith(("a/", "b/")):
                    raw = raw[2:]
                paths.add(raw)
        return paths

    @classmethod
    def analyze(cls, patch: str) -> PatchSecurityVerdict:
        reasons: list[str] = []
        paths = cls.touched_paths(patch)
        for path in sorted(paths):
            if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in IMMUTABLE_FROM_SELF_EVOLUTION):
                reasons.append(f"self-evolution cannot modify protected root-of-trust path: {path}")

        added_lines = "\n".join(
            line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        for token in FORBIDDEN_ADDITIONS:
            if token in added_lines:
                reasons.append(f"candidate adds forbidden privilege pattern: {token}")

        # Large broad changes are not intrinsically malicious, but the self-improver
        # is constrained to minimal hypotheses to keep evaluation causal.
        if len(paths) > 8:
            reasons.append(f"candidate touches too many files for one self-improvement hypothesis: {len(paths)}")

        return PatchSecurityVerdict(not reasons, reasons, paths)
