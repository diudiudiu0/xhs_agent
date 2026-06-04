from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message


class CreateNoteDraftSkill(BaseSkill):
    spec = build_skill_spec("create_note_draft")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        image_files = args.get("image_files")
        await skills.create_draft(image_files=image_files)

        title = skills.memory.last_note_title
        used_images = image_files or skills.memory.generated_images
        image_count = len(used_images or [])
        display_title = title or skill_message(self.name, "default_title")
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success"),
            data={"title": title, "image_count": image_count},
            artifacts=list(used_images or []),
            observations=[
                skill_message(self.name, "observation_title", title=display_title),
                skill_message(self.name, "observation_image_count", image_count=image_count),
            ],
            risk_level=self.spec.risk_level,
            memory_updates={"last_created_draft_title": title},
            next_suggestions=[skill_message(self.name, "next_suggestion")],
        )


DRAFT_SKILLS = [
    CreateNoteDraftSkill(),
]
