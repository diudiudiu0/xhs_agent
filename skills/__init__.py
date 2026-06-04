"""Standard skill package for the XHS account-management agent.

This package is the orchestration-facing layer. It wraps existing
src modules into stable, typed skills that an account manager
agent can inspect and call.
"""

from skills.base import SkillContext, SkillRegistry, SkillResult, SkillSpec
from skills.catalog import DEFAULT_SKILL_REGISTRY, build_default_registry, run_skill

__all__ = [
    "SkillContext",
    "SkillRegistry",
    "SkillResult",
    "SkillSpec",
    "DEFAULT_SKILL_REGISTRY",
    "build_default_registry",
    "run_skill",
]
