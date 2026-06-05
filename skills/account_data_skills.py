from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message
from src.account_management_service import (
    analyze_account_performance,
    plan_content_topics,
    review_risky_action,
    schedule_content_calendar,
)
from src.web_note_metrics_collector import collect_latest_published_note_metrics


async def _collect_metrics(skill_name: str, context: SkillContext, args: dict[str, Any]) -> SkillResult:
    skills = context.require_xhs_skills()
    page = await skills.open_page("web")
    result = await collect_latest_published_note_metrics(
        page,
        output_file=args.get("output_file"),
        note_index=args.get("note_index"),
    )
    note = result.get("note") or {}
    storage = result.get("storage") or {}
    message_key = "duplicate" if storage.get("duplicate") else "success"
    message = skill_message(
        skill_name,
        message_key,
        title=note.get("title") or "",
        output_file=storage.get("output_file") or "",
    )
    return SkillResult.ok(
        skill_name,
        message=message,
        data=result,
        artifacts=[storage.get("output_file")] if storage.get("output_file") else [],
        observations=[
            skill_message(skill_name, "observation_title", title=note.get("title") or ""),
            skill_message(
                skill_name,
                "observation_counts",
                like_count=note.get("like_count"),
                collect_count=note.get("collect_count"),
                comment_count=note.get("comment_count"),
                share_count=note.get("share_count"),
            ),
        ],
        memory_updates={
            "last_collected_note_metrics": note,
            "last_collected_note_metrics_file": storage.get("output_file") or "",
        },
    )


class CollectLatestPublishedNoteMetricsSkill(BaseSkill):
    spec = build_skill_spec("collect_latest_published_note_metrics")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        return await _collect_metrics(self.name, context, args or {})


class CollectNoteMetricsSkill(BaseSkill):
    spec = build_skill_spec("collect_note_metrics")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        return await _collect_metrics(self.name, context, args or {})


class AnalyzeAccountPerformanceSkill(BaseSkill):
    spec = build_skill_spec("analyze_account_performance")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        analysis = analyze_account_performance(
            metrics_file=args.get("metrics_file"),
            top_n=args.get("top_n"),
        )
        totals = analysis.get("totals") or {}
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", note_count=analysis.get("note_count", 0)),
            data=analysis,
            observations=[
                skill_message(
                    self.name,
                    "observation_totals",
                    like_count=totals.get("like_count", 0),
                    collect_count=totals.get("collect_count", 0),
                    comment_count=totals.get("comment_count", 0),
                    share_count=totals.get("share_count", 0),
                ),
                *[str(item) for item in analysis.get("insights", [])[:3]],
            ],
            memory_updates={"last_account_analysis": analysis},
        )


class PlanContentTopicsSkill(BaseSkill):
    spec = build_skill_spec("plan_content_topics")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        session_memory = args.get("session_memory") if isinstance(args.get("session_memory"), dict) else context.session_memory
        analysis = args.get("analysis") or session_memory.get("last_account_analysis")
        plan = plan_content_topics(
            analysis=analysis,
            topic_count=args.get("topic_count") or args.get("count"),
            focus=args.get("focus"),
            output_file=args.get("output_file"),
        )
        topics = plan.get("topics") or []
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", count=len(topics), output_file=plan.get("output_file", "")),
            data=plan,
            artifacts=[plan.get("output_file")] if plan.get("output_file") else [],
            observations=[f"{item.get('id')}. {item.get('title')} - {item.get('angle')}" for item in topics[:5]],
            memory_updates={
                "planned_content_topics": topics,
                "planned_content_topics_file": plan.get("output_file", ""),
            },
        )


class ScheduleContentCalendarSkill(BaseSkill):
    spec = build_skill_spec("schedule_content_calendar")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        session_memory = args.get("session_memory") if isinstance(args.get("session_memory"), dict) else context.session_memory
        topics = args.get("topics") or session_memory.get("planned_content_topics")
        calendar = schedule_content_calendar(
            topics=topics,
            start_date=args.get("start_date"),
            days_between_posts=args.get("days_between_posts"),
            output_file=args.get("output_file"),
        )
        items = calendar.get("items") or []
        return SkillResult.ok(
            self.name,
            message=skill_message(self.name, "success", count=len(items), output_file=calendar.get("output_file", "")),
            data=calendar,
            artifacts=[calendar.get("output_file")] if calendar.get("output_file") else [],
            observations=[f"{item.get('date')} {item.get('title')}" for item in items[:7]],
            memory_updates={
                "content_calendar": items,
                "content_calendar_file": calendar.get("output_file", ""),
            },
        )


class ReplyCommentsSkill(BaseSkill):
    spec = build_skill_spec("reply_comments")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        target_comment = str(args.get("target_comment") or "").strip()
        reply_text = str(args.get("reply_text") or "").strip()
        user_goal = str(args.get("user_goal") or "").strip()
        if not any([target_comment, reply_text, user_goal]):
            return SkillResult.fail(self.name, skill_message(self.name, "missing_goal"), risk_level=self.spec.risk_level)

        if not user_goal:
            user_goal = "在小红书主站回复评论。"
            if target_comment:
                user_goal += f" 目标评论或作者：{target_comment}。"
            if reply_text:
                user_goal += f" 回复内容：{reply_text}。发送前必须请求用户确认。"

        skills = context.require_xhs_skills()
        result = await skills.explore_page_task(
            user_goal=user_goal,
            max_steps=int(args.get("max_steps") or 12),
            worklog_hints=args.get("worklog_hints") or [],
            target_site="web",
        )
        success = bool(result.get("success"))
        if not success:
            return SkillResult.fail(
                self.name,
                error=result.get("answer") or skill_message(self.name, "failed"),
                data=result,
                risk_level=self.spec.risk_level,
            )
        return SkillResult.ok(
            self.name,
            message=result.get("answer") or skill_message(self.name, "success"),
            data=result,
            observations=[result.get("answer", "")],
            risk_level=self.spec.risk_level,
            memory_updates={
                "page_task_answer": result.get("answer", ""),
                "page_task_steps": result.get("steps") or [],
            },
        )


class ReviewRiskyActionSkill(BaseSkill):
    spec = build_skill_spec("review_risky_action")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
        action_description = str(args.get("action_description") or args.get("user_goal") or "").strip()
        if not action_description:
            return SkillResult.fail(self.name, "action_description 不能为空。", risk_level=self.spec.risk_level)
        review = review_risky_action(
            action_description,
            context=args.get("context") if isinstance(args.get("context"), dict) else {},
        )
        return SkillResult.ok(
            self.name,
            message=skill_message(
                self.name,
                "success",
                risk_level=review.get("risk_level", ""),
                requires_confirmation=review.get("requires_confirmation"),
            ),
            data=review,
            observations=[review.get("recommendation", "")],
            risk_level=review.get("risk_level", self.spec.risk_level),
            memory_updates={"last_risk_review": review},
        )


ACCOUNT_DATA_SKILLS = [
    CollectNoteMetricsSkill(),
    CollectLatestPublishedNoteMetricsSkill(),
    AnalyzeAccountPerformanceSkill(),
    PlanContentTopicsSkill(),
    ScheduleContentCalendarSkill(),
    ReplyCommentsSkill(),
    ReviewRiskyActionSkill(),
]
