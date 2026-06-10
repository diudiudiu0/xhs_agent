from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.manager_config import manager_config_get
from src.manager_state import ManagerState


PlanNext = Callable[[str, ManagerState, list[dict[str, Any]], dict[str, Any]], dict[str, Any]]
ExecuteSkillDecision = Callable[[dict[str, Any], str, list[dict[str, Any]]], Awaitable[str | None]]
RememberSuccess = Callable[[ManagerState], None]
FormatSkillFailure = Callable[[dict[str, Any]], str]
RefreshStepMemory = Callable[[str, ManagerState, list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]


@dataclass
class AgentRuntimeHooks:
    plan_next: PlanNext
    execute_skill_decision: ExecuteSkillDecision
    remember_success: RememberSuccess
    format_skill_failure: FormatSkillFailure
    refresh_step_memory: RefreshStepMemory | None = None


class AgentWorkflowRuntime:
    """Generic Goal -> Plan -> Step -> Skill -> Observation -> Memory loop."""

    def __init__(
        self,
        state: ManagerState,
        hooks: AgentRuntimeHooks,
        max_steps: int | None = None,
    ):
        self.state = state
        self.hooks = hooks
        self.max_steps = int(max_steps or manager_config_get("max_steps_per_turn", 8) or 8)

    async def run(
        self,
        goal: str,
        memory_hints: list[dict[str, Any]],
        initial_decision: dict[str, Any] | None = None,
    ) -> str:
        last_observation = self.state.last_skill_result or {}
        decision = initial_decision
        goal_memory_hints = list(memory_hints or [])
        planner_memory_hints = self._scope_memory_hints(goal_memory_hints, "goal")

        for planning_round in range(1, self.max_steps + 1):
            if decision is None:
                planner_memory_hints = self._build_planner_memory(goal, goal_memory_hints, last_observation)
                self.state.record_memory_lookup(planning_round, planner_memory_hints, last_observation)
                decision = self._plan(goal, planner_memory_hints, last_observation)

            decision_type = decision.get("type")
            if decision_type == "call_skill":
                answer = await self._execute_skill_step(decision, goal, planner_memory_hints)
                if answer is not None:
                    return answer
                last_observation = self._observe()
                decision = None
                continue

            if decision_type == "ask_user":
                return self._ask_user(decision)

            if decision_type == "wait":
                await self._wait(decision)
                decision = None
                continue

            return self._finish(decision, last_observation)

        answer = "This turn reached the manager step limit and stopped."
        self.state.fail(answer)
        return answer

    def _plan(
        self,
        goal: str,
        memory_hints: list[dict[str, Any]],
        last_observation: dict[str, Any],
    ) -> dict[str, Any]:
        return self.hooks.plan_next(goal, self.state, memory_hints, last_observation)

    def _build_planner_memory(
        self,
        goal: str,
        goal_memory_hints: list[dict[str, Any]],
        last_observation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        step_memory_hints = []
        if self.hooks.refresh_step_memory:
            step_memory_hints = self.hooks.refresh_step_memory(
                goal,
                self.state,
                goal_memory_hints,
                last_observation,
            )
        return [
            *self._scope_memory_hints(goal_memory_hints, "goal"),
            *self._scope_memory_hints(step_memory_hints, "current_step"),
        ]

    def _scope_memory_hints(self, memory_hints: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
        scoped = []
        for item in memory_hints or []:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized["memory_scope"] = scope
            scoped.append(normalized)
        return scoped

    async def _execute_skill_step(
        self,
        decision: dict[str, Any],
        goal: str,
        memory_hints: list[dict[str, Any]],
    ) -> str | None:
        return await self.hooks.execute_skill_decision(decision, goal, memory_hints)

    def _observe(self) -> dict[str, Any]:
        return self.state.last_skill_result or {}

    def _ask_user(self, decision: dict[str, Any]) -> str:
        step = self.state.add_step(
            decision_type="ask_user",
            sub_goal=decision.get("message", ""),
            reason=decision.get("reason", ""),
            status="waiting_user",
        )
        message = decision.get("message") or ""
        self.state.update_step_result(step, "completed", {"message": message})
        self.state.status = "waiting_user_input"
        return message

    async def _wait(self, decision: dict[str, Any]):
        step = self.state.add_step(
            decision_type="wait",
            sub_goal=decision.get("reason", ""),
            reason=decision.get("reason", ""),
            status="in_progress",
        )
        await asyncio.sleep(float(decision.get("seconds") or 1))
        self.state.update_step_result(step, "completed", {"message": "waited"})

    def _finish(self, decision: dict[str, Any], last_observation: dict[str, Any]) -> str:
        message = decision.get("message") or str(manager_config_get("fallback_final_answer", ""))
        step = self.state.add_step(
            decision_type="final_answer",
            sub_goal="answer user",
            success_criteria="final answer returned",
            reason=decision.get("reason", ""),
            status="completed",
        )
        if last_observation and last_observation.get("success") is False:
            error_message = self.hooks.format_skill_failure(last_observation)
            self.state.update_step_result(step, "failed", {"message": error_message})
            self.state.fail(error_message)
            return error_message

        self.state.update_step_result(step, "completed", {"message": message})
        self.state.complete(message)
        self._remember()
        return message

    def _remember(self):
        self.hooks.remember_success(self.state)
