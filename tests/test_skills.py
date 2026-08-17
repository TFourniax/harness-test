from pathlib import Path

from adaptive_harness.skills import SkillRegistry


def test_skill_retrieval(tmp_path: Path):
    s = tmp_path / "research"
    s.mkdir()
    (s / "SKILL.md").write_text("# Research\nverify sources", encoding="utf-8")
    (s / "metadata.yaml").write_text(
        "name: research\ndescription: verify web sources\ntags: [web, evidence]\n",
        encoding="utf-8",
    )
    reg = SkillRegistry(tmp_path)
    assert reg.retrieve("web evidence research")[0].name == "research"
