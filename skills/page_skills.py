from __future__ import annotations

from typing import Any

from src.browser_state_observer import summarize_browser_state
from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message


class OpenCreatorPageSkill(BaseSkill):
    spec = build_skill_spec("open_creator_page")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        page = await skills.open_page(args.get("target_site") or args.get("site") or "creator")
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success"),
            data={"url": page.url},
            observations=[skill_message(self.name, "observation_url", url=page.url)],
        )


class GetPageStateSkill(BaseSkill):
    spec = build_skill_spec("get_page_state")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        skills = context.require_xhs_skills()
        if args.get("target_site") or args.get("site"):
            await skills.open_page(args.get("target_site") or args.get("site"))
        state = await skills.get_page_state()
        summary = summarize_browser_state(state)
        return SkillResult.ok(
            self.name,
            message=summary,
            data={"state": state, "summary": summary},
            observations=[summary],
        )


class ExplorePageTaskSkill(BaseSkill):
    spec = build_skill_spec("explore_page_task")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        user_goal = args.get("user_goal") or args.get("message")
        if not user_goal:
            return SkillResult.fail(
                self.name,
                skill_message(self.name, "missing_goal"),
                risk_level=self.spec.risk_level,
            )

        skills = context.require_xhs_skills()
        result = await skills.explore_page_task(
            user_goal=str(user_goal),
            max_steps=int(args.get("max_steps") or 12),
            worklog_hints=args.get("worklog_hints") or [],
            target_site=args.get("target_site") or args.get("site"),
        )
        success = bool(result.get("success"))
        answer = result.get("answer", "")
        if not success:
            return SkillResult.fail(
                self.name,
                error=answer or skill_message(self.name, "failed"),
                data=result,
                risk_level=self.spec.risk_level,
            )

        next_suggestion = result.get("next_suggestion")
        return SkillResult.ok(
            self.name,
            message=answer or skill_message(self.name, "success"),
            data=result,
            observations=[answer] if answer else [],
            risk_level=self.spec.risk_level,
            next_suggestions=[next_suggestion] if next_suggestion else [],
            memory_updates={
                "page_task_answer": answer,
                "page_task_steps": result.get("steps") or [],
            },
        )


class HandleDialogsSkill(BaseSkill):
    spec = build_skill_spec("handle_dialogs")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        skills = context.require_xhs_skills()
        handled = await skills.handle_page_dialogs()
        message = skill_message(self.name, "success", handled=handled)
        return SkillResult.ok(
            self.name,
            message=message,
            data={"handled": handled},
            observations=[message],
        )


PAGE_SKILLS = [
    OpenCreatorPageSkill(),
    GetPageStateSkill(),
    ExplorePageTaskSkill(),
    HandleDialogsSkill(),
]
