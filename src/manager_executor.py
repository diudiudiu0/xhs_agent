from __future__ import annotations

from typing import Any

from skills.base import SkillContext, SkillResult
from skills.catalog import DEFAULT_SKILL_REGISTRY
from src.manager_state import ManagerState


class ManagerExecutor:
    def __init__(self, context: SkillContext | None = None):
        self.context = context or SkillContext()

    def skill_exists(self, name: str) -> bool:
        return name in DEFAULT_SKILL_REGISTRY.names()

    async def execute_skill(self, skill_name: str, args: dict[str, Any] | None, state: ManagerState) -> dict[str, Any]:
        if not self.skill_exists(skill_name):
            return {
                "success": False,
                "skill_name": skill_name,
                "message": f"unknown skill: {skill_name}",
                "error": f"unknown skill: {skill_name}",
            }

        merged_args = dict(args or {})
        if "session_memory" not in merged_args:
            merged_args["session_memory"] = state.session_memory

        result: SkillResult = await DEFAULT_SKILL_REGISTRY.run(
            skill_name,
            args=merged_args,
            context=self.context,
        )
        if result.memory_updates:
            self.context.session_memory.update(result.memory_updates)
            state.apply_memory_updates(result.memory_updates)
        return result.to_dict()

    async def close(self):
        await self.context.close()


def compact_skill_result(result: dict[str, Any], limit: int = 1400) -> dict[str, Any]:
    compact = {}
    for key, value in result.items():
        if key in {"data", "observations", "message", "error", "artifacts", "success", "skill_name"}:
            compact[key] = value
    text = str(compact)
    if len(text) <= limit:
        return compact
    compact["data"] = str(compact.get("data", ""))[:500]
    compact["observations"] = [str(item)[:220] for item in compact.get("observations", [])[:4]]
    compact["message"] = str(compact.get("message", ""))[:300]
    compact["error"] = str(compact.get("error", ""))[:300]
    return compact
