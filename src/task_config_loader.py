import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_CONFIG_PATH = PROJECT_ROOT / "cfg" / "task.yaml"
IMAGE_TASK_CONFIG_PATH = PROJECT_ROOT / "cfg" / "image_task.yaml"


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index].rstrip()
        escaped = False
    return line.rstrip()


def _preprocess_yaml(text: str) -> list[tuple[int, str]]:
    rows = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = _strip_inline_comment(raw_line.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        rows.append((indent, line.strip()))
    return rows


def _parse_scalar(value: str):
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return ast.literal_eval(value)
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _find_block_end(lines: list[tuple[int, str]], start: int, parent_indent: int) -> int:
    index = start
    while index < len(lines) and lines[index][0] > parent_indent:
        index += 1
    return index


def _parse_block_scalar(lines: list[tuple[int, str]], start: int, parent_indent: int) -> tuple[str, int]:
    end = _find_block_end(lines, start, parent_indent)
    if start >= end:
        return "", end
    min_indent = min(indent for indent, _ in lines[start:end])
    text_lines = []
    for indent, content in lines[start:end]:
        text_lines.append(" " * max(0, indent - min_indent) + content)
    return "\n".join(text_lines).rstrip(), end


def _parse_sequence(lines: list[tuple[int, str]], start: int, indent: int) -> tuple[list, int]:
    result = []
    index = start
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent != indent or not content.startswith("- "):
            break
        item_text = content[2:].strip()
        if not item_text:
            item, index = _parse_node(lines, index + 1, indent + 2)
            result.append(item)
            continue
        if ":" in item_text and not item_text.startswith(("'", '"')):
            key, value = item_text.split(":", 1)
            item = {key.strip(): _parse_scalar(value.strip())}
            index += 1
            while index < len(lines) and lines[index][0] > indent:
                nested_indent, nested_content = lines[index]
                if ":" not in nested_content:
                    break
                nested_key, nested_value = nested_content.split(":", 1)
                nested_value = nested_value.strip()
                if nested_value in {"|", ">"}:
                    item[nested_key.strip()], index = _parse_block_scalar(lines, index + 1, nested_indent)
                elif nested_value:
                    item[nested_key.strip()] = _parse_scalar(nested_value)
                    index += 1
                else:
                    item[nested_key.strip()], index = _parse_node(lines, index + 1, nested_indent + 2)
            result.append(item)
            continue
        result.append(_parse_scalar(item_text))
        index += 1
    return result, index


def _parse_mapping(lines: list[tuple[int, str]], start: int, indent: int) -> tuple[dict, int]:
    result = {}
    index = start
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            index += 1
            continue
        if content.startswith("- "):
            break
        if ":" not in content:
            index += 1
            continue
        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value in {"|", ">"}:
            result[key], index = _parse_block_scalar(lines, index + 1, current_indent)
        elif value:
            result[key] = _parse_scalar(value)
            index += 1
        else:
            result[key], index = _parse_node(lines, index + 1, current_indent + 2)
    return result, index


def _parse_node(lines: list[tuple[int, str]], start: int, indent: int):
    if start >= len(lines):
        return {}, start
    current_indent, content = lines[start]
    if current_indent < indent:
        return {}, start
    if content.startswith("- "):
        return _parse_sequence(lines, start, current_indent)
    return _parse_mapping(lines, start, current_indent)


def _fallback_safe_load(text: str) -> dict:
    rows = _preprocess_yaml(text)
    parsed, _ = _parse_node(rows, 0, 0)
    return parsed


def _safe_load_yaml_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        data = yaml.safe_load(text)
    except ModuleNotFoundError:
        data = _fallback_safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 解析结果不是字典，请检查 YAML 格式")
    return data


def _deep_merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_task_config() -> dict:
    data = _safe_load_yaml_file(TASK_CONFIG_PATH)
    if IMAGE_TASK_CONFIG_PATH.exists():
        data = _deep_merge(data, _safe_load_yaml_file(IMAGE_TASK_CONFIG_PATH))
    return data


def get_active_note_task_config() -> dict:
    config = load_task_config()
    active_task = config.get("active_note_task", "xhs_note_draft")
    tasks = config.get("note_tasks", {})
    if active_task not in tasks:
        raise KeyError(f"task.yaml 中不存在 active_note_task：{active_task}")
    return tasks[active_task]


def get_active_image_generation_task_config() -> dict:
    config = load_task_config()
    active_task = config.get("active_image_generation_task", "image_asset_generation")
    tasks = config.get("image_generation_tasks", {})
    if active_task not in tasks:
        raise KeyError(f"cfg/image_task.yaml 中不存在 active_image_generation_task：{active_task}")
    return tasks[active_task]


def get_active_image_prompt_task_config() -> dict:
    config = load_task_config()
    active_task = config.get("active_image_prompt_task", "caster_product_prompt")
    tasks = config.get("image_prompt_tasks", {})
    if active_task not in tasks:
        raise KeyError(f"cfg/image_task.yaml 中不存在 active_image_prompt_task：{active_task}")
    return tasks[active_task]


def get_active_image_prompt_pipeline_config() -> dict:
    config = load_task_config()
    active_task = config.get("active_image_prompt_pipeline_task", "caster_product_pipeline")
    tasks = config.get("image_prompt_pipeline_tasks", {})
    if active_task not in tasks:
        raise KeyError(f"cfg/image_task.yaml 中不存在 active_image_prompt_pipeline_task：{active_task}")
    pipeline_config = tasks[active_task]

    prompt_task_name = pipeline_config.get("prompt_task")
    generation_task_name = pipeline_config.get("generation_task")
    prompt_tasks = config.get("image_prompt_tasks", {})
    generation_tasks = config.get("image_generation_tasks", {})
    if prompt_task_name not in prompt_tasks:
        raise KeyError(f"cfg/image_task.yaml 中不存在 image_prompt_tasks.{prompt_task_name}")
    if generation_task_name not in generation_tasks:
        raise KeyError(f"cfg/image_task.yaml 中不存在 image_generation_tasks.{generation_task_name}")

    return {
        "pipeline": pipeline_config,
        "prompt_task": prompt_tasks[prompt_task_name],
        "generation_task": generation_tasks[generation_task_name],
    }
