from __future__ import annotations

import re
from typing import Any

from skills.catalog import DEFAULT_SKILL_REGISTRY
from src.agent_runtime import AgentRuntimeHooks, AgentWorkflowRuntime
from src.manager_config import load_manager_config, manager_config_get, manager_config_list
from src.manager_executor import ManagerExecutor, compact_skill_result
from src.manager_memory import ManagerMemory
from src.manager_planner import ManagerPlanner
from src.manager_state import ManagerState


class ManagerAgent:
    """Goal-level brain that plans one atomic step at a time."""

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
            answer = "Action cancelled."
            self.state.complete(answer)
            return answer

        if normalized not in yes_values:
            return "Please reply y or n."

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
        runtime = AgentWorkflowRuntime(
            state=self.state,
            hooks=AgentRuntimeHooks(
                plan_next=self._plan_next_step,
                execute_skill_decision=self._handle_call_skill_decision,
                remember_success=self.memory.remember_success,
                format_skill_failure=self._skill_failure_message,
                refresh_step_memory=self.memory.search_step,
            ),
            max_steps=int(manager_config_get("max_steps_per_turn", 8) or 8),
        )
        return await runtime.run(
            goal=user_message,
            memory_hints=memory_hints,
            initial_decision=initial_decision,
        )

    def _plan_next_step(
        self,
        user_message: str,
        state: ManagerState,
        memory_hints: list[dict[str, Any]],
        last_skill_result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.planner.plan(
            user_message=user_message,
            state=state,
            memory_hints=memory_hints,
            last_skill_result=last_skill_result,
        )

    def _skill_failure_message(self, result: dict[str, Any]) -> str:
        skill_name = result.get("skill_name") or "unknown_skill"
        error = result.get("error") or result.get("message") or "skill failed"
        return f"{skill_name} failed, this turn has stopped: {error}"

    def _contains_any(self, text: str, words: list[str]) -> bool:
        return any(word and word in text for word in words)

    def _is_conditional_draft_creation_goal(self, text: str) -> bool:
        guard = manager_config_get("conditional_draft_creation_guard", {}) or {}
        if not guard.get("enabled", True):
            return False
        return (
            self._contains_any(text, guard.get("draft_keywords", []))
            and self._contains_any(text, guard.get("condition_keywords", []))
            and self._contains_any(text, guard.get("create_keywords", []))
        )

    def _needs_generated_image_workflow(self, text: str, args: dict[str, Any] | None = None) -> bool:
        guard = manager_config_get("generated_image_draft_guard", {}) or {}
        if not guard.get("enabled", True):
            return False
        combined = text + " " + " ".join(str(value) for value in (args or {}).values())
        return (
            self._contains_any(combined, guard.get("reference_image_keywords", []))
            and self._contains_any(combined, guard.get("generation_keywords", []))
            and self._contains_any(combined, guard.get("draft_keywords", []))
        )

    def _extract_title_hint(self, text: str) -> str:
        for pattern in manager_config_get("title_extract_patterns", []) or []:
            match = re.search(str(pattern), text)
            if match:
                return match.group(1).strip(" ：:，,。. \n\t")
        return ""

    def _copy_decision_fields_to_args(self, decision: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args or {})
        for key in ("sub_goal", "scope", "success_criteria", "allowed_actions", "forbidden_actions"):
            value = decision.get(key)
            if value not in (None, "", []):
                normalized.setdefault(key, value)
        return normalized

    def _normalize_skill_args(self, skill_name: str, args: dict[str, Any], user_message: str) -> dict[str, Any]:
        normalized = dict(args or {})

        if skill_name == "explore_page_task" and not (
            str(normalized.get("user_goal") or "").strip()
            or str(normalized.get("message") or "").strip()
        ):
            normalized["user_goal"] = normalized.get("sub_goal") or user_message

        if skill_name == "explore_page_task" and self._is_conditional_draft_creation_goal(user_message):
            guard = manager_config_get("conditional_draft_creation_guard", {}) or {}
            check_goal = str(
                guard.get("check_only_goal")
                or "Only inspect draft count and return the result. Do not create, upload, edit, save, delete, publish, or send anything."
            )
            normalized["user_goal"] = check_goal
            normalized["sub_goal"] = check_goal
            normalized["scope"] = "read_only"
            normalized["success_criteria"] = normalized.get("success_criteria") or "Return draft_count or clearly report that it cannot be recognized."
            normalized["parent_user_goal"] = user_message
            normalized["max_steps"] = min(int(normalized.get("max_steps") or 6), 6)

        if skill_name == "create_generated_note_draft":
            if not str(normalized.get("title") or "").strip():
                title = self._extract_title_hint(user_message)
                if title:
                    normalized["title"] = title
            if not str(normalized.get("user_goal") or "").strip():
                normalized["user_goal"] = user_message
            normalized.setdefault("scope", "workflow")
            normalized.setdefault("sub_goal", "Generate prompts, generate images, write note text, and create a draft.")
            normalized.setdefault("success_criteria", "Generated images exist and draft_saved is true.")

        return normalized

    def _normalize_skill_choice(
        self,
        skill_name: str,
        args: dict[str, Any],
        user_message: str,
    ) -> tuple[str, dict[str, Any]]:
        normalized_args = dict(args or {})
        if skill_name == "create_note_draft" and self._needs_generated_image_workflow(user_message, normalized_args):
            session_images = self.state.session_memory.get("generated_images") or []
            explicit_images = normalized_args.get("image_files") or []
            allow_default = bool(normalized_args.get("allow_default_media", False))
            if not session_images and not explicit_images and not allow_default:
                skill_name = "create_generated_note_draft"
        return skill_name, self._normalize_skill_args(skill_name, normalized_args, user_message)

    async def _handle_call_skill_decision(
        self,
        decision: dict[str, Any],
        user_message: str,
        memory_hints: list[dict[str, Any]],
    ) -> str | None:
        raw_skill_name = decision.get("skill_name") or ""
        raw_args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        raw_args = self._copy_decision_fields_to_args(decision, raw_args)
        skill_name, args = self._normalize_skill_choice(raw_skill_name, raw_args, user_message)
        step = self.state.add_step(
            decision_type="call_skill",
            skill_name=skill_name,
            sub_goal=str(args.get("sub_goal") or decision.get("sub_goal") or args.get("user_goal") or ""),
            scope=str(args.get("scope") or decision.get("scope") or ""),
            success_criteria=str(args.get("success_criteria") or decision.get("success_criteria") or ""),
            allowed_actions=[str(item) for item in (args.get("allowed_actions") or decision.get("allowed_actions") or [])],
            forbidden_actions=[str(item) for item in (args.get("forbidden_actions") or decision.get("forbidden_actions") or [])],
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
            answer = self._skill_failure_message(result)
            self.state.fail(answer)
            return answer

        spec = DEFAULT_SKILL_REGISTRY.get(skill_name).spec
        if decision.get("requires_user_confirmation") or spec.requires_confirmation:
            confirmation_message = (
                decision.get("confirmation_message")
                or decision.get("reason")
                or f"Confirm running skill: {skill_name}"
            )
            self.state.set_pending_confirmation(
                {
                    "decision": decision,
                    "user_message": user_message,
                    "memory_hints": memory_hints,
                    "step_index": step.index,
                }
            )
            return f"{confirmation_message}\nReply y to continue, or n to cancel."

        self.state.update_step_result(step, "in_progress", {"message": "running"})
        result = await self.executor.execute_skill(skill_name, args, self.state)
        compact = compact_skill_result(result)
        if result.get("success"):
            self.state.update_step_result(step, "completed", compact)
            return None

        self.state.update_step_result(step, "failed", compact)
        answer = self._skill_failure_message(compact)
        self.state.fail(answer)
        return answer

    async def close(self):
        await self.executor.close()
