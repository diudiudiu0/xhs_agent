import base64
import mimetypes
import os
import sys
import urllib.request
from pathlib import Path

from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import IMAGE_MODEL_CONFIG
from src.task_config_loader import get_active_image_generation_task_config

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value.replace("\\", "/")).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _get_openai_api_key() -> str:
    api_key = IMAGE_MODEL_CONFIG.get("api_key") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI 图片生成 API Key 为空。请在 cfg/model_config.py 的 IMAGE_MODEL_CONFIG['api_key'] 中填写，"
            "或设置环境变量 OPENAI_API_KEY。"
        )
    return api_key


def _get_doubao_api_key() -> str:
    api_key = (
        IMAGE_MODEL_CONFIG.get("api_key")
        or os.getenv("ARK_API_KEY")
        or os.getenv("VOLCENGINE_API_KEY")
    )
    if not api_key:
        raise ValueError(
            "豆包/火山方舟图片生成 API Key 为空。请在 cfg/model_config.py 的 IMAGE_MODEL_CONFIG['api_key'] 中填写，"
            "或设置环境变量 ARK_API_KEY / VOLCENGINE_API_KEY。"
        )
    return api_key


def _build_openai_client() -> OpenAI:
    return OpenAI(
        api_key=_get_openai_api_key(),
        base_url=IMAGE_MODEL_CONFIG.get("base_url") or None,
        timeout=IMAGE_MODEL_CONFIG.get("timeout", 120),
    )


def _build_doubao_client() -> OpenAI:
    return OpenAI(
        api_key=_get_doubao_api_key(),
        base_url=IMAGE_MODEL_CONFIG.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3",
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
    if isinstance(item, dict):
        image_base64 = item.get("b64_json") or item.get("base64") or item.get("image_base64")
        image_url = item.get("url") or item.get("image_url")
    else:
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


def _encode_image_as_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_input_image(input_image: str | None, input_image_source: str = "local") -> Path | str | None:
    if not input_image:
        return None
    source = (input_image_source or "local").strip().lower()
    if source not in {"local", "url"}:
        raise ValueError("input_image_source 只能是 local 或 url")
    if source == "url":
        if not input_image.startswith(("http://", "https://")):
            raise ValueError("input_image_source=url 时，input_image 必须是 http:// 或 https:// 开头的图片地址")
        return input_image
    if input_image.startswith(("http://", "https://")):
        raise ValueError("input_image_source=local 时，input_image 应为本机图片路径；如果要使用 URL，请改为 input_image_source: url")
    return _resolve_project_path(input_image)


def _generate_or_edit_with_openai(
    prompt: str,
    input_image_path: Path | None,
    output_dir: Path,
    count: int,
    model: str | None,
    size: str | None,
    quality: str | None,
    output_format: str,
) -> list[Path]:
    client = _build_openai_client()
    args = _request_args(prompt, count, model, size, quality)

    if isinstance(input_image_path, str):
        raise ValueError("OpenAI 图片编辑当前只支持本机图片文件，不支持直接传 URL。")

    if input_image_path:
        print(f"开始使用 OpenAI 编辑图片：{input_image_path}")
        with input_image_path.open("rb") as image_file:
            response = client.images.edit(image=image_file, **args)
    else:
        print("开始使用 OpenAI 根据提示词生成图片")
        response = client.images.generate(**args)

    return [_save_image_item(item, output_dir, output_format) for item in response.data]


def _generate_or_edit_with_doubao(
    prompt: str,
    input_image: Path | str | None,
    output_dir: Path,
    count: int,
    model: str | None,
    size: str | None,
    aspect_ratio: str | None,
    watermark: bool | None,
    output_format: str,
) -> list[Path]:
    if input_image:
        print(f"开始使用豆包/Seedream 参考图片生成或编辑：{input_image}")
    else:
        print("开始使用豆包/Seedream 根据提示词生成图片")

    extra_body = {}
    if watermark is not None:
        extra_body["watermark"] = bool(watermark)
    elif "watermark" in IMAGE_MODEL_CONFIG:
        extra_body["watermark"] = bool(IMAGE_MODEL_CONFIG.get("watermark"))
    if aspect_ratio:
        extra_body["aspect_ratio"] = aspect_ratio
    if input_image:
        extra_body["image"] = _encode_image_as_data_url(input_image) if isinstance(input_image, Path) else input_image

    client = _build_doubao_client()
    print(
        "正在调用豆包/Seedream 图片生成接口："
        f"model={model or IMAGE_MODEL_CONFIG.get('image_model', 'doubao-seedream-5-0-260128')}，"
        f"size={size or IMAGE_MODEL_CONFIG.get('size', '2K')}，"
        f"aspect_ratio={aspect_ratio or '未指定'}，count={count}",
        flush=True,
    )
    response = client.images.generate(
        model=model or IMAGE_MODEL_CONFIG.get("image_model", "doubao-seedream-5-0-260128"),
        prompt=prompt,
        size=size or IMAGE_MODEL_CONFIG.get("size", "2K"),
        response_format=IMAGE_MODEL_CONFIG.get("response_format", "b64_json"),
        n=count,
        extra_body=extra_body,
    )
    return [_save_image_item(item, output_dir, output_format) for item in response.data]


def generate_or_edit_image(
    prompt: str,
    input_image: str | None = None,
    output_dir: str | None = None,
    count: int | None = None,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    aspect_ratio: str | None = None,
    input_image_source: str = "local",
    watermark: bool | None = None,
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
    input_image_obj = _resolve_input_image(input_image, input_image_source)

    if isinstance(input_image_obj, Path):
        if not input_image_obj.exists() or not input_image_obj.is_file():
            raise FileNotFoundError(
                f"输入图片不存在：{input_image_obj}。"
                "如果使用相对路径，请确认它是相对项目根目录的路径，例如 doc/pic_exam.png。"
            )
        if input_image_obj.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
            raise ValueError(
                f"输入图片格式暂不支持：{input_image_obj.suffix}。"
                "当前支持 .png、.jpg、.jpeg、.webp。"
            )

    provider = (IMAGE_MODEL_CONFIG.get("provider") or "openai").strip().lower()
    if provider == "doubao":
        saved_paths = _generate_or_edit_with_doubao(
            prompt.strip(),
            input_image_obj,
            output_dir_path,
            count,
            model,
            size,
            aspect_ratio,
            watermark,
            output_format,
        )
    elif provider == "openai":
        saved_paths = _generate_or_edit_with_openai(
            prompt.strip(),
            input_image_obj,
            output_dir_path,
            count,
            model,
            size,
            quality,
            output_format,
        )
    else:
        raise ValueError(f"暂不支持的图片生成 provider：{provider}")

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
        input_image_source=task_config.get("input_image_source") or "local",
        output_dir=task_config.get("output_dir"),
        count=task_config.get("count"),
        size=task_config.get("size"),
        aspect_ratio=task_config.get("aspect_ratio"),
        watermark=task_config.get("watermark"),
    )
