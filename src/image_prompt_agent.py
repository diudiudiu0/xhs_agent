import base64
import json
import mimetypes
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import IMAGE_PROMPT_PLANNER_MODEL_CONFIG, MODEL_CONFIG, VISION_PROMPT_MODEL_CONFIG
from src.image_generation_service import generate_or_edit_image_from_config
from src.prompt_config import get_prompt_config
from src.task_config_loader import get_active_image_prompt_pipeline_config, get_active_image_prompt_task_config


SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _as_positive_int(value, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _format_template(template: str, values: dict) -> str:
    return (template or "").format_map(_SafeFormatDict(values)).strip()


def _resolve_project_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value.replace("\\", "/")).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _encode_image_as_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_required_image_url(task_config: dict) -> str:
    input_image = (task_config.get("input_image") or "").strip()
    if not input_image:
        raise ValueError("看图生成提示词要求 input_image 非空，请在 cfg/task.yaml 中填写本机图片路径或图片 URL。")

    source = (task_config.get("input_image_source") or "local").strip().lower()
    if source not in {"local", "url"}:
        raise ValueError("input_image_source 只能是 local 或 url")

    if source == "url":
        if not input_image.startswith(("http://", "https://")):
            raise ValueError("input_image_source=url 时，input_image 必须是 http:// 或 https:// 开头的图片地址")
        return input_image

    if input_image.startswith(("http://", "https://")):
        raise ValueError("input_image_source=local 时，input_image 应为本机图片路径；如果要使用 URL，请改为 input_image_source: url")

    image_path = _resolve_project_path(input_image)
    if image_path is None or not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(
            f"输入图片不存在：{image_path}。如果使用相对路径，请确认它是相对项目根目录的路径，例如 doc/pic_exam.png。"
        )
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        raise ValueError(f"输入图片格式暂不支持：{image_path.suffix}。当前支持 .png、.jpg、.jpeg、.webp。")
    return _encode_image_as_data_url(image_path)


def _resolve_api_key_from_config(config: dict, default_env_key: str) -> tuple[str, list[str]]:
    env_keys = config.get("env_keys")
    if not isinstance(env_keys, list) or not env_keys:
        env_keys = [config.get("env_key") or default_env_key]
    api_key = config.get("api_key")
    if not api_key:
        api_key = next((os.getenv(str(env_key)) for env_key in env_keys if os.getenv(str(env_key))), "")
    return api_key, [str(item) for item in env_keys]


def _build_openai_client_from_config(config: dict, default_env_key: str, default_timeout: float) -> OpenAI:
    api_key, env_keys = _resolve_api_key_from_config(config, default_env_key)
    if not api_key:
        raise ValueError(
            "模型 API Key 为空。请在 cfg/model_config.py 中填写对应 api_key，"
            f"或设置环境变量 {'/'.join(env_keys)}。"
        )
    timeout_value = float(config.get("timeout", default_timeout) or default_timeout)
    timeout = httpx.Timeout(
        timeout=timeout_value,
        connect=float(config.get("connect_timeout", min(20.0, timeout_value)) or min(20.0, timeout_value)),
        write=float(config.get("write_timeout", min(30.0, timeout_value)) or min(30.0, timeout_value)),
        read=float(config.get("read_timeout", timeout_value) or timeout_value),
        pool=float(config.get("pool_timeout", min(20.0, timeout_value)) or min(20.0, timeout_value)),
    )
    return OpenAI(
        api_key=api_key,
        base_url=config.get("base_url"),
        timeout=timeout,
    )


def _build_vision_client() -> OpenAI:
    return _build_openai_client_from_config(VISION_PROMPT_MODEL_CONFIG, "MOONSHOT_API_KEY", 180)


def _build_image_prompt_planner_client() -> OpenAI:
    return _build_openai_client_from_config(IMAGE_PROMPT_PLANNER_MODEL_CONFIG, "MOONSHOT_API_KEY", 180)


