import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from cfg.model_config import PAGE_CONTEXT_MODEL_CONFIG
from src.page_context import PageContextManager


def main():
    if PAGE_CONTEXT_MODEL_CONFIG.get("model") != "deepseek-v4-flash":
        raise AssertionError("page_context model should be deepseek-v4-flash")

    manager = PageContextManager()
    manager.reset(
        "reply to a comment based on the related note content",
        {
            "site": "web",
            "page_phase": "web_note_detail",
            "url": "https://www.xiaohongshu.com/example",
        },
    )
    context = manager.context
    required_keys = {
        "schema_version",
        "site",
        "page_phase",
        "current_url",
        "current_task",
        "task_stage",
        "target",
        "collected",
        "ui_state",
        "missing",
        "last_action_effect",
        "navigation_path",
    }
    missing = required_keys - set(context)
    if missing:
        raise AssertionError(f"page_context missing fields: {sorted(missing)}")
    if context["site"] != "web":
        raise AssertionError("page_context site was not synchronized")
    if context["navigation_path"] != []:
        raise AssertionError("page_context navigation_path should start empty")

    manager.config["update_prompt_template"] = ""
    manager.update(
        "reply to a comment based on the related note content",
        {"action": "click", "element_index": 7, "reason": "open note management"},
        "已点击",
        "点击后页面从首页进入笔记管理页。",
        {
            "site": "creator",
            "page_phase": "creator_home",
            "url": "https://creator.xiaohongshu.com/new/home",
        },
        {
            "site": "creator",
            "page_phase": "note_management",
            "url": "https://creator.xiaohongshu.com/creator/notes",
        },
    )
    path = manager.context.get("navigation_path")
    if not isinstance(path, list) or len(path) != 1:
        raise AssertionError("page_context navigation_path did not record the update step")
    if "click" not in path[0].get("action", ""):
        raise AssertionError("page_context navigation_path action is missing")
    if "note_management" not in path[0].get("to", ""):
        raise AssertionError("page_context navigation_path destination is missing")
    if not manager.render():
        raise AssertionError("page_context render is empty")
    print("page_context config and default structure check passed")
    print(manager.brief())


if __name__ == "__main__":
    main()
