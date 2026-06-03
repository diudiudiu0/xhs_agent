import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.image_prompt_agent import generate_image_with_prompt_from_image_config
from src.task_config_loader import get_active_image_prompt_pipeline_config


def main():
    pipeline_config = get_active_image_prompt_pipeline_config()
    prompt_task_config = pipeline_config["prompt_task"]
    generation_task_config = pipeline_config["generation_task"]

    print("当前看图生成提示词任务配置：")
    for key, value in prompt_task_config.items():
        print(f"- {key}: {value}")

    print("\n当前图片生成任务配置：")
    for key, value in generation_task_config.items():
        print(f"- {key}: {value}")

    result = generate_image_with_prompt_from_image_config(prompt_task_config, generation_task_config)
    generated_prompts = result.get("generated_prompts") or [result["generated_prompt"]]
    print("\n视觉模型生成的图片提示词：")
    for index, prompt in enumerate(generated_prompts, start=1):
        print(f"\n[{index}] {prompt}")

    print("\n图片生成完成，保存路径：")
    for path in result["saved_paths"]:
        print(path)


if __name__ == "__main__":
    main()
