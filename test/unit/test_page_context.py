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
    }
    missing = required_keys - set(context)
    if missing:
        raise AssertionError(f"page_context missing fields: {sorted(missing)}")
    if context["site"] != "web":
        raise AssertionError("page_context site was not synchronized")
    if not manager.render():
        raise AssertionError("page_context render is empty")
    print("page_context config and default structure check passed")
    print(manager.brief())


if __name__ == "__main__":
    main()
