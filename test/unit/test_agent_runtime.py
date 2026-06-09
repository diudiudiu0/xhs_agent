import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_runtime import AgentRuntimeHooks, AgentWorkflowRuntime
from src.manager_state import ManagerState


class RuntimeHarness:
    def __init__(self, state: ManagerState):
        self.state = state
        self.decisions = [
            {"type": "call_skill", "skill_name": "demo_skill", "args": {"value": 1}, "reason": "need data"},
            {"type": "final_answer", "message": "done", "reason": "goal complete"},
        ]
        self.executed = []
        self.remembered = []
        self.step_memory_queries = []

    def plan_next(self, goal, state, memory_hints, last_observation):
        scopes = [item.get("memory_scope") for item in memory_hints]
        if scopes != ["goal", "current_step"]:
            raise AssertionError(memory_hints)
        if last_observation:
            if last_observation.get("skill_name") != "demo_skill":
                raise AssertionError(last_observation)
        return self.decisions.pop(0)

    async def execute_skill_decision(self, decision, goal, memory_hints):
        self.executed.append((goal, decision.get("skill_name"), decision.get("args")))
        step = self.state.add_step(
            decision_type="call_skill",
            skill_name=decision.get("skill_name", ""),
            sub_goal="run demo skill",
            reason=decision.get("reason", ""),
            status="in_progress",
        )
        self.state.update_step_result(
            step,
            "completed",
            {"success": True, "skill_name": "demo_skill", "message": "observed demo"},
        )
        return None

    def remember_success(self, manager_state):
        self.remembered.append(manager_state.current_goal)

    def format_skill_failure(self, result):
        return str(result.get("error") or "failed")

    def refresh_step_memory(self, goal, state, goal_memory_hints, last_observation):
        self.step_memory_queries.append(
            {
                "goal": goal,
                "last_skill": last_observation.get("skill_name", ""),
                "goal_memory_count": len(goal_memory_hints),
            }
        )
        return [{"user_request": f"step memory {len(self.step_memory_queries)}"}]


async def main_async():
    state = ManagerState()
    state.reset_for_user_goal("test goal")
    harness = RuntimeHarness(state)
    runtime = AgentWorkflowRuntime(
        state=state,
        hooks=AgentRuntimeHooks(
            plan_next=harness.plan_next,
            execute_skill_decision=harness.execute_skill_decision,
            remember_success=harness.remember_success,
            format_skill_failure=harness.format_skill_failure,
            refresh_step_memory=harness.refresh_step_memory,
        ),
        max_steps=3,
    )
    answer = await runtime.run("test goal", memory_hints=[{"user_request": "goal memory"}])
    if answer != "done":
        raise AssertionError(answer)
    if harness.executed != [("test goal", "demo_skill", {"value": 1})]:
        raise AssertionError(harness.executed)
    if harness.remembered != ["test goal"]:
        raise AssertionError(harness.remembered)
    if harness.step_memory_queries != [
        {"goal": "test goal", "last_skill": "", "goal_memory_count": 1},
        {"goal": "test goal", "last_skill": "demo_skill", "goal_memory_count": 1},
    ]:
        raise AssertionError(harness.step_memory_queries)
    if [step.decision_type for step in state.steps] != ["call_skill", "final_answer"]:
        raise AssertionError(state.to_dict())


def main():
    asyncio.run(main_async())
    print("agent runtime check passed")


if __name__ == "__main__":
    main()
