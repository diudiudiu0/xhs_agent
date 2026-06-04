# test/test_page_tool_registry.py
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.page_tool_registry import PAGE_TOOL_REGISTRY
from src.xhs_page_explorer import _is_valid_action


def main():
    expected_tools = {
        "click",
        "click_semantic_target",
        "click_text_in_element",
        "click_near_text",
        "click_media_near_text",
        "fill",
        "fill_textbox",
        "fill_title",
        "fill_content",
        "save_and_leave",
        "switch_site",
        "back",
        "wait",
        "extract_answer",
        "done",
        "fail",
    }
    names = set(PAGE_TOOL_REGISTRY.names())
    missing = expected_tools - names
    if missing:
        raise AssertionError(f"页面工具缺失：{sorted(missing)}")

    valid_actions = [
        {
            "action": "click_near_text",
            "near_text": "目标对象唯一文本",
            "text": "目标操作",
            "requires_user_confirmation": False,
            "reason": "锚点区域点击",
        },
        {
            "action": "click_media_near_text",
            "near_text": "目标对象唯一文本",
            "requires_user_confirmation": False,
            "reason": "打开关联预览",
        },
        {
            "action": "fill_textbox",
            "hint_text": "回复",
            "value": "回复内容",
            "requires_user_confirmation": False,
            "reason": "填写动态输入框",
        },
        {
            "action": "done",
            "answer": "任务完成",
            "next_suggestion": "可以继续下一项",
            "requires_user_confirmation": False,
            "reason": "完成任务",
        },
    ]

    for action in valid_actions:
        if not _is_valid_action(action):
            raise AssertionError(f"动作未通过 schema 校验：{action}")

    print("页面工具注册表校验通过")
    print(PAGE_TOOL_REGISTRY.names())


if __name__ == "__main__":
    main()
