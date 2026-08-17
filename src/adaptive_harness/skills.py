from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    tags: list[str]


class SkillRegistry:
    """Versionable, file-backed procedural skills.

    Each skill is a folder containing SKILL.md and optional metadata.yaml/tests.
    Retrieval is deliberately transparent; replace scoring with embeddings for scale.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[Skill]:
        skills: list[Skill] = []
        for md in self.root.glob("*/SKILL.md"):
            meta_path = md.parent / "metadata.yaml"
            meta = yaml.safe_load(meta_path.read_text()) if meta_path.exists() else {}
            body = md.read_text(encoding="utf-8")
            skills.append(
                Skill(
                    name=meta.get("name", md.parent.name),
                    description=meta.get("description", body.splitlines()[0].lstrip("# ")),
                    body=body,
                    path=md.parent,
                    tags=list(meta.get("tags", [])),
                )
            )
        return skills

    def retrieve(self, query: str, limit: int = 5) -> list[Skill]:
        q = set(re.findall(r"[a-zA-Z0-9_-]+", query.lower()))
        scored = []
        for skill in self.load():
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()
            toks = set(re.findall(r"[a-zA-Z0-9_-]+", text))
            score = len(q & toks) / max(1, len(q))
            if score:
                scored.append((score, skill))
        return [s for _, s in sorted(scored, key=lambda x: x[0], reverse=True)[:limit]]
