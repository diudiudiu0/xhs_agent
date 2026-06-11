import json
import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.account_management_service import (
    analyze_account_performance,
    generate_creative_strategy,
    plan_content_topics,
    review_risky_action,
    schedule_content_calendar,
)


def main():
    metrics_file = Path("data/test_account_metrics.json")
    overview_file = Path("data/test_account_overview.json")
    strategy_file = Path("data/test_creative_strategy.json")
    topics_file = Path("data/test_content_topics.json")
    calendar_file = Path("data/test_content_calendar.json")
    for path in (metrics_file, overview_file, strategy_file, topics_file, calendar_file):
        if path.exists():
            path.unlink()

    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    metrics_file.write_text(
        json.dumps(
            {
                "version": 1,
                "notes": [
                    {
                        "title": "caster guide",
                        "published_at": "2026-06-04",
                        "view_count": 100,
                        "like_count": 2,
                        "collect_count": 5,
                        "comment_count": 1,
                        "share_count": 0,
                        "comments": [{"author": "user_a", "content": "nice"}],
                    },
                    {
                        "title": "tool cart caster setup",
                        "published_at": "2026-06-05",
                        "view_count": 50,
                        "like_count": 1,
                        "collect_count": 1,
                        "comment_count": 0,
                        "share_count": 1,
                        "comments": [],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    overview_file.write_text(
        json.dumps(
            {
                "version": 1,
                "overview": {
                    "metrics": {"views": 150, "likes": 3, "collects": 6},
                    "metric_cards": [],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    analysis = analyze_account_performance(metrics_file=metrics_file)
    if analysis["note_count"] != 2:
        raise AssertionError(analysis)
    if analysis["totals"]["view_count"] != 150 or analysis["rates"]["engagement_rate"] is None:
        raise AssertionError(analysis)
    if not analysis["top_notes"] or analysis["top_notes"][0]["title"] != "caster guide":
        raise AssertionError(analysis["top_notes"])

    strategy = generate_creative_strategy(
        metrics_file=metrics_file,
        overview_file=overview_file,
        output_file=strategy_file,
        topic_count=2,
        use_llm=False,
    )
    if len(strategy.get("next_topics", [])) != 2 or not strategy_file.exists():
        raise AssertionError(strategy)

    topic_plan = plan_content_topics(
        analysis=analysis,
        topic_count=3,
        focus="caster product recommendation",
        output_file=topics_file,
    )
    if len(topic_plan["topics"]) != 3 or not topics_file.exists():
        raise AssertionError(topic_plan)

    calendar = schedule_content_calendar(
        topics=topic_plan["topics"],
        start_date="2026-06-10",
        days_between_posts=2,
        output_file=calendar_file,
    )
    if calendar["items"][1]["date"] != "2026-06-12" or not calendar_file.exists():
        raise AssertionError(calendar)

    risk = review_risky_action("删除第三篇草稿")
    if risk["risk_level"] != "high" or not risk["requires_confirmation"]:
        raise AssertionError(risk)

    safe = review_risky_action("查看账号数据")
    if safe["risk_level"] != "low" or safe["requires_confirmation"]:
        raise AssertionError(safe)

    for path in (metrics_file, overview_file, strategy_file, topics_file, calendar_file):
        if path.exists():
            path.unlink()
    print("account management service check passed")


if __name__ == "__main__":
    main()
