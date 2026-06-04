from functools import lru_cache
from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_CONFIG_PATH = PROJECT_ROOT / "cfg" / "prompts.yaml"


@lru_cache(maxsize=1)
def load_prompt_config() -> dict:
    if not PROMPT_CONFIG_PATH.exists():
        return {}
    return _safe_load_yaml_file(PROMPT_CONFIG_PATH)


def get_prompt_config(*keys: str, default: Any = None) -> Any:
    value: Any = load_prompt_config()
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def get_prompt_dict(*keys: str) -> dict:
    value = get_prompt_config(*keys, default={})
    return value if isinstance(value, dict) else {}


def get_prompt_list(*keys: str) -> list:
    value = get_prompt_config(*keys, default=[])
    return value if isinstance(value, list) else []


def render_prompt_template(template: str, **values: Any) -> str:
    rendered = template or ""
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered.strip()
