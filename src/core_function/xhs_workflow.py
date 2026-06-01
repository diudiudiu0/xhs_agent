import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKLOG_PATH = PROJECT_ROOT / "agent_memory" / "xhs_agent_worklog.json"


@dataclass
class WorkStep:
    name: str
    status: str = "pending"
    note: str = ""


@dataclass
class WorkItem:
    user_request: str
    action: str
    args: dict = field(default_factory=dict)
    status: str = "pending"
    steps: list[WorkStep] = field(default_factory=list)
    result: str = ""
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class XhsWorkflow:
    """终端 Agent 的工作台：记录任务、步骤、结果和下一步建议。"""

    def __init__(self, worklog_path: Path = WORKLOG_PATH):
        self.worklog_path = worklog_path
        self.tasks: list[WorkItem] = []
        self.current_task: WorkItem | None = None
        self.load()

    def load(self):
        if not self.worklog_path.exists():
            return
        try:
            data = json.loads(self.worklog_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.tasks = []
        for item in data.get("tasks", []):
            steps = [WorkStep(**step) for step in item.get("steps", [])]
            item["steps"] = steps
            self.tasks.append(WorkItem(**item))
        self.current_task = self.tasks[-1] if self.tasks else None

    def save(self):
        self.worklog_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tasks": [asdict(task) for task in self.tasks[-50:]]}
        self.worklog_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self, user_request: str, intent: dict) -> WorkItem:
        action = intent.get("action", "chat")
        args = intent.get("args") or {}
        task = WorkItem(
            user_request=user_request,
            action=action,
            args=args,
            status="in_progress",
            steps=self._default_steps(action),
        )
        self.tasks.append(task)
        self.current_task = task
        self.save()
        return task

    def _default_steps(self, action: str) -> list[WorkStep]:
        mapping = {
            "generate_prompts": ["读取图片任务配置", "调用视觉模型生成提示词", "保存提示词到会话记忆"],
            "revise_prompts": ["读取当前提示词", "按用户要求重写提示词", "保存修改后的提示词"],
            "generate_images": ["读取当前提示词", "调用图片模型生成图片", "保存图片路径到会话记忆"],
            "plan_note_text": ["读取当前图片提示词", "生成标题和正文", "保存发帖文案"],
            "create_draft": ["打开创作中心", "上传素材", "填写标题正文", "暂存离开"],
            "open_page": ["打开创作中心", "保存页面句柄"],
            "page_state": ["打开或复用页面", "采集页面状态", "输出状态摘要"],
            "handle_dialogs": ["打开或复用页面", "检测并处理网页弹窗", "记录处理结果"],
            "inspect_drafts": ["打开创作中心", "进入可见草稿入口", "读取草稿箱数量"],
            "explore_page_task": ["理解页面任务", "观察页面和可交互元素", "小步探索并观察反馈", "提取结果并记录成功路径"],
        }
        return [WorkStep(name=name) for name in mapping.get(action, ["理解用户请求", "给出回复"])]

    def step(self, name: str, status: str = "completed", note: str = ""):
        task = self.current_task
        if not task:
            return
        for step in task.steps:
            if step.name == name or step.status == "pending":
                step.status = status
                if note:
                    step.note = note
                break
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def complete(self, result: str = ""):
        task = self.current_task
        if not task:
            return
        task.status = "completed"
        task.result = result
        for step in task.steps:
            if step.status == "pending":
                step.status = "completed"
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def fail(self, error: str):
        task = self.current_task
        if not task:
            return
        task.status = "failed"
        task.error = error
        for step in task.steps:
            if step.status == "pending":
                step.status = "failed"
                step.note = error
                break
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def snapshot(self) -> str:
        lines = ["工作台状态："]
        if not self.tasks:
            return "工作台状态：暂无任务"
        for index, task in enumerate(self.tasks[-5:], start=max(1, len(self.tasks) - 4)):
            lines.append(f"{index}. [{task.status}] {task.user_request} -> {task.action}")
            for step in task.steps:
                lines.append(f"   - [{step.status}] {step.name}{('：' + step.note) if step.note else ''}")
            if task.result:
                lines.append(f"   结果：{task.result}")
            if task.error:
                lines.append(f"   错误：{task.error}")
        return "\n".join(lines)

    def next_suggestion(self) -> str:
        task = self.current_task
        if not task:
            return "下一步：等待用户提出任务。"
        if task.status == "failed":
            if "ERR_NAME_NOT_RESOLVED" in task.error or "DNS/网络解析失败" in task.error:
                return "下一步：先检查本机网络、代理或 DNS；如果浏览器里已有小红书页面，重新发起任务时 Agent 会优先复用现有页面。"
            return "下一步：根据错误信息调整参数或重新发起任务。"
        if task.status == "completed":
            suggestions = {
                "generate_prompts": "下一步：如果不满意提示词，可以继续说“修改提示词...”；满意后说“生成图片”。",
                "revise_prompts": "下一步：确认提示词满意后说“生成图片”。",
                "generate_images": "下一步：可以说“写文案”或“创建草稿”。",
                "plan_note_text": "下一步：可以说“创建草稿”。",
                "inspect_drafts": "下一步：可以继续查看草稿详情，或创建新的草稿。",
                "explore_page_task": "下一步：如果结果满意，可以继续要求我复用这条路径；如果不满意，可以补充约束让我继续探索。",
            }
            return suggestions.get(task.action, "下一步：等待用户继续指令。")
        pending = [step.name for step in task.steps if step.status == "pending"]
        return f"下一步：继续执行 {pending[0]}。" if pending else "下一步：整理任务结果。"
