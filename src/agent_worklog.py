import json
import queue
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.prompt_config import get_prompt_config, get_prompt_dict, get_prompt_list, render_prompt_template


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
        mapping = get_prompt_dict("workflow", "default_steps")
        fallback = mapping.get("fallback") or ["understand_request", "respond"]
        steps = mapping.get(action, fallback)
        return [WorkStep(name=str(name)) for name in steps]

    def _tokens(self, text: str) -> set[str]:
        import re

        text = (text or "").lower()
        domain_terms = set(get_prompt_list("workflow", "memory_terms", "domain_terms"))
        stop_chars = set(str(get_prompt_config("workflow", "memory_terms", "stop_chars", default="")))
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

            from cfg.model_config import MEMORY_REVIEW_MODEL_CONFIG
        except Exception as exc:
            return {
                "should_add": True,
                "duplicate_request": "",
                "reason": f"记忆审核模型不可用，使用本地非重复判断：{exc}",
            }

        template = str(get_prompt_config("workflow", "memory_review_prompt_template", default=""))
        prompt = render_prompt_template(
            template,
            existing_requests_json=json.dumps(existing_requests, ensure_ascii=False, indent=2),
            user_request=user_request,
        )
        try:
            client = OpenAI(
                api_key=MEMORY_REVIEW_MODEL_CONFIG["api_key"],
                base_url=MEMORY_REVIEW_MODEL_CONFIG["base_url"],
                timeout=MEMORY_REVIEW_MODEL_CONFIG.get("timeout", 30),
            )
            response = client.chat.completions.create(
                model=MEMORY_REVIEW_MODEL_CONFIG.get("model", "deepseek-v4-flash"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=MEMORY_REVIEW_MODEL_CONFIG.get("max_tokens", 800),
                temperature=MEMORY_REVIEW_MODEL_CONFIG.get("temperature", 0.1),
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
        important_terms = set(get_prompt_list("workflow", "memory_terms", "important_terms"))
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
        suggestions = get_prompt_dict("workflow", "next_suggestions")
        task = self.current_task
        if not task:
            return str(suggestions.get("idle", ""))
        if task.status == "failed":
            if "ERR_NAME_NOT_RESOLVED" in task.error or "DNS/网络解析失败" in task.error:
                return str(suggestions.get("failed_network", ""))
            return str(suggestions.get("failed_default", ""))
        if task.status == "completed":
            if task.next_suggestion:
                if task.next_suggestion.startswith("下一步"):
                    return task.next_suggestion
                return f"下一步：{task.next_suggestion}"
            return str(suggestions.get("completed_default", ""))
        pending = [step.name for step in task.steps if step.status == "pending"]
        if pending:
            return str(suggestions.get("pending_template", "next: {step}")).format(step=pending[0])
        return str(suggestions.get("finishing", ""))
