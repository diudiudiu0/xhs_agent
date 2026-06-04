from __future__ import annotations

from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_CONFIG_PATH = PROJECT_ROOT / "cfg" / "skills.yaml"


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def load_skill_config() -> dict[str, Any]:
    data = _safe_load_yaml_file(SKILL_CONFIG_PATH)
    skills = data.get("skills")
    if not isinstance(skills, dict) or not skills:
        raise ValueError("cfg/skills.yaml 中 skills 必须是非空字典。")
    return data


def get_skill_config(name: str) -> dict[str, Any]:
    skills = load_skill_config()["skills"]
    config = skills.get(name)
    if not isinstance(config, dict):
        raise KeyError(f"cfg/skills.yaml 中不存在技能配置：{name}")
    return config


def _as_mapping(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, "", "{}"):
        return {}
    raise ValueError(f"技能配置字段应为字典，但得到：{value!r}")


def _as_list(value) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, "", "[]"):
        return []
    return [value]


def build_skill_spec(name: str):
    from skills.base import SkillSpec

    config = get_skill_config(name)
    return SkillSpec(
        name=name,
        description=str(config.get("description") or ""),
        input_schema=_as_mapping(config.get("input_schema")),
        output_schema=_as_mapping(config.get("output_schema")),
        executor_type=str(config.get("executor_type") or "tool"),
        executor_agent=str(config.get("executor_agent") or "none"),
        autonomy_level=str(config.get("autonomy_level") or "low"),
        manager_role=str(config.get("manager_role") or ""),
        preconditions=_as_list(config.get("preconditions")),
        memory_reads=_as_list(config.get("memory_reads")),
        memory_writes=_as_list(config.get("memory_writes")),
        risk_policy=_as_mapping(config.get("risk_policy")),
        risk_level=str(config.get("risk_level") or "low"),
        side_effects=_as_list(config.get("side_effects")),
        tags=_as_list(config.get("tags")),
        examples=_as_list(config.get("examples")),
        requires_confirmation=bool(config.get("requires_confirmation", False)),
    )


def skill_message(name: str, key: str, default: str = "", **values) -> str:
    messages = get_skill_config(name).get("messages") or {}
    template = messages.get(key, default)
    if template is None:
        template = default
    return str(template).format_map(_SafeFormatDict(values)).strip()
