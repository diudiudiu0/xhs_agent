from __future__ import annotations

import asyncio
from typing import Any

from skills.catalog import DEFAULT_SKILL_REGISTRY
from src.manager_config import load_manager_config, manager_config_get, manager_config_list
from src.manager_executor import ManagerExecutor, compact_skill_result
from src.manager_memory import ManagerMemory
from src.manager_planner import ManagerPlanner
from src.manager_state import ManagerState


class ManagerAgent:
    def __init__(
        self,
        planner: ManagerPlanner | None = None,
        executor: ManagerExecutor | None = None,
        memory: ManagerMemory | None = None,
        state: ManagerState | None = None,
    ):
        self.config = load_manager_config()
        self.planner = planner or ManagerPlanner()
        self.executor = executor or ManagerExecutor()
        self.memory = memory or ManagerMemory()
        self.state = state or ManagerState()

    async def handle_user_message(self, user_message: str) -> str:
        user_message = (user_message or "").strip()
        if not user_message:
            return ""

        if self.state.pending_confirmation:
            return await self._handle_confirmation_reply(user_message)

        self.state.reset_for_user_goal(user_message)
        memory_hints = self.memory.search(user_message)
        return await self._run_planning_loop(user_message, memory_hints)

    async def _handle_confirmation_reply(self, user_message: str) -> str:
        normalized = user_message.strip().lower()
        yes_values = {str(item).lower() for item in manager_config_list("confirmation_yes_values")}
        no_values = {str(item).lower() for item in manager_config_list("confirmation_no_values")}

        if normalized in no_values:
            self.state.clear_pending_confirmation()
            answer = "已取消该动作。"
            self.state.complete(answer)
            return answer

        if normalized not in yes_values:
            return "请回复 y 或 n。"

        pending = self.state.pending_confirmation or {}
        decision = dict(pending.get("decision") or {})
        decision["requires_user_confirmation"] = False
        decision["confirmation_message"] = ""
        user_goal = pending.get("user_message") or self.state.current_goal
        memory_hints = pending.get("memory_hints") or []
        self.state.clear_pending_confirmation()
        self.state.status = "planning"
        return await self._run_planning_loop(user_goal, memory_hints, initial_decision=decision)

    async def _run_planning_loop(
        self,
        user_message: str,
        memory_hints: list[dict[str, Any]],
        initial_decision: dict[str, Any] | None = None,
    ) -> str:
        max_steps = int(manager_config_get("max_steps_per_turn", 8) or 8)
        last_result = self.state.last_skill_result or {}
        decision = initial_decision

        for _ in range(max_steps):
            if decision is None:
                decision = self.planner.plan(
                    user_message=user_message,
                    state=self.state,
                    memory_hints=memory_hints,
                    last_skill_result=last_result,
                )

            decision_type = decision.get("type")
            if decision_type == "call_skill":
                answer = await self._handle_call_skill_decision(decision, user_message, memory_hints)
                if answer is not None:
                    return answer
                last_result = self.state.last_skill_result
                decision = None
                continue

            if decision_type == "ask_user":
                step = self.state.add_step(
                    decision_type="ask_user",
                    reason=decision.get("reason", ""),
                    status="waiting_user",
                )
                message = decision.get("message") or ""
                self.state.update_step_result(step, "completed", {"message": message})
                self.state.status = "waiting_user_input"
                return message

            if decision_type == "wait":
                step = self.state.add_step(
                    decision_type="wait",
                    reason=decision.get("reason", ""),
                    status="in_progress",
                )
                await asyncio.sleep(float(decision.get("seconds") or 1))
                self.state.update_step_result(step, "completed", {"message": "waited"})
                decision = None
                continue

            message = decision.get("message") or str(manager_config_get("fallback_final_answer", ""))
            step = self.state.add_step(
                decision_type="final_answer",
                reason=decision.get("reason", ""),
                status="completed",
            )
            self.state.update_step_result(step, "completed", {"message": message})
            self.state.complete(message)
            self.memory.remember_success(self.state)
            return message

        answer = "本轮任务达到 manager 最大规划步数，已停止继续执行。"
        self.state.fail(answer)
        return answer

    async def _handle_call_skill_decision(
        self,
        decision: dict[str, Any],
        user_message: str,
        memory_hints: list[dict[str, Any]],
    ) -> str | None:
        skill_name = decision.get("skill_name") or ""
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        step = self.state.add_step(
            decision_type="call_skill",
            skill_name=skill_name,
            args=args,
            reason=decision.get("reason", ""),
            status="planned",
        )

        if not self.executor.skill_exists(skill_name):
            result = {
                "success": False,
                "skill_name": skill_name,
                "message": f"unknown skill: {skill_name}",
                "error": f"unknown skill: {skill_name}",
            }
            self.state.update_step_result(step, "failed", result)
            return None

        spec = DEFAULT_SKILL_REGISTRY.get(skill_name).spec
        if decision.get("requires_user_confirmation") or spec.requires_confirmation:
            confirmation_message = (
                decision.get("confirmation_message")
                or decision.get("reason")
                or f"确认执行 skill: {skill_name}"
            )
            self.state.set_pending_confirmation(
                {
                    "decision": decision,
                    "user_message": user_message,
                    "memory_hints": memory_hints,
                    "step_index": step.index,
                }
            )
            return f"{confirmation_message}\n确认继续请输入 y，取消请输入 n。"

        self.state.update_step_result(step, "in_progress", {"message": "running"})
        result = await self.executor.execute_skill(skill_name, args, self.state)
        compact = compact_skill_result(result)
        self.state.update_step_result(step, "completed" if result.get("success") else "failed", compact)
        return None

    async def close(self):
        await self.executor.close()
