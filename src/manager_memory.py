from __future__ import annotations

import json
from typing import Any

from src.manager_config import manager_config_get
from src.manager_state import ManagerState
from src.agent_worklog import XhsWorkflow
from src.memory_retriever import MemoryRetriever, load_memory_config


class ManagerMemory:
    def __init__(self, workflow: XhsWorkflow | None = None):
        self.workflow = workflow or XhsWorkflow()
        self.retriever = MemoryRetriever()

    def search(self, user_message: str) -> list[dict]:
        return self._search(user_message)

    def _search(self, query: str) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []
        limit = int(manager_config_get("max_memory_hints", 4) or 4)
        config = load_memory_config()
        retrieval = config.get("retrieval") or {}
        manager_config = retrieval.get("manager_agent") if isinstance(retrieval.get("manager_agent"), dict) else {}
        return self.retriever.search(
            query,
            target_agent=str(manager_config.get("target_agent") or "manager_agent"),
            memory_types=[str(item) for item in manager_config.get("memory_types") or ["manager_experience", "page_path"]],
            limit=limit,
            retrieval_method=str(manager_config.get("retrieval_method") or retrieval.get("default_method") or "bm25"),
        )

    def search_step(
        self,
        user_message: str,
        state: ManagerState,
        goal_memory_hints: list[dict[str, Any]] | None = None,
        last_observation: dict[str, Any] | None = None,
    ) -> list[dict]:
        query = self._build_step_memory_query(user_message, state, last_observation or {})
        return self._search(query)

    def _build_step_memory_query(
        self,
        user_message: str,
        state: ManagerState,
        last_observation: dict[str, Any],
    ) -> str:
        if not last_observation:
            return user_message

        compact_observation = {
            "skill_name": last_observation.get("skill_name", ""),
            "success": last_observation.get("success"),
            "message": last_observation.get("message", ""),
            "error": last_observation.get("error", ""),
            "observations": (last_observation.get("observations") or [])[:4],
            "artifacts": (last_observation.get("artifacts") or [])[:4],
        }
        active_steps = [
            {
                "index": step.index,
                "decision_type": step.decision_type,
                "skill_name": step.skill_name,
                "sub_goal": step.sub_goal,
                "status": step.status,
            }
            for step in state.steps[-2:]
            if step.status in {"planned", "in_progress", "failed"}
        ]
        return "\n".join(
            [
                "current_step_memory_query",
                f"goal: {user_message}",
                "last_observation:",
                json.dumps(compact_observation, ensure_ascii=False),
                "active_unfinished_or_failed_steps:",
                json.dumps(active_steps, ensure_ascii=False),
                "retrieve memory useful for deciding the next single step only.",
            ]
        )

    def remember_success(self, state: ManagerState):
        if state.status != "completed" or not state.final_answer:
            return
        raw_steps = []
        for step in state.steps:
            if step.status != "completed":
                continue
            raw_steps.append(
                {
                    "action": step.skill_name or step.decision_type,
                    "element_text": step.reason,
                    "result": step.result.get("message") or step.result.get("error") or "",
                    "observation": "; ".join(str(item) for item in step.result.get("observations", [])[:3]),
                    "page_url": "",
                }
            )
        self.workflow.remember_experience(
            user_request=state.current_goal,
            result=state.final_answer,
            raw_steps=raw_steps,
        )
