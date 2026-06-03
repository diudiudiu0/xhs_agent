import json
import queue
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    next_suggestion: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class WorkExperience:
    user_request: str
    result: str
    summary: str
    steps: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class XhsWorkflow:
    """终端 Agent 的工作台：记录任务、步骤、结果和下一步建议。"""

    def __init__(self, worklog_path: Path = WORKLOG_PATH):
        self.worklog_path = worklog_path
        self.tasks: list[WorkItem] = []
        self.experiences: list[WorkExperience] = []
        self.memorized_requests: list[str] = []
        self.current_task: WorkItem | None = None
        self._lock = threading.RLock()
        self._memory_review_queue: queue.Queue[tuple[WorkExperience, list[str]]] = queue.Queue()
        self._memory_review_thread = threading.Thread(
            target=self._memory_review_loop,
            name="xhs-memory-review",
            daemon=True,
        )
        self._memory_review_thread.start()
        self.load()

    def load(self):
        with self._lock:
            if not self.worklog_path.exists():
                return
            try:
                data = json.loads(self.worklog_path.read_text(encoding="utf-8"))
            except Exception:
                return
            self.tasks = []
            for item in data.get("tasks", []):
                if item.get("status") != "completed":
                    continue
                steps = [WorkStep(**step) for step in item.get("steps", [])]
                item["steps"] = steps
                self.tasks.append(WorkItem(**item))
            self.experiences = []
            for item in data.get("experiences", []):
                self.experiences.append(WorkExperience(**item))
            stored_requests = [
                str(request).strip()
                for request in data.get("memorized_requests", [])
                if str(request).strip()
            ]
            if not stored_requests:
                stored_requests = [item.user_request for item in self.experiences if item.user_request]
            self.memorized_requests = list(dict.fromkeys(stored_requests))
            self.current_task = self.tasks[-1] if self.tasks else None

    def save(self):
        with self._lock:
            self.worklog_path.parent.mkdir(parents=True, exist_ok=True)
            completed_tasks = [task for task in self.tasks if task.status == "completed"]
            data = {
                "tasks": [asdict(task) for task in completed_tasks[-50:]],
                "memorized_requests": self.memorized_requests[-300:],
                "experiences": [asdict(item) for item in self.experiences[-120:]],
            }
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
            "explore_page_task": ["理解页面任务", "观察页面和可交互元素", "小步探索并观察反馈", "提取结果并记录成功路径"],
        }
        return [WorkStep(name=name) for name in mapping.get(action, ["理解用户请求", "给出回复"])]

    def _tokens(self, text: str) -> set[str]:
        import re

        text = (text or "").lower()
        domain_terms = {
            "草稿箱", "草稿", "标题", "正文", "内容", "编辑", "修改", "删除",
            "创建", "发布", "上传", "图片", "视频", "首页", "笔记管理",
            "发帖", "第一篇", "最早", "较早", "时间更早", "数量", "详情",
            "帖子", "笔记", "作品", "文档",
        }
        stop_chars = set("的了我你他她它为把将当前这边那篇这篇一个一下多少几有请帮")
        tokens = set(re.findall(r"[a-z0-9]+", text))
        tokens.update(term for term in domain_terms if term in text)

        chunks = re.findall(r"[\u4e00-\u9fff]+", text)
        for chunk in chunks:
            cleaned = "".join(char for char in chunk if char not in stop_chars)
            for index in range(max(0, len(cleaned) - 1)):
                tokens.add(cleaned[index : index + 2])
        return tokens

    def _build_experience(self, user_request: str, result: str, raw_steps: list[dict] | None) -> WorkExperience:
        raw_steps = raw_steps or []
        cleaned_steps = []
        summary_lines = []
        for index, step in enumerate(raw_steps, start=1):
            item = {
                "step": index,
                "action": str(step.get("action", ""))[:220],
                "element_text": str(step.get("element_text", ""))[:220],
                "result": str(step.get("result", ""))[:260],
                "observation": str(step.get("observation", ""))[:320],
                "page_url": step.get("page_url", ""),
            }
            cleaned_steps.append(item)
            useful_text = item["observation"] or item["result"]
            if useful_text:
                summary_lines.append(f"{index}. {useful_text}")

        summary = "\n".join(summary_lines[-12:]) or str(result)[:500]
        return WorkExperience(
            user_request=user_request,
            result=str(result)[:1000],
            summary=summary[:2500],
            steps=cleaned_steps[-20:],
        )

    def _local_duplicate_request(self, user_request: str, existing_requests: list[str]) -> str:
        normalized = "".join((user_request or "").split()).lower()
        if not normalized:
            return ""
        for existing in existing_requests:
            existing_normalized = "".join((existing or "").split()).lower()
            if existing_normalized == normalized:
                return existing
        query_tokens = self._tokens(user_request)
        for existing in existing_requests:
            existing_tokens = self._tokens(existing)
            overlap = query_tokens & existing_tokens
            ratio = len(overlap) / max(1, len(query_tokens))
            if ratio >= 0.75 and len(overlap) >= 3:
                return existing
        return ""

    def _ask_memory_agent_should_add(self, user_request: str, existing_requests: list[str]) -> dict:
        local_duplicate = self._local_duplicate_request(user_request, existing_requests)
        if local_duplicate:
            return {
                "should_add": False,
                "duplicate_request": local_duplicate,
                "reason": "本地规则判断为重复请求。",
            }

        try:
            from openai import OpenAI

            from cfg.model_config import MODEL_CONFIG
        except Exception as exc:
            return {
                "should_add": True,
                "duplicate_request": "",
                "reason": f"记忆审核模型不可用，使用本地非重复判断：{exc}",
            }

        prompt = f"""
你是小红书 Agent 的长期记忆审核器。

任务：
判断“当前成功请求”是否应该作为一条新的长期记忆写入。

判断标准：
- 如果当前请求和已有请求语义相同、只是措辞不同、标点不同、数量表达不同，should_add=false。
- 如果当前请求是已有请求的明显重复执行，也 should_add=false。
- 如果当前请求目标不同、操作对象不同、成功路径可能不同，should_add=true。
- 只比较请求语义，不要根据步骤判断。

已有长期记忆请求列表：
{json.dumps(existing_requests, ensure_ascii=False, indent=2)}

当前成功请求：
{user_request}

只输出 JSON：
{{
  "should_add": true,
  "duplicate_request": "",
  "reason": "简短说明"
}}
""".strip()
        try:
            client = OpenAI(
                api_key=MODEL_CONFIG["api_key"],
                base_url=MODEL_CONFIG["base_url"],
                timeout=MODEL_CONFIG.get("timeout", 30),
            )
            response = client.chat.completions.create(
                model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("content_model"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=MODEL_CONFIG.get("formatter_temperature", 0.1),
            )
            raw_text = response.choices[0].message.content or ""
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            parsed = json.loads(raw_text[start:end]) if start >= 0 and end > start else {}
            if isinstance(parsed, dict) and "should_add" in parsed:
                return {
                    "should_add": bool(parsed.get("should_add")),
                    "duplicate_request": str(parsed.get("duplicate_request") or ""),
                    "reason": str(parsed.get("reason") or ""),
                }
        except Exception as exc:
            return {
                "should_add": True,
                "duplicate_request": "",
                "reason": f"记忆审核模型调用失败，使用本地非重复判断：{exc}",
            }

        return {
            "should_add": True,
            "duplicate_request": "",
            "reason": "记忆审核模型返回不可解析，使用本地非重复判断。",
        }

    def _memory_review_worker(self, experience: WorkExperience, existing_requests: list[str]):
        decision = self._ask_memory_agent_should_add(experience.user_request, existing_requests)
        if not decision.get("should_add"):
            duplicate = decision.get("duplicate_request") or "未知重复请求"
            print(
                "长期记忆审核：本次成功请求与已有请求重复，未写入完整步骤。"
                f"重复请求：{duplicate}；原因：{decision.get('reason', '')}"
            )
            return

        with self._lock:
            duplicate = self._local_duplicate_request(experience.user_request, self.memorized_requests)
            if duplicate:
                print(f"长期记忆审核：写入前发现重复请求，已跳过。重复请求：{duplicate}")
                return
            self.experiences.append(experience)
            self.memorized_requests.append(experience.user_request)
            self.memorized_requests = list(dict.fromkeys(self.memorized_requests))[-300:]
            print(f"长期记忆审核：已写入新经验：{experience.user_request}")
            self.save()

    def _memory_review_loop(self):
        while True:
            experience, existing_requests = self._memory_review_queue.get()
            try:
                self._memory_review_worker(experience, existing_requests)
            except Exception as exc:
                print(f"长期记忆审核线程异常，已跳过本次写入：{exc}")
            finally:
                self._memory_review_queue.task_done()

    def remember_experience(self, user_request: str, result: str, raw_steps: list[dict] | None):
        experience = self._build_experience(user_request, result, raw_steps)
        with self._lock:
            existing_requests = list(self.memorized_requests)
        self._memory_review_queue.put((experience, existing_requests))
        print("长期记忆审核：已提交后台队列，不阻塞当前任务。")

    def search_experiences(self, user_request: str, limit: int = 3) -> list[dict]:
        query_tokens = self._tokens(user_request)
        if not query_tokens:
            return []
        scored = []
        important_terms = {"草稿箱", "草稿", "标题", "正文", "编辑", "修改", "删除", "创建", "上传", "图片", "视频", "数量", "详情"}
        for item in self.experiences:
            request_tokens = self._tokens(item.user_request)
            haystack = " ".join([item.user_request, item.result, item.summary])
            item_tokens = self._tokens(haystack)
            overlap = query_tokens & item_tokens
            request_overlap = query_tokens & request_tokens
            important_overlap = {
                token
                for token in overlap
                if token in important_terms
            }
            score = len(overlap) + len(request_overlap) * 2 + len(important_overlap) * 3
            ratio = len(overlap) / max(1, len(query_tokens))
            request_ratio = len(request_overlap) / max(1, len(query_tokens))
            request_important_overlap = request_overlap & important_terms
            if score >= 4 and (important_overlap or request_ratio >= 0.2 or ratio >= 0.25):
                if request_ratio >= 0.35 or len(request_important_overlap) >= 2:
                    reuse_level = "same_goal_candidate"
                elif request_important_overlap:
                    reuse_level = "context_reference_only"
                else:
                    reuse_level = "weak_context_only"
                scored.append((score, request_ratio, ratio, sorted(request_overlap), sorted(overlap), reuse_level, item))
        scored.sort(key=lambda pair: (pair[0], pair[1], pair[2]), reverse=True)
        return [
            {
                "user_request": item.user_request,
                "match_score": score,
                "match_ratio": round(ratio, 3),
                "request_match_ratio": round(request_ratio, 3),
                "request_overlap_terms": request_overlap_terms[:20],
                "overlap_terms": overlap_terms[:20],
                "reuse_level": reuse_level,
                "result": item.result,
                "summary": item.summary,
                "steps": item.steps,
            }
            for score, request_ratio, ratio, request_overlap_terms, overlap_terms, reuse_level, item in scored[:limit]
        ]

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
            if step.status in {"pending", "in_progress"}:
                step.status = "completed"
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def set_next_suggestion(self, suggestion: str):
        task = self.current_task
        if not task:
            return
        task.next_suggestion = (suggestion or "").strip()
        task.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def fail(self, error: str):
        task = self.current_task
        if not task:
            return
        task.status = "failed"
        task.error = error
        for step in task.steps:
            if step.status in {"pending", "in_progress"}:
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
            if task.next_suggestion:
                if task.next_suggestion.startswith("下一步"):
                    return task.next_suggestion
                return f"下一步：{task.next_suggestion}"
            return "下一步：等待用户继续指令。"
        pending = [step.name for step in task.steps if step.status == "pending"]
        return f"下一步：继续执行 {pending[0]}。" if pending else "下一步：整理任务结果。"
