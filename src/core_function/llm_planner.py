# src/core_function/llm_planner.py
import json
from pathlib import Path

from cfg.model_config import MODEL_CONFIG
from src.core_function.element_extractor import extract_interactive_elements
from src.core_function.task_config_loader import get_active_note_task_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def get_active_task_config() -> dict:
    return get_active_note_task_config()


def _get_by_path(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _resolve_project_path(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def _folder_has_media(folder: str | None, exts: set[str]) -> bool:
    if not folder:
        return False
    path = Path(folder).expanduser()
    if not path.exists() or not path.is_dir():
        return False
    return any(item.is_file() and item.suffix.lower() in exts for item in path.iterdir())


def _file_exists(file_path: str | None, exts: set[str]) -> bool:
    if not file_path:
        return False
    path = Path(file_path).expanduser()
    return path.exists() and path.is_file() and path.suffix.lower() in exts


def validate_active_task_config() -> list[str]:
    task_config = get_active_task_config()
    task_input = task_config.get("input", {})
    errors = []
    required_fields = task_config.get("required_fields", [])
    for field_path in required_fields:
        value = _get_by_path(task_config, field_path)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"cfg/task.yaml 必填项 {field_path} 不能为空")

    topic = (task_input.get("topic") or "").strip()
    if not topic and "input.topic" not in required_fields:
        errors.append("cfg/task.yaml 的 input.topic 不能为空，请先填写运营主题，例如：脚轮推荐")

    post_type = (task_input.get("post_type") or "").strip().lower()
    if post_type not in {"image", "video"}:
        errors.append("cfg/task.yaml 的 input.post_type 必须是 image 或 video")

    image_folder = _resolve_project_path(task_input.get("image_folder"))
    video_folder = _resolve_project_path(task_input.get("video_folder"))
    default_image = _resolve_project_path(task_input.get("default_image_file") or "doc/pic_exam.png")
    default_video = _resolve_project_path(task_input.get("default_video_file") or "doc/vid_exam.mp4")

    if post_type == "image":
        if not _folder_has_media(image_folder, IMAGE_EXTS) and not _file_exists(default_image, IMAGE_EXTS):
            errors.append("图文笔记需要 input.image_folder 中有图片，或默认图片 doc/pic_exam.png 存在")
    elif post_type == "video":
        if not _folder_has_media(video_folder, VIDEO_EXTS) and not _file_exists(default_video, VIDEO_EXTS):
            errors.append("视频笔记需要 input.video_folder 中有视频，或默认视频 doc/vid_exam.mp4 存在")

    return errors


def ensure_active_task_config_valid() -> None:
    errors = validate_active_task_config()
    if errors:
        message = "任务配置无效，已停止运行：\n" + "\n".join(f"- {error}" for error in errors)
        raise ValueError(message)


def get_note_task_inputs(validate: bool = True) -> dict:
    if validate:
        ensure_active_task_config_valid()

    task_config = get_active_task_config()
    task_input = task_config.get("input", {})
    post_type = (task_input.get("post_type") or "image").strip().lower()
    return {
        "post_type": post_type,
        "topic": (task_input.get("topic") or "").strip(),
        "title": (task_input.get("title") or "").strip(),
        "seed_content": (task_input.get("seed_content") or "").strip(),
        "target_chars": int(task_input.get("target_chars") or 1000),
        "expand_content": bool(task_input.get("expand_content", True)),
        "image_folder": _resolve_project_path(task_input.get("image_folder")),
        "video_folder": _resolve_project_path(task_input.get("video_folder")),
        "default_image_file": _resolve_project_path(task_input.get("default_image_file") or "doc/pic_exam.png"),
        "default_video_file": _resolve_project_path(task_input.get("default_video_file") or "doc/vid_exam.mp4"),
        "num_images": int(task_input.get("num_images") or 3),
        "num_videos": int(task_input.get("num_videos") or 1),
    }


def build_task_description(title: str | None = None, content: str | None = None) -> str:
    task_input = get_note_task_inputs(validate=False)
    title = title or task_input["title"]
    content = content or task_input["seed_content"]
    task_config = get_active_task_config()
    template = task_config.get("task_description_template", "")
    post_type_name = "图文笔记" if task_input["post_type"] == "image" else "视频笔记"
    return template.format(
        title=title,
        content=content,
        post_type=task_input["post_type"],
        post_type_name=post_type_name,
    )


def _get_client():
    from openai import OpenAI

    return OpenAI(
        api_key=MODEL_CONFIG["api_key"],
        base_url=MODEL_CONFIG["base_url"],
        timeout=MODEL_CONFIG.get("timeout", 30),
    )


def _style_values(content_config: dict) -> dict:
    style_config = content_config.get("style", {})
    return {
        "account_role": style_config.get("account_role", "小红书商品推荐账号的运营写手"),
        "target_user": style_config.get("target_user", "对该主题感兴趣的用户"),
        "tone": style_config.get("tone", "真实、实用、自然"),
        "structure": style_config.get("structure", "痛点引入、选购建议、场景推荐、结尾互动"),
        "extra_requirements": style_config.get("extra_requirements", "不要夸大功效，只输出正文"),
    }


def _remove_question_marks(text: str) -> str:
    return (text or "").replace("？", "。").replace("?", "。").strip()


def _extract_json_object(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return None


def generate_note_text_from_image_prompts(
    image_prompts: list[str],
    title: str | None = None,
    seed_content: str | None = None,
    topic: str | None = None,
    target_chars: int | None = None,
) -> dict:
    """
    根据图片生成提示词规划小红书标题和正文。
    - title 为空：生成标题和正文。
    - title 非空：保留标题，结合标题和图片提示词生成正文。
    """
    task_input = get_note_task_inputs(validate=False)
    task_config = get_active_task_config()
    content_config = task_config.get("content_generation", {})

    title = (title if title is not None else task_input["title"]).strip()
    seed_content = (seed_content if seed_content is not None else task_input["seed_content"]).strip()
    topic = (topic if topic is not None else task_input["topic"]).strip()
    target_chars = target_chars or task_input["target_chars"]
    image_prompts_text = "\n\n".join(
        f"[{index}] {prompt}" for index, prompt in enumerate(image_prompts, start=1)
    )

    style = _style_values(content_config)
    values = {
        **style,
        "topic": topic,
        "title": title,
        "seed_content": seed_content,
        "target_chars": target_chars,
        "image_prompts_text": image_prompts_text,
    }

    if title:
        prompt_template = content_config.get("image_prompt_content_template", "")
        prompt = prompt_template.format(**values)
    else:
        prompt_template = content_config.get("image_prompt_title_content_template", "")
        prompt = prompt_template.format(**values)

    fallback_title = title or task_input["title"] or topic or "脚轮实用推荐"
    fallback_content = content_config.get("fallback_content", seed_content)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL_CONFIG["content_model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("content_max_tokens", 1800),
            temperature=MODEL_CONFIG.get("content_temperature", 0.7),
        )
        raw_text = (response.choices[0].message.content or "").strip()
        if title:
            content = raw_text
        else:
            parsed = _extract_json_object(raw_text)
            title = (parsed or {}).get("title", "").strip() if parsed else ""
            content = (parsed or {}).get("content", "").strip() if parsed else ""
            if not content:
                content = raw_text
            if not title:
                title = fallback_title

        content = _remove_question_marks(content)
        title = _remove_question_marks(title or fallback_title)
        if content:
            print(f"已根据图片提示词生成发帖内容，标题：{title}，正文长度约 {len(content)} 字符")
            return {"title": title, "content": content}
    except Exception as exc:
        print(f"根据图片提示词生成发帖内容失败，使用兜底内容：{exc}")

    return {
        "title": _remove_question_marks(fallback_title),
        "content": _remove_question_marks(fallback_content),
    }


def expand_note_content(
    title: str | None = None,
    seed_content: str | None = None,
    topic: str | None = None,
    target_chars: int | None = None,
) -> str:
    """
    将用户给出的简短需求扩写成小红书商品推荐正文。
    DeepSeek 失败时返回本地兜底文案，保证测试流程不被内容生成阻塞。
    """
    task_input = get_note_task_inputs(validate=False)
    title = title or task_input["title"]
    seed_content = seed_content or task_input["seed_content"]
    topic = topic or task_input["topic"]
    target_chars = target_chars or task_input["target_chars"]
    post_type_name = "图文笔记" if task_input["post_type"] == "image" else "视频笔记"

    task_config = get_active_task_config()
    content_config = task_config.get("content_generation", {})
    style_config = _style_values(content_config)
    prompt_template = content_config.get("prompt_template", "")
    prompt = prompt_template.format(
        topic=topic,
        title=title,
        seed_content=seed_content,
        target_chars=target_chars,
        post_type=task_input["post_type"],
        post_type_name=post_type_name,
        account_role=style_config["account_role"],
        target_user=style_config["target_user"],
        tone=style_config["tone"],
        structure=style_config["structure"],
        extra_requirements=style_config["extra_requirements"],
    )
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL_CONFIG["content_model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("content_max_tokens", 1800),
            temperature=MODEL_CONFIG.get("content_temperature", 0.7),
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            text = _remove_question_marks(text)
            print(f"已生成扩写正文，长度约 {len(text)} 字符")
            return text
    except Exception as exc:
        print(f"正文扩写失败，使用本地兜底文案：{exc}")

    return _remove_question_marks(content_config.get("fallback_content", seed_content))


async def _get_page_context(page):
    try:
        title = await page.title()
    except Exception:
        title = ""

    try:
        visible_text = await page.evaluate(
            """() => (document.body?.innerText || '')
                .replace(/\\s+/g, ' ')
                .trim()
                .slice(0, 5000)
            """
        )
    except Exception:
        visible_text = ""

    try:
        fields = await page.evaluate(
            """() => Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                })
                .map((el, index) => ({
                    index,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
                    value: (el.value || '').slice(0, 300)
                }))
            """
        )
    except Exception:
        fields = []

    try:
        action_candidates = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
                'button, a, [role="button"], [role="link"], [tabindex], [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"]'
            ))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden';
                })
                .map((el, index) => ({
                    index,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    className: String(el.className || '').slice(0, 120),
                    text: (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .slice(0, 160)
                }))
                .filter(item => item.text)
                .slice(0, 80)
            """
        )
    except Exception:
        action_candidates = []

    return {
        "url": page.url,
        "title": title,
        "visible_text": visible_text,
        "fields": fields,
        "action_candidates": action_candidates,
    }


async def get_next_action(
    page,
    task_description: str,
    history: list = None,
    elements: list = None,
    agent_state: dict | None = None,
):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    if elements is None:
        elements = await extract_interactive_elements(page)

    if not elements:
        # 如果还是没有元素，告诉 Agent 等待或返回 back
        print("当前页面无可用元素")
        # 返回一个特殊动作，让循环处理
        return {"action": "wait"}

    # 打印前20个元素，方便观察真实页面状态。
    print("当前可交互元素（前20个）：")
    for el in elements[:20]:
        print(f"  [{el['index']}] {el['desc']}")

    elements_text = "\n".join([f"[{el['index']}] {el['desc']}" for el in elements])

    history_str = "暂无"
    if history:
        history_str = "\n".join(
            [
                (
                    f"- step={a.get('step')} action={a.get('action')} "
                    f"index={a.get('element_index')} desc={a.get('desc', '')} "
                    f"value={a.get('value', '')} result={a.get('result', '')}"
                )
                for a in history[-8:]
            ]
        )

    page_context = await _get_page_context(page)
    state_text = json.dumps(agent_state or {}, ensure_ascii=False, indent=2)
    context_text = json.dumps(page_context, ensure_ascii=False, indent=2)
    task_config = get_active_task_config()
    first_step_instruction = ""
    if not history:
        first_step_instruction = task_config.get("first_step_instruction", "")
    rules_text = "\n".join([f"    - {rule}" for rule in task_config.get("planner_rules", [])])
    planner_intro = task_config.get(
        "planner_intro",
        "你是小红书创作中心自动化 Agent，目标是创建图文笔记草稿。",
    )
    response_schema = task_config.get(
        "response_schema",
        '{"action": "wait", "element_index": null, "value": "", "reason": "", "expected_result": ""}',
    )

    prompt = f"""{planner_intro}
    
    当前 Agent 内部状态：
    {state_text}
    
    浏览器页面上下文：
    {context_text}
    
    当前页面可交互元素列表：
    {elements_text}
    
    最近执行历史：
    {history_str}
    
    {first_step_instruction}
    
    任务目标：{task_description}
    
    返回严格 JSON：
{response_schema}
    
    规则：
{rules_text}
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL_CONFIG["planner_model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("planner_max_tokens", 800),
            temperature=MODEL_CONFIG.get("planner_temperature", 0.1)
        )
        raw_content = response.choices[0].message.content
        print(f"LLM 原始返回: {raw_content}")
        start = raw_content.find('{')
        end = raw_content.rfind('}') + 1
        if start != -1 and end != -1:
            content = raw_content[start:end]
        else:
            print("JSON 提取失败")
            return {"action": "done"}
        return json.loads(content)
    except Exception as e:
        print(f"LLM 调用失败: {e}")
        return {"action": "wait"}
