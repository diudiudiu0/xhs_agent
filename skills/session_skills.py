from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message


class ShowSessionMemorySkill(BaseSkill):
    spec = build_skill_spec("show_session_memory")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        skills = context.require_xhs_skills()
        memory_text = skills.show_memory()
        return SkillResult.ok(
            self.name,
            message=memory_text,
            data={"memory_text": memory_text},
            observations=[memory_text],
        )


class CloseSessionSkill(BaseSkill):
    spec = build_skill_spec("close_session")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        await context.close()
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success"),
            data={"closed": True},
            observations=[skill_message(self.name, "observation")],
        )


SESSION_SKILLS = [
    ShowSessionMemorySkill(),
    CloseSessionSkill(),
]
