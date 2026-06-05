import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
        "create_note_draft",
        "collect_latest_published_note_metrics",
        "open_creator_page",
        "get_page_state",
        "explore_page_task",
        "handle_dialogs",
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

    collector = specs["collect_latest_published_note_metrics"]
    if collector.get("executor_agent") != "web_note_metrics_collector":
        raise AssertionError("collect_latest_published_note_metrics should use web_note_metrics_collector")
    if "account_data" not in collector.get("tags", []):
        raise AssertionError("collect_latest_published_note_metrics should advertise account data support")

    catalog = render_skill_catalog()
    for text in ("executor=agent/page_explorer", "requires_confirmation_for", "comments", "web_note_metrics_collector"):
        if text not in catalog:
            raise AssertionError(f"rendered catalog missing: {text}")

    print("skill catalog check passed")


if __name__ == "__main__":
    main()
