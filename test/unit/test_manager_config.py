import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cfg.model_config import MANAGER_MODEL_CONFIG
from src.manager_config import load_manager_config
from src.manager_planner import ManagerPlanner
from src.manager_state import ManagerState


def main():
    if MANAGER_MODEL_CONFIG.get("model") != "deepseek-v4-pro":
        raise AssertionError("manager brain model should be deepseek-v4-pro")

    config = load_manager_config()
    for key in ("system_prompt", "planner_prompt_template", "decision_schema", "behavior_rules"):
        if not config.get(key):
            raise AssertionError(f"manager config missing {key}")

    planner = ManagerPlanner()
    state = ManagerState(current_goal="测试目标", status="planning")
    prompt = planner.build_prompt(
        user_message="帮我查看主页评论",
        state=state,
        memory_hints=[{"user_request": "查看评论", "summary": "use web site"}],
    )
    for text in ("可用 skill catalog", "explore_page_task", "Manager Agent"):
        if text not in prompt:
            raise AssertionError(f"manager prompt missing {text}")

    print("manager config check passed")


if __name__ == "__main__":
    main()
