import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
    def __init__(self, fail_skills=None):
        self.calls = []
        self.fail_skills = set(fail_skills or [])

    def skill_exists(self, name):
        return name in {
            "generate_images",
            "explore_page_task",
            "create_note_draft",
            "create_generated_note_draft",
        }

    async def execute_skill(self, skill_name, args, state):
        self.calls.append((skill_name, args))
        if skill_name in self.fail_skills:
            return {
                "success": False,
                "skill_name": skill_name,
                "message": f"failed {skill_name}",
                "error": f"failed {skill_name}",
            }
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

    def search_step(self, user_message, state, goal_memory_hints=None, last_observation=None):
        return [{"user_request": "step request", "summary": "step path"}]

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


async def test_failed_skill_stops_turn():
    planner = FakePlanner(
        [
            {
                "type": "call_skill",
                "skill_name": "explore_page_task",
                "args": {"user_goal": "check drafts"},
                "reason": "check prerequisite",
            },
            {
                "type": "call_skill",
                "skill_name": "generate_images",
                "args": {"prompts": ["should not run"]},
                "reason": "should not continue after failed prerequisite",
            },
        ]
    )
    executor = FakeExecutor(fail_skills={"explore_page_task"})
    memory = FakeMemory()
    agent = ManagerAgent(planner=planner, executor=executor, memory=memory, state=ManagerState())
    answer = await agent.handle_user_message("check drafts then create if empty")
    if "explore_page_task" not in answer or "failed" not in answer:
        raise AssertionError(answer)
    if executor.calls != [("explore_page_task", {"user_goal": "check drafts"})]:
        raise AssertionError(executor.calls)
    if agent.state.status != "failed":
        raise AssertionError(agent.state.status)
    if memory.remembered:
        raise AssertionError(memory.remembered)


async def test_conditional_draft_check_is_read_only():
    planner = FakePlanner(
        [
            {
                "type": "call_skill",
                "skill_name": "explore_page_task",
                "args": {},
                "reason": "use page explorer",
            },
            {
                "type": "final_answer",
                "message": "done",
                "reason": "goal complete",
            },
        ]
    )
    executor = FakeExecutor()
    agent = ManagerAgent(planner=planner, executor=executor, memory=FakeMemory(), state=ManagerState())
    await agent.handle_user_message("查看草稿箱，如果为空就创建草稿")
    if len(executor.calls) != 1 or executor.calls[0][0] != "explore_page_task":
        raise AssertionError(executor.calls)
    args = executor.calls[0][1]
    if args.get("parent_user_goal") != "查看草稿箱，如果为空就创建草稿":
        raise AssertionError(executor.calls)
    if "只查看草稿箱" not in args.get("user_goal", ""):
        raise AssertionError(executor.calls)
    step = agent.state.steps[0]
    if step.scope != "read_only":
        raise AssertionError(step.to_dict())
    if "只查看草稿箱" not in step.sub_goal:
        raise AssertionError(step.to_dict())


async def test_generated_image_draft_redirects_from_raw_draft_skill():
    planner = FakePlanner(
        [
            {
                "type": "call_skill",
                "skill_name": "create_note_draft",
                "args": {},
                "reason": "wrong low level choice",
            },
            {
                "type": "final_answer",
                "message": "done",
                "reason": "goal complete",
            },
        ]
    )
    executor = FakeExecutor()
    agent = ManagerAgent(planner=planner, executor=executor, memory=FakeMemory(), state=ManagerState())
    await agent.handle_user_message("使用默认路径下的图片去生成帖子所需的提示词，并根据提示词生成图片后创建草稿，标题为:家居脚轮推荐")
    if len(executor.calls) != 1 or executor.calls[0][0] != "create_generated_note_draft":
        raise AssertionError(executor.calls)
    args = executor.calls[0][1]
    if args.get("title") != "家居脚轮推荐":
        raise AssertionError(executor.calls)
    step = agent.state.steps[0]
    if step.skill_name != "create_generated_note_draft" or step.scope != "workflow":
        raise AssertionError(step.to_dict())


async def main():
    await test_call_skill_then_final()
    await test_confirmation_flow()
    await test_failed_skill_stops_turn()
    await test_conditional_draft_check_is_read_only()
    await test_generated_image_draft_redirects_from_raw_draft_skill()
    print("manager agent check passed")


if __name__ == "__main__":
    asyncio.run(main())
