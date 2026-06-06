from __future__ import annotations

from src.manager_config import manager_config_get
from src.manager_state import ManagerState
from src.agent_worklog import XhsWorkflow
from src.memory_retriever import MemoryRetriever, load_memory_config


class ManagerMemory:
    def __init__(self, workflow: XhsWorkflow | None = None):
        self.workflow = workflow or XhsWorkflow()
        self.retriever = MemoryRetriever()

    def search(self, user_message: str) -> list[dict]:
        limit = int(manager_config_get("max_memory_hints", 4) or 4)
        config = load_memory_config()
        retrieval = config.get("retrieval") or {}
        manager_config = retrieval.get("manager_agent") if isinstance(retrieval.get("manager_agent"), dict) else {}
        return self.retriever.search(
            user_message,
            target_agent=str(manager_config.get("target_agent") or "manager_agent"),
            memory_types=[str(item) for item in manager_config.get("memory_types") or ["manager_experience", "page_path"]],
            limit=limit,
            retrieval_method=str(manager_config.get("retrieval_method") or retrieval.get("default_method") or "bm25"),
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
