from functools import lru_cache
from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGER_CONFIG_PATH = PROJECT_ROOT / "cfg" / "manager_agent.yaml"


@lru_cache(maxsize=1)
def load_manager_config() -> dict[str, Any]:
    if not MANAGER_CONFIG_PATH.exists():
        return {}
    data = _safe_load_yaml_file(MANAGER_CONFIG_PATH)
    config = data.get("manager_agent")
    if not isinstance(config, dict):
        raise ValueError("cfg/manager_agent.yaml 中 manager_agent 必须是字典")
    return config


def manager_config_get(key: str, default: Any = None) -> Any:
    return load_manager_config().get(key, default)


def manager_config_list(key: str) -> list:
    value = manager_config_get(key, [])
    return value if isinstance(value, list) else []
