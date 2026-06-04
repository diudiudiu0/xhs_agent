from __future__ import annotations

from skills.base import SkillContext, SkillRegistry, SkillResult
from skills.content_skills import CONTENT_SKILLS
from skills.draft_skills import DRAFT_SKILLS
from skills.image_skills import IMAGE_SKILLS
from skills.page_skills import PAGE_SKILLS
from skills.session_skills import SESSION_SKILLS


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for skill in [
        *IMAGE_SKILLS,
        *CONTENT_SKILLS,
        *DRAFT_SKILLS,
        *PAGE_SKILLS,
        *SESSION_SKILLS,
    ]:
        registry.register(skill)
    return registry


DEFAULT_SKILL_REGISTRY = build_default_registry()


async def run_skill(
    name: str,
    args: dict | None = None,
    context: SkillContext | None = None,
) -> SkillResult:
    """Run a registered skill with a uniform result object."""

    return await DEFAULT_SKILL_REGISTRY.run(name, args=args or {}, context=context)


def render_skill_catalog() -> str:
    """Render the skill catalog for a manager/planner prompt."""

    return DEFAULT_SKILL_REGISTRY.render_catalog()
