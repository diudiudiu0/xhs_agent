from __future__ import annotations

from typing import Any

from skills.base import BaseSkill, SkillContext, SkillResult
from skills.config import build_skill_spec, skill_message
from src.web_note_metrics_collector import collect_latest_published_note_metrics


class CollectLatestPublishedNoteMetricsSkill(BaseSkill):
    spec = build_skill_spec("collect_latest_published_note_metrics")

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        args = args or {}
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
            self.name,
            message_key,
            title=note.get("title") or "",
            output_file=storage.get("output_file") or "",
        )
        return SkillResult.ok(
            self.name,
            message=message,
            data=result,
            artifacts=[storage.get("output_file")] if storage.get("output_file") else [],
            observations=[
                skill_message(self.name, "observation_title", title=note.get("title") or ""),
                skill_message(self.name, "observation_time", published_at=note.get("published_at") or ""),
                skill_message(
                    self.name,
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


ACCOUNT_DATA_SKILLS = [
    CollectLatestPublishedNoteMetricsSkill(),
]
