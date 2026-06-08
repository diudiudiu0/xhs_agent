from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_SCOPES_PATH = PROJECT_ROOT / "cfg" / "skill_scopes.yaml"


@lru_cache(maxsize=1)
def load_skill_scopes() -> dict[str, Any]:
    if not SKILL_SCOPES_PATH.exists():
        return {"skills": {}}
    data = _safe_load_yaml_file(SKILL_SCOPES_PATH)
    return data if isinstance(data, dict) else {"skills": {}}


def get_skill_scope(skill_name: str, scope: str | None) -> dict[str, Any]:
    scope_name = (scope or "unrestricted").strip() or "unrestricted"
    skills = load_skill_scopes().get("skills") or {}
    skill_scopes = skills.get(skill_name) if isinstance(skills.get(skill_name), dict) else {}
    config = skill_scopes.get(scope_name)
    if not isinstance(config, dict):
        config = skill_scopes.get("unrestricted") if isinstance(skill_scopes.get("unrestricted"), dict) else {}
    result = dict(config)
    result["name"] = scope_name
    return result


def merge_action_constraints(
    skill_name: str,
    scope: str | None,
    allowed_actions: list[str] | None = None,
    forbidden_actions: list[str] | None = None,
) -> dict[str, Any]:
    config = get_skill_scope(skill_name, scope)
    merged_allowed = list(dict.fromkeys([*(config.get("allowed_actions") or []), *(allowed_actions or [])]))
    merged_forbidden = list(dict.fromkeys([*(config.get("forbidden_actions") or []), *(forbidden_actions or [])]))
    return {
        "scope": config.get("name") or (scope or "unrestricted"),
        "description": config.get("description", ""),
        "prompt_note": config.get("prompt_note", ""),
        "allowed_actions": merged_allowed,
        "forbidden_actions": merged_forbidden,
    }
