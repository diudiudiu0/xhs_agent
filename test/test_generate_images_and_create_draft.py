import asyncio
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.core_function.agent_note_publisher import agent_create_note_draft
from src.core_function.browser_actions import open_creator_home
from src.core_function.image_prompt_agent import generate_image_with_prompt_from_image_config
from src.core_function.llm_planner import get_note_task_inputs
from src.core_function.task_config_loader import get_active_image_prompt_pipeline_config


def _numeric_sort_key(path: Path):
    return (0, int(path.stem)) if path.stem.isdigit() else (1, path.name)


def generate_images_for_note() -> list[str]:
    pipeline_config = get_active_image_prompt_pipeline_config()
    prompt_task_config = pipeline_config["prompt_task"]
    generation_task_config = pipeline_config["generation_task"]

    print("开始生成发帖图片素材...")
    result = generate_image_with_prompt_from_image_config(prompt_task_config, generation_task_config)
    saved_paths = [Path(path) for path in result.get("saved_paths", [])]
    saved_paths = sorted(saved_paths, key=_numeric_sort_key)

    if not saved_paths:
        raise ValueError("图片生成完成但没有返回任何保存路径，已停止发帖流程。")

    print("\n本次将按以下顺序上传图片：")
    for index, path in enumerate(saved_paths, start=1):
        print(f"{index}. {path}")

    return [str(path) for path in saved_paths]


async def main():
    try:
        task_input = get_note_task_inputs(validate=True)
        image_files = generate_images_for_note()
    except Exception as exc:
        print(exc)
        return

    page, browser, context, p = await open_creator_home(headless=False)

    try:
        await agent_create_note_draft(
            page,
            post_type="image",
            title=task_input["title"],
            content=task_input["seed_content"],
            image_folder=str(Path(image_files[0]).parent),
            image_files=image_files,
            num_images=len(image_files),
            expand_content=task_input["expand_content"],
            content_topic=task_input["topic"],
            default_image_file=image_files[0],
            video_folder=task_input["video_folder"],
            default_video_file=task_input["default_video_file"],
            num_videos=task_input["num_videos"],
        )

        print("图片生成并创建草稿流程结束，请检查页面，30秒后自动关闭...")
        await asyncio.sleep(30)

    finally:
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
