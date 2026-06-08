import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.catalog import DEFAULT_SKILL_REGISTRY, render_skill_catalog


def main():
    specs = {spec["name"]: spec for spec in DEFAULT_SKILL_REGISTRY.specs()}
    required = {
        "generate_image_prompts",
        "revise_image_prompts",
        "generate_images",
        "plan_note_text",
        "create_generated_note_draft",
        "create_note_draft",
        "collect_note_metrics",
        "collect_latest_published_note_metrics",
        "analyze_account_performance",
        "plan_content_topics",
        "reply_comments",
        "schedule_content_calendar",
        "review_risky_action",
        "open_creator_page",
        "get_page_state",
        "explore_page_task",
        "handle_dialogs",
        "search_long_term_memory",
        "show_session_memory",
        "close_session",
    }
    missing = required - set(specs)
    if missing:
        raise AssertionError(f"missing skills: {sorted(missing)}")

    explorer = specs["explore_page_task"]
    if explorer.get("executor_type") != "agent":
        raise AssertionError("explore_page_task should be an agent skill")
    if explorer.get("executor_agent") != "page_explorer":
        raise AssertionError("explore_page_task should use page_explorer")
    if explorer.get("autonomy_level") != "high":
        raise AssertionError("explore_page_task should be high autonomy")
    if "send_reply" not in explorer.get("risk_policy", {}).get("requires_confirmation_for", []):
        raise AssertionError("explore_page_task must require confirmation for send_reply")
    if "web_site" not in explorer.get("tags", []):
        raise AssertionError("explore_page_task should advertise web site support")
    if "comments" not in explorer.get("tags", []):
        raise AssertionError("explore_page_task should advertise comment support")

    draft = specs["create_note_draft"]
    if draft.get("executor_type") != "workflow":
        raise AssertionError("create_note_draft should be workflow skill")

    generated_draft = specs["create_generated_note_draft"]
    if generated_draft.get("executor_type") != "workflow":
        raise AssertionError("create_generated_note_draft should be workflow skill")
    if "recommended" not in generated_draft.get("tags", []):
        raise AssertionError("create_generated_note_draft should be recommended for generated-image drafts")

    collector = specs["collect_latest_published_note_metrics"]
    if collector.get("executor_agent") != "web_note_metrics_collector":
        raise AssertionError("collect_latest_published_note_metrics should use web_note_metrics_collector")
    if "account_data" not in collector.get("tags", []):
        raise AssertionError("collect_latest_published_note_metrics should advertise account data support")

    for name in (
        "collect_note_metrics",
        "analyze_account_performance",
        "plan_content_topics",
        "reply_comments",
        "schedule_content_calendar",
        "review_risky_action",
    ):
        if name not in specs:
            raise AssertionError(f"missing account-management skill: {name}")

    if specs["reply_comments"].get("executor_agent") != "page_explorer":
        raise AssertionError("reply_comments should delegate to page_explorer")
    if specs["review_risky_action"].get("executor_agent") != "account_management_service":
        raise AssertionError("review_risky_action should use account_management_service")

    catalog = render_skill_catalog()
    for text in (
        "executor=agent/page_explorer",
        "requires_confirmation_for",
        "comments",
        "web_note_metrics_collector",
        "account_management_service",
        "collect_note_metrics",
        "review_risky_action",
        "search_long_term_memory",
        "create_generated_note_draft",
    ):
        if text not in catalog:
            raise AssertionError(f"rendered catalog missing: {text}")

    print("skill catalog check passed")


if __name__ == "__main__":
    main()
