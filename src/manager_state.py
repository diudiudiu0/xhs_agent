from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class ManagerStep:
    index: int
    decision_type: str
    skill_name: str = ""
    sub_goal: str = ""
    scope: str = ""
    success_criteria: str = ""
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    status: str = "planned"
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManagerState:
    current_goal: str = ""
    status: str = "idle"
    steps: list[ManagerStep] = field(default_factory=list)
    memory_lookups: list[dict[str, Any]] = field(default_factory=list)
    session_memory: dict[str, Any] = field(default_factory=dict)
    last_skill_result: dict[str, Any] = field(default_factory=dict)
    pending_confirmation: dict[str, Any] | None = None
    final_answer: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def reset_for_user_goal(self, user_message: str):
        self.current_goal = user_message
        self.status = "planning"
        self.steps = []
        self.memory_lookups = []
        self.last_skill_result = {}
        self.pending_confirmation = None
        self.final_answer = ""
        self.created_at = _now()
        self.updated_at = self.created_at

    def add_step(
        self,
        decision_type: str,
        skill_name: str = "",
        sub_goal: str = "",
        scope: str = "",
        success_criteria: str = "",
        allowed_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        args: dict[str, Any] | None = None,
        reason: str = "",
        status: str = "planned",
    ) -> ManagerStep:
        step = ManagerStep(
            index=len(self.steps) + 1,
            decision_type=decision_type,
            skill_name=skill_name,
            sub_goal=sub_goal,
            scope=scope,
            success_criteria=success_criteria,
            allowed_actions=allowed_actions or [],
            forbidden_actions=forbidden_actions or [],
            args=args or {},
            reason=reason,
            status=status,
        )
        self.steps.append(step)
        self.updated_at = _now()
        return step

    def update_step_result(self, step: ManagerStep, status: str, result: dict[str, Any]):
        step.status = status
        step.result = result
        self.last_skill_result = result
        self.updated_at = _now()

    def record_memory_lookup(
        self,
        planning_round: int,
        memory_hints: list[dict[str, Any]],
        last_observation: dict[str, Any] | None = None,
    ):
        grouped: dict[str, list[dict[str, Any]]] = {"goal": [], "current_step": []}
        for item in memory_hints or []:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("memory_scope") or "unknown")
            grouped.setdefault(scope, [])
            grouped[scope].append(
                {
                    "memory_id": item.get("memory_id", ""),
                    "memory_type": item.get("memory_type", ""),
                    "retrieval_method": item.get("retrieval_method", ""),
                    "match_score": item.get("match_score"),
                    "reuse_level": item.get("reuse_level", ""),
                    "site": item.get("site", ""),
                    "task_type": item.get("task_type", ""),
                    "user_request": item.get("user_request", ""),
                    "summary": str(item.get("summary") or "")[:420],
                    "result": str(item.get("result") or "")[:300],
                }
            )
        observation = last_observation or {}
        self.memory_lookups.append(
            {
                "planning_round": planning_round,
                "created_at": _now(),
                "last_skill_name": observation.get("skill_name", ""),
                "last_success": observation.get("success"),
                "goal": grouped.get("goal", []),
                "current_step": grouped.get("current_step", []),
            }
        )
        self.memory_lookups = self.memory_lookups[-30:]
        self.updated_at = _now()

    def apply_memory_updates(self, updates: dict[str, Any] | None):
        if not updates:
            return
        self.session_memory.update(updates)
        self.updated_at = _now()

    def set_pending_confirmation(self, decision: dict[str, Any]):
        self.pending_confirmation = decision
        self.status = "waiting_user_confirmation"
        self.updated_at = _now()

    def clear_pending_confirmation(self):
        self.pending_confirmation = None
        self.updated_at = _now()

    def complete(self, answer: str):
        self.status = "completed"
        self.final_answer = answer
        self.updated_at = _now()

    def fail(self, answer: str):
        self.status = "failed"
        self.final_answer = answer
        self.updated_at = _now()

    def to_dict(self, recent_steps: int | None = None) -> dict[str, Any]:
        steps = self.steps[-recent_steps:] if recent_steps else self.steps
        return {
            "current_goal": self.current_goal,
            "status": self.status,
            "steps": [step.to_dict() for step in steps],
            "memory_lookups": self.memory_lookups[-30:],
            "session_memory": self.session_memory,
            "last_skill_result": self.last_skill_result,
            "pending_confirmation": self.pending_confirmation,
            "final_answer": self.final_answer,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
