import argparse
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.image_prompt_agent import generate_image_prompts_from_image, generate_image_with_prompt_from_image_config
from src.task_config_loader import get_active_image_prompt_pipeline_config


def main():
    parser = argparse.ArgumentParser(description="Run image prompt generation pipeline.")
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Only generate prompts; do not call the image generation API.",
    )
    args = parser.parse_args()

    pipeline_config = get_active_image_prompt_pipeline_config()
    prompt_task_config = pipeline_config["prompt_task"]
    generation_task_config = pipeline_config["generation_task"]

    print("当前看图生成提示词任务配置：")
    for key, value in prompt_task_config.items():
        print(f"- {key}: {value}")

    print("\n当前图片生成任务配置：")
    for key, value in generation_task_config.items():
        print(f"- {key}: {value}")

    if args.prompt_only:
        generated_prompts = generate_image_prompts_from_image(prompt_task_config, generation_task_config)
        result = {"saved_paths": []}
    else:
        result = generate_image_with_prompt_from_image_config(prompt_task_config, generation_task_config)
        generated_prompts = result.get("generated_prompts") or [result["generated_prompt"]]

    print("\n视觉模型生成的图片提示词：")
    for index, prompt in enumerate(generated_prompts, start=1):
        print(f"\n[{index}] {prompt}")

    if args.prompt_only:
        print("\n已启用 --prompt-only，未调用图片生成 API。")
    else:
        print("\n图片生成完成，保存路径：")
        for path in result["saved_paths"]:
            print(path)


if __name__ == "__main__":
    main()
