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
        allow_default_media = bool(args.get("allow_default_media", False))
        draft_result = await skills.create_draft(
            image_files=image_files,
            allow_default_media=allow_default_media,
        )

        title = skills.memory.last_note_title
        used_images = image_files or skills.memory.generated_images
        image_count = len(used_images or [])
        display_title = title or skill_message(self.name, "default_title")
        if not draft_result.get("success"):
            reason = draft_result.get("reason") or "草稿创建未完成。"
            return SkillResult.fail(
                self.name,
                error=reason,
                data=draft_result,
                observations=[
                    skill_message(self.name, "observation_title", title=display_title),
                    skill_message(self.name, "observation_image_count", image_count=image_count),
                    f"草稿状态：{draft_result.get('status', 'failed')}",
                ],
                risk_level=self.spec.risk_level,
            )

        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success"),
            data={
                "title": title,
                "image_count": image_count,
                "allow_default_media": allow_default_media,
                "draft_result": draft_result,
            },
            artifacts=list(used_images or []),
            observations=[
                skill_message(self.name, "observation_title", title=display_title),
                skill_message(self.name, "observation_image_count", image_count=image_count),
            ],
            risk_level=self.spec.risk_level,
            memory_updates={"last_created_draft_title": title},
            next_suggestions=[skill_message(self.name, "next_suggestion")],
        )


class CreateGeneratedNoteDraftSkill(BaseSkill):
    spec = build_skill_spec("create_generated_note_draft")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()

        title = args.get("title")
        user_goal = args.get("user_goal") or args.get("topic") or title
        count = args.get("count")
        input_image = args.get("input_image")

        prompts = skills.generate_prompts(
            input_image=input_image,
            user_goal=user_goal,
            count=count,
        )
        if not prompts:
            return SkillResult.fail(
                self.name,
                error=skill_message(self.name, "missing_prompts"),
                risk_level=self.spec.risk_level,
            )

        image_paths = skills.generate_images(prompts=prompts)
        if not image_paths:
            return SkillResult.fail(
                self.name,
                error=skill_message(self.name, "missing_images"),
                data={"prompts": prompts},
                risk_level=self.spec.risk_level,
            )

        note_text = skills.plan_note_text(title=title)
        draft_result = await skills.create_draft(
            image_files=image_paths,
            allow_default_media=False,
        )

        final_title = note_text.get("title") or skills.memory.last_note_title
        image_count = len(image_paths)
        if not draft_result.get("success"):
            reason = draft_result.get("reason") or skill_message(self.name, "draft_failed")
            return SkillResult.fail(
                self.name,
                error=skill_message(self.name, "draft_failed_with_reason", reason=reason),
                data={
                    "prompts": prompts,
                    "image_paths": image_paths,
                    "note_text": note_text,
                    "draft_result": draft_result,
                },
                observations=[
                    skill_message(self.name, "observation_prompt_count", count=len(prompts)),
                    skill_message(self.name, "observation_image_count", count=image_count),
                    skill_message(self.name, "observation_title", title=final_title),
                ],
                risk_level=self.spec.risk_level,
            )

        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", title=final_title),
            data={
                "prompts": prompts,
                "image_paths": image_paths,
                "note_text": note_text,
                "draft_result": draft_result,
            },
            artifacts=image_paths,
            observations=[
                skill_message(self.name, "observation_prompt_count", count=len(prompts)),
                skill_message(self.name, "observation_image_count", count=image_count),
                skill_message(self.name, "observation_title", title=final_title),
            ],
            risk_level=self.spec.risk_level,
            memory_updates={
                "generated_prompts": prompts,
                "generated_images": image_paths,
                "last_note_title": final_title,
                "last_note_content": note_text.get("content", ""),
                "last_created_draft_title": final_title,
            },
            next_suggestions=[skill_message(self.name, "next_suggestion")],
        )


DRAFT_SKILLS = [
    CreateGeneratedNoteDraftSkill(),
    CreateNoteDraftSkill(),
]
