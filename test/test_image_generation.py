import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.task_config_loader import get_active_image_generation_task_config
from src.image_generation_agent import generate_or_edit_image_from_config


def main():
    task_config = get_active_image_generation_task_config()
    print("当前图片生成任务配置：")
    for key, value in task_config.items():
        print(f"- {key}: {value}")

    saved_paths = generate_or_edit_image_from_config(task_config)
    print("测试完成，生成文件：")
    for path in saved_paths:
        print(path)


if __name__ == "__main__":
    main()
