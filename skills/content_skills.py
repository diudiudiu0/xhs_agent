from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message


class PlanNoteTextSkill(BaseSkill):
    spec = build_skill_spec("plan_note_text")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        note_text = skills.plan_note_text(title=args.get("title"))
        title = note_text.get("title", "")
        content = note_text.get("content", "")
        content_length = len(content)
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", title=title),
            data={"title": title, "content": content},
            observations=[
                skill_message(self.name, "observation_title", title=title),
                skill_message(self.name, "observation_content_length", content_length=content_length),
            ],
            memory_updates={"last_note_title": title, "last_note_content": content},
        )


CONTENT_SKILLS = [
    PlanNoteTextSkill(),
]