def _build_text_formatter_client() -> OpenAI:
    return OpenAI(
        api_key=MODEL_CONFIG["api_key"],
        base_url=MODEL_CONFIG["base_url"],
        timeout=MODEL_CONFIG.get("timeout", 30),
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _heading_pattern_text(heading_keywords: list[str] | None = None) -> str:
    keyword_pattern = ""
    if heading_keywords:
        escaped_keywords = [re.escape(str(keyword)) for keyword in heading_keywords if str(keyword).strip()]
        if escaped_keywords:
            keyword_pattern = "|" + "|".join(escaped_keywords)
    return (
        r"PROMPT[_\s-]*\d+|(?:第\s*)?[一二三四五六七八九十\d]+\s*[张幅条]?"
        f"{keyword_pattern}"
    )


def _clean_prompt_candidate(text: str, heading_keywords: list[str] | None = None) -> str:
    text = _strip_json_fence(text)
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"^\s*[-*]\s*", "", text).strip()
    text = re.sub(
        rf"^(?:{_heading_pattern_text(heading_keywords)})\s*[:：、\.\)]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return text


def _extract_section_prompts(
    cleaned: str,
    expected_count: int,
    heading_keywords: list[str] | None = None,
) -> list[str]:
    heading_pattern = re.compile(
        r"(?m)(?:^|\n)\s*(?:[-*]\s*)?"
        rf"(?:{_heading_pattern_text(heading_keywords)})"
        r"\s*[:：、\.\)]\s*",
        flags=re.IGNORECASE,
    )
    matches = list(heading_pattern.finditer(cleaned))
    prompts = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        candidate = _clean_prompt_candidate(cleaned[start:end], heading_keywords)
        if len(candidate) >= 20:
            prompts.append(candidate)
    if len(prompts) >= expected_count:
        return prompts[:expected_count]

    paragraph_prompts = []
    for paragraph in re.split(r"\n\s*\n+", cleaned):
        candidate = _clean_prompt_candidate(paragraph, heading_keywords)
        if len(candidate) >= 30:
            paragraph_prompts.append(candidate)
    if len(paragraph_prompts) >= expected_count:
        return paragraph_prompts[:expected_count]

    return []


def _parse_prompt_list(
    raw_text: str,
    expected_count: int,
    heading_keywords: list[str] | None = None,
) -> list[str]:
    cleaned = _strip_json_fence(raw_text)
    json_text = cleaned
    if not json_text.startswith("["):
        start = json_text.find("[")
        end = json_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            json_text = json_text[start : end + 1]

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for key in ("prompts", "items", "results"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        if isinstance(parsed, dict):
            dict_prompts = []
            for key, value in sorted(parsed.items(), key=lambda item: str(item[0])):
                if isinstance(value, str) and re.search(r"(prompt|image|图|张|\d)", str(key), flags=re.IGNORECASE):
                    candidate = _clean_prompt_candidate(value, heading_keywords)
                    if candidate:
                        dict_prompts.append(candidate)
            if len(dict_prompts) >= expected_count:
                return dict_prompts[:expected_count]

    if isinstance(parsed, list):
        prompts = []
        for item in parsed:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                value = str(item.get("prompt") or item.get("text") or "").strip()
            else:
                value = ""
            if value:
                prompts.append(value)
        if len(prompts) >= expected_count:
            return prompts[:expected_count]

    section_prompts = _extract_section_prompts(cleaned, expected_count, heading_keywords)
    if section_prompts:
        return section_prompts

    fallback_prompts = []
    for line in cleaned.splitlines():
        line = line.strip()
        candidate = _clean_prompt_candidate(line, heading_keywords)
        if len(candidate) >= 20:
            fallback_prompts.append(candidate)

    if len(fallback_prompts) >= expected_count:
        return fallback_prompts[:expected_count]

    preview = cleaned[:800].replace("\n", "\\n")
    raise ValueError(
        "看图提示词模型没有返回可解析的批量提示词。"
        "请让模型返回 JSON 数组，或降低 image_generation_tasks.count 后重试。"
        f"模型原始返回预览：{preview}"
    )


def _format_prompts_with_text_model(
    raw_text: str,
    expected_count: int,
    prompt_task_config: dict,
    generation_task_config: dict | None = None,
) -> list[str]:
    generation_task_config = generation_task_config or {}
    user_goal = (prompt_task_config.get("user_goal") or "").strip()
    batch_prompt_plan = (prompt_task_config.get("batch_prompt_plan") or "").strip()
    size = generation_task_config.get("size") or "未指定"
    aspect_ratio = generation_task_config.get("aspect_ratio") or "未指定"
    formatter_config = prompt_task_config.get("formatter", {})
    formatter_template = (formatter_config.get("prompt_template") or "").strip()
    if not formatter_template:
        raise ValueError("cfg/image_task.yaml 中 image_prompt_tasks.<任务名>.formatter.prompt_template 不能为空")

    formatter_prompt = _format_template(
        formatter_template,
        {
            "raw_text": raw_text,
            "expected_count": expected_count,
            "user_goal": user_goal,
            "size": size,
            "aspect_ratio": aspect_ratio,
            "batch_prompt_plan": batch_prompt_plan,
        },
    )

    client = _build_text_formatter_client()
    response = client.chat.completions.create(
        model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("content_model", "deepseek-v4-flash"),
        messages=[{"role": "user", "content": formatter_prompt}],
        max_tokens=MODEL_CONFIG.get("formatter_max_tokens", max(1800, expected_count * 900)),
        temperature=MODEL_CONFIG.get("formatter_temperature", MODEL_CONFIG.get("planner_temperature", 0.1)),
    )
    formatted_text = (response.choices[0].message.content or "").strip()
    if not formatted_text:
        raise ValueError("DeepSeek 格式化模型返回为空，无法整理图片提示词。")
    heading_keywords = prompt_task_config.get("parse_heading_keywords", [])
    return _parse_prompt_list(formatted_text, expected_count, heading_keywords)


def _plan_prompts_from_image_description(
    image_description: str,
    expected_count: int,
    prompt_task_config: dict,
    generation_task_config: dict | None = None,
) -> list[str]:
    generation_task_config = generation_task_config or {}
    planner_config = prompt_task_config.get("description_planner", {})
    planner_template = (planner_config.get("prompt_template") or "").strip()
    if not planner_template:
        return _format_prompts_with_text_model(
            image_description,
            expected_count,
            prompt_task_config,
            generation_task_config,
        )

    size = generation_task_config.get("size") or prompt_task_config.get("size") or "未指定"
    aspect_ratio = generation_task_config.get("aspect_ratio") or prompt_task_config.get("aspect_ratio") or "未指定"
    planner_prompt = _format_template(
        planner_template,
        {
            "image_description": image_description,
            "expected_count": expected_count,
            "instruction": (prompt_task_config.get("instruction") or "").strip(),
            "user_goal": (prompt_task_config.get("user_goal") or "").strip(),
            "batch_prompt_plan": (prompt_task_config.get("batch_prompt_plan") or "").strip(),
            "size": size,
            "aspect_ratio": aspect_ratio,
        },
    )

    client = _build_image_prompt_planner_client()
    planner_model = IMAGE_PROMPT_PLANNER_MODEL_CONFIG.get("model") or VISION_PROMPT_MODEL_CONFIG.get("model", "kimi-k2.6")
    planner_temperature = IMAGE_PROMPT_PLANNER_MODEL_CONFIG.get("temperature")
    planner_max_tokens = _as_positive_int(
        planner_config.get("max_tokens") or IMAGE_PROMPT_PLANNER_MODEL_CONFIG.get("max_tokens"),
        max(1800, expected_count * 900),
    )
    response = client.chat.completions.create(
        model=planner_model,
        messages=[{"role": "user", "content": planner_prompt}],
        max_tokens=planner_max_tokens,
        temperature=planner_temperature,
    )
    planned_text = (response.choices[0].message.content or "").strip()
    if not planned_text:
        raise ValueError("Kimi 批量图片提示词规划返回为空，无法继续生成图片。")
    heading_keywords = prompt_task_config.get("parse_heading_keywords", [])
    return _parse_prompt_list(planned_text, expected_count, heading_keywords)


def _extract_text_from_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "prompt", "reasoning_content"):
            text = _extract_text_from_value(value.get(key))
            if text:
                return text
        return ""
    return ""


def _extract_response_text(response) -> str:
    if not getattr(response, "choices", None):
        return ""

    message = response.choices[0].message
    text = _extract_text_from_value(getattr(message, "content", None))
    if text:
        return text

    for attr_name in ("reasoning_content", "text"):
        text = _extract_text_from_value(getattr(message, attr_name, None))
        if text:
            return text

    if hasattr(message, "model_dump"):
        dumped = message.model_dump()
        for key in ("content", "reasoning_content", "text"):
            text = _extract_text_from_value(dumped.get(key))
            if text:
                return text

    return ""


def _get_first_choice_finish_reason(response) -> str:
    if not getattr(response, "choices", None):
        return "no_choices"
    return str(getattr(response.choices[0], "finish_reason", "") or "unknown")


def _build_prompt_instruction(
    task_config: dict,
    generation_task_config: dict | None = None,
    prompt_count: int = 1,
) -> str:
    instruction = (task_config.get("instruction") or "").strip()
    user_goal = (task_config.get("user_goal") or "").strip()
    generation_task_config = generation_task_config or {}
    size = generation_task_config.get("size") or task_config.get("size") or ""
    aspect_ratio = generation_task_config.get("aspect_ratio") or task_config.get("aspect_ratio") or ""
    batch_prompt_plan = (task_config.get("batch_prompt_plan") or "").strip()
    output_requirements = task_config.get("vision_output_requirements", {})
    output_requirement = (
        output_requirements.get("single")
        if prompt_count == 1
        else output_requirements.get("batch")
    ) or ""
    prompt_template = (task_config.get("vision_prompt_template") or "").strip()
    if not prompt_template:
        raise ValueError("cfg/image_task.yaml 中 image_prompt_tasks.<任务名>.vision_prompt_template 不能为空")

    return _format_template(
        prompt_template,
        {
            "instruction": instruction,
            "user_goal": user_goal,
            "prompt_count": prompt_count,
            "size": size or "未指定",
            "aspect_ratio": aspect_ratio or "未指定",
            "batch_prompt_plan": batch_prompt_plan,
            "vision_output_requirements": _format_template(
                output_requirement,
                {"prompt_count": prompt_count},
            ),
        },
    )


def _build_vision_description_instruction(
    task_config: dict,
    generation_task_config: dict | None = None,
    prompt_count: int = 1,
) -> str:
    generation_task_config = generation_task_config or {}
    description_template = (task_config.get("vision_description_template") or "").strip()
    if not description_template:
        return _build_prompt_instruction(
            task_config,
            generation_task_config=generation_task_config,
            prompt_count=1,
        )

    size = generation_task_config.get("size") or task_config.get("size") or ""
    aspect_ratio = generation_task_config.get("aspect_ratio") or task_config.get("aspect_ratio") or ""
    return _format_template(
        description_template,
        {
            "instruction": (task_config.get("instruction") or "").strip(),
            "user_goal": (task_config.get("user_goal") or "").strip(),
            "prompt_count": prompt_count,
            "size": size or "未指定",
            "aspect_ratio": aspect_ratio or "未指定",
            "batch_prompt_plan": (task_config.get("batch_prompt_plan") or "").strip(),
        },
    )


def _build_retry_instruction(prompt_count: int, task_config: dict | None = None) -> str:
    retry_templates = (task_config or {}).get("retry_instruction_template", {})
    retry_template = retry_templates.get("single") if prompt_count == 1 else retry_templates.get("batch")
    if retry_template:
        return _format_template(retry_template, {"prompt_count": prompt_count})
    return ""


def _call_vision_prompt_model(
    image_url: str,
    prompt_instruction: str,
    prompt_count: int = 1,
    prompt_task_config: dict | None = None,
    log_task_name: str = "图片提示词",
) -> str:
    model_name = VISION_PROMPT_MODEL_CONFIG.get("model", "kimi-k2.6")
    temperature = VISION_PROMPT_MODEL_CONFIG.get("temperature")
    if temperature is None:
        temperature = 1 if str(model_name).startswith("kimi-") else 0.4
    max_tokens = _as_positive_int(VISION_PROMPT_MODEL_CONFIG.get("max_tokens"), 1200)
    if prompt_count > 1:
        max_tokens = max(max_tokens, prompt_count * 1100)

    system_prompt = VISION_PROMPT_MODEL_CONFIG.get(
        "system_prompt",
        str(get_prompt_config("image_prompt_agent", "default_system_prompt", default="")),
    )
    image_url_payload = {"url": image_url}
    image_url_detail = str(VISION_PROMPT_MODEL_CONFIG.get("image_url_detail") or "").strip()
    if image_url_detail:
        image_url_payload["detail"] = image_url_detail

    user_content = [
        {"type": "image_url", "image_url": image_url_payload},
        {"type": "text", "text": prompt_instruction},
    ]

    client = _build_vision_client()
    extra_body = VISION_PROMPT_MODEL_CONFIG.get("extra_body")
    max_attempts = _as_positive_int(VISION_PROMPT_MODEL_CONFIG.get("retry_attempts"), 2)
    empty_reasons = []
    connection_errors = []
    for attempt in range(max_attempts):
        current_user_content = list(user_content)
        if empty_reasons:
            retry_instruction = _build_retry_instruction(prompt_count, prompt_task_config)
            if retry_instruction:
                current_user_content.append({"type": "text", "text": retry_instruction})

        try:
            print(
                f"正在调用看图提示词模型：{model_name}，任务={log_task_name}，目标数量={prompt_count}，"
                f"第 {attempt + 1}/{max_attempts} 次尝试...",
                flush=True,
            )
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": current_user_content},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body if isinstance(extra_body, dict) and extra_body else None,
            )
        except (APITimeoutError, APIConnectionError) as exc:
            error_name = type(exc).__name__
            connection_errors.append(error_name)
            print(f"看图提示词模型连接/超时异常：{error_name}，准备重试 ({attempt + 1}/{max_attempts})")
            if attempt + 1 >= max_attempts:
                raise TimeoutError(
                    "看图提示词模型请求超时或连接失败，已停止继续生成图片。"
                    "如果是批量生成，请尝试把 cfg/model_config.py 中 "
                    "VISION_PROMPT_MODEL_CONFIG['timeout'] 调大到 180-300，"
                    "或先把 cfg/image_task.yaml 中 image_generation_tasks 的 count 降低到 1-2。"
                ) from exc
            continue

        generated_prompt = _extract_response_text(response)
        if generated_prompt:
            print("看图提示词模型已返回内容。", flush=True)
            return generated_prompt
        empty_reasons.append(_get_first_choice_finish_reason(response))

    raise ValueError(
        "看图提示词模型连续返回空内容，无法继续生成图片。"
        f"finish_reason={empty_reasons}。"
        f"connection_errors={connection_errors}。"
        "这通常是模型没有按格式输出、响应被截断/过滤，或当前视觉模型对该图片输入不稳定。"
    )


