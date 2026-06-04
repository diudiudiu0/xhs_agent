from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message


class GenerateImagePromptsSkill(BaseSkill):
    spec = build_skill_spec("generate_image_prompts")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        prompts = skills.generate_prompts(
            input_image=args.get("input_image"),
            user_goal=args.get("user_goal"),
            count=args.get("count"),
        )
        count = len(prompts)
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", count=count),
            data={"prompts": prompts, "count": count},
            observations=[skill_message(self.name, "observation_count", count=count)],
            memory_updates={"generated_prompts": prompts},
        )


class ReviseImagePromptsSkill(BaseSkill):
    spec = build_skill_spec("revise_image_prompts")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        instruction = args.get("revision_instruction") or args.get("user_goal") or args.get("message")
        if not instruction:
            return SkillResult.fail(self.name, skill_message(self.name, "missing_instruction"))

        skills = context.require_xhs_skills()
        prompts = skills.revise_prompts(str(instruction))
        count = len(prompts)
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", count=count),
            data={"prompts": prompts, "count": count},
            observations=[skill_message(self.name, "observation_count", count=count)],
            memory_updates={"generated_prompts": prompts},
        )


class GenerateImagesSkill(BaseSkill):
    spec = build_skill_spec("generate_images")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        image_paths = skills.generate_images(prompts=args.get("prompts"))
        count = len(image_paths)
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", count=count),
            data={"image_paths": image_paths, "count": count},
            artifacts=image_paths,
            observations=[skill_message(self.name, "observation_count", count=count)],
            memory_updates={"generated_images": image_paths},
        )


IMAGE_SKILLS = [
    GenerateImagePromptsSkill(),
    ReviseImagePromptsSkill(),
    GenerateImagesSkill(),
]
