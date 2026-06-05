import asyncio
import json
import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from skills.base import SkillContext
from skills.catalog import DEFAULT_SKILL_REGISTRY


async def main():
    metrics_file = Path("data/test_account_management_skills_metrics.json")
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    metrics_file.write_text(
        json.dumps(
            {
                "version": 1,
                "notes": [
                    {
                        "title": "caster parameter post",
                        "published_at": "2026-06-05",
                        "like_count": 1,
                        "collect_count": 3,
                        "comment_count": 1,
                        "share_count": 0,
                        "comments": [{"author": "user_a", "content": "nice"}],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    context = SkillContext()
    analysis_result = await DEFAULT_SKILL_REGISTRY.run(
        "analyze_account_performance",
        args={"metrics_file": str(metrics_file)},
        context=context,
    )
    if not analysis_result.success or analysis_result.data.get("note_count") != 1:
        raise AssertionError(analysis_result)

    topic_result = await DEFAULT_SKILL_REGISTRY.run(
        "plan_content_topics",
        args={
            "analysis": analysis_result.data,
            "topic_count": 2,
            "output_file": "data/test_account_management_skills_topics.json",
        },
        context=context,
    )
    if not topic_result.success or len(topic_result.data.get("topics", [])) != 2:
        raise AssertionError(topic_result)

    calendar_result = await DEFAULT_SKILL_REGISTRY.run(
        "schedule_content_calendar",
        args={
            "topics": topic_result.data["topics"],
            "start_date": "2026-06-10",
            "output_file": "data/test_account_management_skills_calendar.json",
        },
        context=context,
    )
    if not calendar_result.success or not calendar_result.data.get("items"):
        raise AssertionError(calendar_result)

    risk_result = await DEFAULT_SKILL_REGISTRY.run(
        "review_risky_action",
        args={"action_description": "发送评论回复"},
        context=context,
    )
    if not risk_result.success or not risk_result.data.get("requires_confirmation"):
        raise AssertionError(risk_result)

    for path in [
        metrics_file,
        Path("data/test_account_management_skills_topics.json"),
        Path("data/test_account_management_skills_calendar.json"),
    ]:
        if path.exists():
            path.unlink()
    print("account management skills check passed")


if __name__ == "__main__":
    asyncio.run(main())
