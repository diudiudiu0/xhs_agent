from __future__ import annotations

from src.manager_config import manager_config_get
from src.manager_state import ManagerState
from src.agent_worklog import XhsWorkflow


class ManagerMemory:
    def __init__(self, workflow: XhsWorkflow | None = None):
        self.workflow = workflow or XhsWorkflow()

    def search(self, user_message: str) -> list[dict]:
        limit = int(manager_config_get("max_memory_hints", 4) or 4)
        return self.workflow.search_experiences(user_message, limit=limit)

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
