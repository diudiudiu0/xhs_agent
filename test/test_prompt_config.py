import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import MEMORY_REVIEW_MODEL_CONFIG, PAGE_CONTEXT_MODEL_CONFIG
from src.prompt_config import get_prompt_config, get_prompt_list, render_prompt_template


def main():
    if PAGE_CONTEXT_MODEL_CONFIG.get("model") != "deepseek-v4-flash":
        raise AssertionError("PAGE_CONTEXT_MODEL_CONFIG.model must be deepseek-v4-flash")
    if MEMORY_REVIEW_MODEL_CONFIG.get("model") != "deepseek-v4-flash":
        raise AssertionError("MEMORY_REVIEW_MODEL_CONFIG.model must be deepseek-v4-flash")

    phase_rules = get_prompt_list("page_phase", "rules")
    if not phase_rules:
        raise AssertionError("page_phase.rules is empty")

    planner_template = get_prompt_config("page_explorer", "planner_prompt_template", default="")
    if "{user_goal}" not in planner_template or "{snapshot_json}" not in planner_template:
        raise AssertionError("page_explorer planner template missing required placeholders")

    rendered = render_prompt_template("goal={user_goal}; data={data}", user_goal="test", data='{"ok": true}')
    if rendered != 'goal=test; data={"ok": true}':
        raise AssertionError("render_prompt_template failed")

    note_keywords = get_prompt_list("note_publisher", "keywords", "save_and_leave")
    if not note_keywords:
        raise AssertionError("note_publisher save_and_leave keywords are empty")

    steps = get_prompt_config("workflow", "default_steps", "explore_page_task", default=[])
    if not steps:
        raise AssertionError("workflow default steps missing explore_page_task")

    site_selector_template = get_prompt_config("xhs_skill_runtime", "site_selector_prompt_template", default="")
    if "{user_goal}" not in site_selector_template:
        raise AssertionError("xhs_skill_runtime site selector template missing user_goal")

    if not get_prompt_list("browser_tools", "save_and_leave", "save_words"):
        raise AssertionError("browser_tools save words are empty")
    if not get_prompt_list("interactive_element_extractor", "priority_words"):
        raise AssertionError("interactive_element_extractor priority words are empty")

    print("prompt config check passed")


if __name__ == "__main__":
    main()
