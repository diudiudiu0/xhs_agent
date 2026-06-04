import asyncio
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import open_creator_home
from src.note_draft_workflow import agent_create_note_draft
from src.note_content_service import get_note_task_inputs

async def main():
    try:
        task_input = get_note_task_inputs(validate=True)
    except ValueError as exc:
        print(exc)
        return

    # 1. 打开已登录的创作中心
    page, browser, context, p = await open_creator_home(headless=False)

    try:
        # 2. 调用 Agent 创建草稿；标题、主题、正文要点和素材配置来自 cfg/task.yaml
        await agent_create_note_draft(
            page,
            post_type=task_input["post_type"],
            title=task_input["title"],
            content=task_input["seed_content"],
            image_folder=task_input["image_folder"],
            video_folder=task_input["video_folder"],
            num_images=task_input["num_images"],
            num_videos=task_input["num_videos"],
            expand_content=task_input["expand_content"],
            content_topic=task_input["topic"],
            default_image_file=task_input["default_image_file"],
            default_video_file=task_input["default_video_file"],
        )

        # 3. 观察结果
        print("请检查页面，30秒后自动关闭...")
        await asyncio.sleep(30)

    finally:
        # 4. 清理
        await browser.close()
        await p.stop()

if __name__ == "__main__":
    asyncio.run(main())
 