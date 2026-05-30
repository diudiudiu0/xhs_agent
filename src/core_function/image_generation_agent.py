import base64
import os
import sys
import urllib.request
from pathlib import Path

from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import IMAGE_MODEL_CONFIG
from src.core_function.task_config_loader import get_active_image_generation_task_config

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _build_client() -> OpenAI:
    api_key = IMAGE_MODEL_CONFIG.get("api_key") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "图片生成 API Key 为空。请在 cfg/model_config.py 的 IMAGE_MODEL_CONFIG['api_key'] 中填写，"
            "或设置环境变量 OPENAI_API_KEY。"
        )

    return OpenAI(
        api_key=api_key,
        base_url=IMAGE_MODEL_CONFIG.get("base_url") or None,
        timeout=IMAGE_MODEL_CONFIG.get("timeout", 120),
    )


def _next_output_path(output_dir: Path, output_format: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "." + output_format.lower().lstrip(".")
    used_numbers = []
    for item in output_dir.iterdir():
        if item.is_file() and item.stem.isdigit():
            used_numbers.append(int(item.stem))
    next_index = max(used_numbers, default=0) + 1
    return output_dir / f"{next_index}{suffix}"


def _save_image_item(item, output_dir: Path, output_format: str) -> Path:
    output_path = _next_output_path(output_dir, output_format)
    image_base64 = getattr(item, "b64_json", None)
    image_url = getattr(item, "url", None)

    if image_base64:
        output_path.write_bytes(base64.b64decode(image_base64))
        return output_path

    if image_url:
        with urllib.request.urlopen(image_url, timeout=120) as response:
            output_path.write_bytes(response.read())
        return output_path

    raise ValueError("图片生成接口未返回 b64_json 或 url，无法保存图片。")


def _request_args(prompt: str, count: int, model: str | None, size: str | None, quality: str | None) -> dict:
    args = {
        "model": model or IMAGE_MODEL_CONFIG.get("image_model", "gpt-image-1"),
        "prompt": prompt,
        "n": count,
    }
    if size:
        args["size"] = size
    if quality:
        args["quality"] = quality
    return args


def generate_or_edit_image(
    prompt: str,
    input_image: str | None = None,
    output_dir: str | None = None,
    count: int | None = None,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> list[Path]:
    """
    根据纯文本提示词生成图片，或根据本地参考图 + 修改要求编辑图片。

    返回保存到本机的图片路径列表，文件名按 1.png、2.png、3.png 顺序递增。
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt 不能为空，请填写图片生成/修改要求。")

    output_dir_path = _resolve_project_path(output_dir)
    if output_dir_path is None:
        raise ValueError("output_dir 不能为空。")

    count = count or 1
    output_format = IMAGE_MODEL_CONFIG.get("output_format", "png").lower().lstrip(".")
    size = size or IMAGE_MODEL_CONFIG.get("size")
    quality = quality or IMAGE_MODEL_CONFIG.get("quality")
    client = _build_client()

    args = _request_args(prompt.strip(), count, model, size, quality)
    input_image_path = _resolve_project_path(input_image)

    if input_image_path:
        if not input_image_path.exists() or not input_image_path.is_file():
            raise FileNotFoundError(f"输入图片不存在：{input_image_path}")
        if input_image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
            raise ValueError(f"输入图片格式暂不支持：{input_image_path.suffix}")

        print(f"开始编辑图片：{input_image_path}")
        with input_image_path.open("rb") as image_file:
            response = client.images.edit(image=image_file, **args)
    else:
        print("开始根据提示词生成图片")
        response = client.images.generate(**args)

    saved_paths = []
    for item in response.data:
        saved_paths.append(_save_image_item(item, output_dir_path, output_format))

    print("图片已保存：")
    for path in saved_paths:
        print(f"- {path}")
    return saved_paths


def validate_image_generation_task_config(task_config: dict | None = None) -> list[str]:
    task_config = task_config or get_active_image_generation_task_config()
    errors = []
    for field in task_config.get("required_fields", []):
        value = task_config.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"cfg/task.yaml 图片生成必填项 {field} 不能为空")
    return errors


def generate_or_edit_image_from_config(config: dict | None = None) -> list[Path]:
    """从 cfg/task.yaml 的 active_image_generation_task 读取任务参数并执行。"""
    task_config = config or get_active_image_generation_task_config()
    errors = validate_image_generation_task_config(task_config)
    if errors:
        raise ValueError("图片生成任务配置无效，已停止运行：\n" + "\n".join(f"- {error}" for error in errors))
    return generate_or_edit_image(
        prompt=task_config.get("prompt", ""),
        input_image=task_config.get("input_image"),
        output_dir=task_config.get("output_dir"),
        count=task_config.get("count"),
    )
