"""The skills are one source of truth: .github/skills must mirror .claude/skills."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_github_skills_mirror_claude_skills():
    claude = {p.name: p for p in (ROOT / ".claude" / "skills").iterdir() if p.is_dir()}
    github = {p.name: p for p in (ROOT / ".github" / "skills").iterdir()}
    assert (
        set(claude) == set(github) == {"linkedin-setup", "linkedin-outreach", "linkedin-campaign"}
    )
    for name, src in claude.items():
        assert (src / "SKILL.md").read_text() == (github[name] / "SKILL.md").read_text(), name
        assert (src / "SKILL.md").read_text().startswith("---\nname: " + name), name


def test_copilot_instructions_point_at_the_skills():
    text = (ROOT / ".github" / "copilot-instructions.md").read_text()
    for name in ("linkedin-setup", "linkedin-outreach", "linkedin-campaign"):
        assert name in text
    assert "Never reply to a prospect" in text


def test_portable_skill_points_at_the_repository_and_the_inner_skills():
    text = (ROOT / "skills" / "linkedin-agent" / "SKILL.md").read_text()
    assert text.startswith("---\nname: linkedin-agent\n")
    assert "git clone https://github.com/latanasov/linkedin-agent" in text
    for name in ("linkedin-setup", "linkedin-outreach", "linkedin-campaign"):
        assert f".claude/skills/{name}/SKILL.md" in text
    assert "Never reply to a prospect" in text
