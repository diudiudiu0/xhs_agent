# test/test_page_context.py
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from cfg.model_config import PAGE_CONTEXT_MODEL_CONFIG
from src.page_context import PageContextManager


def main():
    if PAGE_CONTEXT_MODEL_CONFIG.get("model") != "deepseek-v4-flash":
        raise AssertionError("page_context 模型应配置为 deepseek-v4-flash")

    manager = PageContextManager()
    manager.reset(
        "根据评论和所属帖子回复评论",
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
        raise AssertionError(f"page_context 缺少字段：{sorted(missing)}")
    if context["site"] != "web":
        raise AssertionError("page_context site 未正确同步")
    if not manager.render():
        raise AssertionError("page_context render 为空")
    print("page_context 配置和默认结构校验通过")
    print(manager.brief())


if __name__ == "__main__":
    main()