def generate_image_prompt_from_image(task_config: dict | None = None) -> str:
    """使用视觉大模型根据 input_image 生成单条图片生成提示词。"""
    task_config = task_config or get_active_image_prompt_task_config()
    image_url = _resolve_required_image_url(task_config)
    prompt_instruction = _build_prompt_instruction(task_config, prompt_count=1)
    return _call_vision_prompt_model(image_url, prompt_instruction, prompt_count=1, prompt_task_config=task_config)


def generate_image_prompts_from_image(
    prompt_task_config: dict | None = None,
    generation_task_config: dict | None = None,
) -> list[str]:
    """根据出图数量生成一组分工明确的图片生成提示词。"""
    prompt_task_config = prompt_task_config or get_active_image_prompt_task_config()
    prompt_count = _as_positive_int((generation_task_config or {}).get("count"), 1)
    print(f"准备根据参考图生成 {prompt_count} 条图片提示词...", flush=True)
    image_url = _resolve_required_image_url(prompt_task_config)
    mode = str(prompt_task_config.get("prompt_generation_mode") or "").strip().lower()
    if not mode:
        mode = "vision_describe_then_plan" if prompt_count > 1 else "legacy_vision_prompt"

    if mode in {"vision_describe_then_plan", "describe_then_plan", "vision_summary_then_plan"}:
        print("采用两段式流程：视觉模型描述参考图，文本模型规划批量图片提示词。", flush=True)
        description_instruction = _build_vision_description_instruction(
            prompt_task_config,
            generation_task_config=generation_task_config,
            prompt_count=prompt_count,
        )
        image_description = _call_vision_prompt_model(
            image_url,
            description_instruction,
            prompt_count=1,
            prompt_task_config=prompt_task_config,
            log_task_name="参考图描述",
        )
        print("正在调用文本模型根据图片描述规划批量图片提示词...", flush=True)
        return _plan_prompts_from_image_description(
            image_description,
            prompt_count,
            prompt_task_config,
            generation_task_config,
        )

    prompt_instruction = _build_prompt_instruction(
        prompt_task_config,
        generation_task_config=generation_task_config,
        prompt_count=prompt_count,
    )
    raw_text = _call_vision_prompt_model(
        image_url,
        prompt_instruction,
        prompt_count=prompt_count,
        prompt_task_config=prompt_task_config,
    )
    if prompt_count == 1:
        return [raw_text]
    try:
        print("正在调用文本模型整理批量图片提示词...", flush=True)
        return _format_prompts_with_text_model(raw_text, prompt_count, prompt_task_config, generation_task_config)
    except Exception as exc:
        print(f"DeepSeek 格式化提示词失败，尝试本地解析兜底：{exc}")
        return _parse_prompt_list(raw_text, prompt_count, prompt_task_config.get("parse_heading_keywords", []))


