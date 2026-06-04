from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class SkillSpec:
    """Description a manager agent reads before deciding to call a skill."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    executor_type: str = "tool"
    executor_agent: str = "none"
    autonomy_level: str = "low"
    manager_role: str = ""
    preconditions: list[str] = field(default_factory=list)
    memory_reads: list[str] = field(default_factory=list)
    memory_writes: list[str] = field(default_factory=list)
    risk_policy: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    side_effects: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillResult:
    """Uniform result returned by every skill."""

    success: bool
    skill_name: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    error: str = ""
    risk_level: str = "low"
    need_user_confirmation: bool = False
    next_suggestions: list[str] = field(default_factory=list)
    memory_updates: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        skill_name: str,
        message: str = "",
        data: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        observations: list[str] | None = None,
        risk_level: str = "low",
        next_suggestions: list[str] | None = None,
        memory_updates: dict[str, Any] | None = None,
    ) -> "SkillResult":
        return cls(
            success=True,
            skill_name=skill_name,
            message=message,
            data=data or {},
            artifacts=artifacts or [],
            observations=observations or [],
            risk_level=risk_level,
            next_suggestions=next_suggestions or [],
            memory_updates=memory_updates or {},
        )

    @classmethod
    def fail(
        cls,
        skill_name: str,
        error: str,
        message: str = "",
        data: dict[str, Any] | None = None,
        observations: list[str] | None = None,
        risk_level: str = "low",
    ) -> "SkillResult":
        return cls(
            success=False,
            skill_name=skill_name,
            message=message or error,
            data=data or {},
            observations=observations or [],
            error=error,
            risk_level=risk_level,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillContext:
    """Shared runtime context passed to skills.

    It lazily creates XhsAgentSkills so future manager agents can keep one
    browser/session/memory context across multiple skill calls.
    """

    xhs_skills: Any = None
    session_memory: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    operator: str = "terminal_user"

    def require_xhs_skills(self):
        if self.xhs_skills is None:
            from src.xhs_agent_skills import XhsAgentSkills

            self.xhs_skills = XhsAgentSkills()
        return self.xhs_skills

    async def close(self):
        if self.xhs_skills is not None and hasattr(self.xhs_skills, "close"):
            close_result = self.xhs_skills.close()
            if inspect.isawaitable(close_result):
                await close_result
        self.xhs_skills = None


class Skill(Protocol):
    spec: SkillSpec

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        ...


class BaseSkill:
    spec: SkillSpec

    @property
    def name(self) -> str:
        return self.spec.name

    async def run(self, context: SkillContext, args: dict[str, Any] | None = None) -> SkillResult:
        raise NotImplementedError


class SkillRegistry:
    """A small registry that keeps skill lookup and execution uniform."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        name = skill.spec.name
        if not name:
            raise ValueError("Skill name cannot be empty.")
        if name in self._skills:
            raise ValueError(f"Duplicate skill registered: {name}")
        self._skills[name] = skill
        return skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Unknown skill: {name}")
        return self._skills[name]

    def names(self) -> list[str]:
        return sorted(self._skills)

    def specs(self) -> list[dict[str, Any]]:
        return [self._skills[name].spec.to_dict() for name in self.names()]

    def render_catalog(self) -> str:
        lines = []
        for spec in self.specs():
            executor = spec.get("executor_type", "tool")
            agent = spec.get("executor_agent", "none")
            autonomy = spec.get("autonomy_level", "low")
            lines.append(f"- {spec['name']} [{spec['risk_level']}] executor={executor}/{agent} autonomy={autonomy}")
            lines.append(f"  {spec['description']}")
            if spec.get("manager_role"):
                lines.append(f"  manager_role: {spec['manager_role']}")
            if spec.get("preconditions"):
                lines.append(f"  preconditions: {', '.join(spec['preconditions'])}")
            if spec.get("memory_reads"):
                lines.append(f"  memory_reads: {', '.join(spec['memory_reads'])}")
            if spec.get("memory_writes"):
                lines.append(f"  memory_writes: {', '.join(spec['memory_writes'])}")
            if spec.get("side_effects"):
                lines.append(f"  side_effects: {', '.join(spec['side_effects'])}")
            if spec.get("tags"):
                lines.append(f"  tags: {', '.join(spec['tags'])}")
            risk_policy = spec.get("risk_policy") or {}
            confirmation_for = risk_policy.get("requires_confirmation_for") or []
            if confirmation_for:
                lines.append(f"  requires_confirmation_for: {', '.join(confirmation_for)}")
            if spec.get("examples"):
                lines.append(f"  examples: {'; '.join(spec['examples'][:3])}")
        return "\n".join(lines)

    async def run(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        context: SkillContext | None = None,
    ) -> SkillResult:
        context = context or SkillContext()
        skill = self.get(name)
        try:
            return await skill.run(context, args or {})
        except Exception as exc:
            return SkillResult.fail(
                skill_name=name,
                error=str(exc),
                risk_level=skill.spec.risk_level,
            )
