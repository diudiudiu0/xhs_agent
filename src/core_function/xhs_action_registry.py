from pathlib import Path

from src.core_function.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_ACTION_CONFIG_PATH = PROJECT_ROOT / "cfg" / "terminal_actions.yaml"


def load_terminal_actions() -> list[dict]:
    """Load the terminal-agent action catalog from cfg/terminal_actions.yaml."""
    data = _safe_load_yaml_file(TERMINAL_ACTION_CONFIG_PATH)
    actions = data.get("terminal_actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("cfg/terminal_actions.yaml 中 terminal_actions 必须是非空列表")

    normalized = []
    for index, item in enumerate(actions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"cfg/terminal_actions.yaml 第 {index} 个 action 不是字典")
        action = str(item.get("action") or "").strip()
        if not action:
            raise ValueError(f"cfg/terminal_actions.yaml 第 {index} 个 action 缺少 action 名称")
        item = dict(item)
        item["action"] = action
        item.setdefault("description", "")
        item.setdefault("args", {})
        item.setdefault("choose_when", [])
        item.setdefault("do_not_choose_when", [])
        item.setdefault("examples", [])
        normalized.append(item)
    return normalized


TERMINAL_ACTIONS = load_terminal_actions()
VALID_ACTION_NAMES = {item["action"] for item in TERMINAL_ACTIONS}


def render_action_catalog() -> str:
    def join_values(values) -> str:
        if not isinstance(values, list):
            return str(values or "")
        return "；".join(str(value) for value in values)

    lines = []
    for index, item in enumerate(load_terminal_actions(), start=1):
        lines.append(f"{index}. action={item['action']}")
        lines.append(f"   description: {item.get('description', '')}")
        if item.get("args"):
            lines.append(f"   args: {item['args']}")
        lines.append(f"   choose_when: {join_values(item.get('choose_when', []))}")
        lines.append(f"   do_not_choose_when: {join_values(item.get('do_not_choose_when', []))}")
        lines.append(f"   examples: {join_values(item.get('examples', []))}")
    return "\n".join(lines)