def generate_image_with_prompt_from_image_config(
    prompt_task_config: dict | None = None,
    generation_task_config: dict | None = None,
) -> dict:
    """
    先根据 input_image 生成图片 prompt，再把生成的 prompt 交给 image_generation_service 出图。

    返回：
    - generated_prompt：兼容旧调用，单张时为提示词，多张时为多条提示词拼接文本
    - generated_prompts：视觉模型生成的提示词列表
    - saved_paths：图片生成结果路径列表
    """
    if prompt_task_config is None or generation_task_config is None:
        pipeline_config = get_active_image_prompt_pipeline_config()
        prompt_task_config = prompt_task_config or pipeline_config["prompt_task"]
        generation_task_config = generation_task_config or pipeline_config["generation_task"]

    generated_prompts = generate_image_prompts_from_image(prompt_task_config, generation_task_config)

    saved_paths = []
    for index, generated_prompt in enumerate(generated_prompts, start=1):
        print(f"\n开始生成第 {index}/{len(generated_prompts)} 张图片")
        image_task_config = deepcopy(generation_task_config)
        image_task_config["prompt"] = generated_prompt
        image_task_config["count"] = 1
        saved_paths.extend(generate_or_edit_image_from_config(image_task_config))

    return {
        "generated_prompt": generated_prompts[0] if len(generated_prompts) == 1 else "\n\n".join(generated_prompts),
        "generated_prompts": generated_prompts,
        "saved_paths": saved_paths,
    }
