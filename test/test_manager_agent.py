import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.manager_agent import ManagerAgent
from src.manager_state import ManagerState


class FakePlanner:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def plan(self, user_message, state, memory_hints=None, last_skill_result=None):
        if not self.decisions:
            return {"type": "final_answer", "message": "done", "reason": "empty"}
        return self.decisions.pop(0)


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def skill_exists(self, name):
        return name in {"generate_images", "explore_page_task"}

    async def execute_skill(self, skill_name, args, state):
        self.calls.append((skill_name, args))
        state.apply_memory_updates({"last_called_skill": skill_name})
        return {
            "success": True,
            "skill_name": skill_name,
            "message": f"ran {skill_name}",
            "data": {"args": args},
            "observations": [f"observed {skill_name}"],
            "memory_updates": {"last_called_skill": skill_name},
        }

    async def close(self):
        return None


class FakeMemory:
    def __init__(self):
        self.remembered = []

    def search(self, user_message):
        return [{"user_request": "old request", "summary": "old path"}]

    def remember_success(self, state):
        self.remembered.append(state.current_goal)


async def test_call_skill_then_final():
    planner = FakePlanner(
        [
            {
                "type": "call_skill",
                "skill_name": "generate_images",
                "args": {"prompts": ["test prompt"]},
                "reason": "need images",
            },
            {
                "type": "final_answer",
                "message": "图片已生成",
                "reason": "goal complete",
            },
        ]
    )
    executor = FakeExecutor()
    memory = FakeMemory()
    agent = ManagerAgent(planner=planner, executor=executor, memory=memory, state=ManagerState())
    answer = await agent.handle_user_message("生成图片")
    if answer != "图片已生成":
        raise AssertionError(answer)
    if executor.calls != [("generate_images", {"prompts": ["test prompt"]})]:
        raise AssertionError(executor.calls)
    if agent.state.session_memory.get("last_called_skill") != "generate_images":
        raise AssertionError(agent.state.session_memory)
    if memory.remembered != ["生成图片"]:
        raise AssertionError(memory.remembered)


async def test_confirmation_flow():
    planner = FakePlanner(
        [
            {
                "type": "call_skill",
                "skill_name": "explore_page_task",
                "args": {"user_goal": "删除草稿"},
                "requires_user_confirmation": True,
                "confirmation_message": "确认删除草稿吗",
                "reason": "destructive",
            },
            {
                "type": "final_answer",
                "message": "已处理",
                "reason": "done",
            },
        ]
    )
    executor = FakeExecutor()
    agent = ManagerAgent(planner=planner, executor=executor, memory=FakeMemory(), state=ManagerState())
    first = await agent.handle_user_message("删除草稿")
    if "确认删除草稿吗" not in first:
        raise AssertionError(first)
    second = await agent.handle_user_message("y")
    if second != "已处理":
        raise AssertionError(second)
    if not executor.calls:
        raise AssertionError("expected skill call after confirmation")


async def main():
    await test_call_skill_then_final()
    await test_confirmation_flow()
    print("manager agent check passed")


if __name__ == "__main__":
    asyncio.run(main())
