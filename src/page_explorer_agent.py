import asyncio
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from cfg.model_config import MODEL_CONFIG
from src.browser_state import (
    observe_browser_state,
    summarize_browser_state,
    wait_for_browser_feedback,
)
from src.interactive_element_extractor import extract_interactive_elements
from src.page_context import PageContextManager
from src.page_tool_registry import PAGE_TOOL_REGISTRY, PageToolContext
from src.prompt_config import get_prompt_config, get_prompt_list, render_prompt_template
from src.native_dialog import close_native_dialog_with_escape, get_native_dialog_state
from src.memory_retriever import MemoryRetriever, load_memory_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPLORATION_MEMORY_PATH = PROJECT_ROOT / "agent_memory" / "xhs_exploration_memory.json"
EXPLORATION_TRACE_PATH = PROJECT_ROOT / "agent_memory" / "xhs_exploration_trace.jsonl"
VALID_ACTIONS = set(PAGE_TOOL_REGISTRY.names())


@dataclass
class ExplorationStep:
    action: str
    reason: str = ""
    element_text: str = ""
    result: str = ""
    page_url: str = ""
    observation: str = ""


@dataclass
class ExplorationRecord:
    task: str
    result: str
    path: list[ExplorationStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _client() -> OpenAI:
    return OpenAI(
        api_key=MODEL_CONFIG["api_key"],
        base_url=MODEL_CONFIG["base_url"],
        timeout=MODEL_CONFIG.get("timeout", 30),
    )


def _extract_json(raw_text: str) -> dict | None:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _compact_text(value: str, limit: int = 1200) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


def _extract_text_from_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "reasoning_content"):
            text = _extract_text_from_value(value.get(key))
            if text:
                return text
    return ""


def _extract_response_text(response) -> str:
    if not getattr(response, "choices", None):
        return ""
    message = response.choices[0].message
    text = _extract_text_from_value(getattr(message, "content", None))
    if text:
        return text
    for attr_name in ("reasoning_content", "text"):
        text = _extract_text_from_value(getattr(message, attr_name, None))
        if text:
            return text
    if hasattr(message, "model_dump"):
        dumped = message.model_dump()
        for key in ("content", "reasoning_content", "text"):
            text = _extract_text_from_value(dumped.get(key))
            if text:
                return text
    return ""


def _debug_response(label: str, response, raw_text: str):
    finish_reason = "unknown"
    message_preview = ""
    if getattr(response, "choices", None):
        choice = response.choices[0]
        finish_reason = str(getattr(choice, "finish_reason", "") or "unknown")
        message = getattr(choice, "message", None)
        if message is not None and hasattr(message, "model_dump"):
            try:
                message_preview = json.dumps(message.model_dump(), ensure_ascii=False)[:800]
            except Exception:
                message_preview = repr(message)[:800]
    print(
        f"{label} 响应：finish_reason={finish_reason} "
        f"content_len={len(raw_text or '')} raw={repr((raw_text or '')[:1000])}",
        flush=True,
    )
    if not raw_text:
        print(f"{label} message_dump预览：{message_preview}", flush=True)


def _is_valid_action(action: Any) -> bool:
    return PAGE_TOOL_REGISTRY.validate(action)


class ExplorationMemory:
    """保存成功探索路径，让 Agent 下次能像人一样复用经验。"""

    def __init__(self, path: Path = EXPLORATION_MEMORY_PATH):
        self.path = path
        self.records: list[ExplorationRecord] = []
        self.task_requests: list[str] = []
        self.retriever = MemoryRetriever()
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        records = []
        for item in data.get("records", []):
            try:
                steps = [ExplorationStep(**step) for step in item.get("path", [])]
                item["path"] = steps
                records.append(ExplorationRecord(**item))
            except TypeError:
                continue
        self.records = records
        task_requests = [
            str(task).strip()
            for task in data.get("task_requests", [])
            if str(task).strip()
        ]
        if not task_requests:
            task_requests = [record.task for record in self.records if record.task]
        self.task_requests = list(dict.fromkeys(task_requests))
        self._align_task_order()

    def _align_task_order(self):
        indexed: dict[str, ExplorationRecord] = {}
        for record in self.records:
            task = str(record.task or "").strip()
            if not task or task in indexed:
                continue
            record.task = task
            indexed[task] = record

        ordered: list[ExplorationRecord] = []
        for task in self.task_requests:
            if task in indexed:
                ordered.append(indexed.pop(task))
        ordered.extend(indexed.values())
        self.records = ordered[-80:]
        self.task_requests = [record.task for record in self.records]

    def save(self):
        self._align_task_order()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "task_requests": self.task_requests,
            "records": [asdict(record) for record in self.records],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, task: str, result: str, path: list[ExplorationStep]):
        if not result:
            return
        self.records.append(ExplorationRecord(task=task, result=result, path=deepcopy(path)))
        self._align_task_order()
        self.save()

    def _tokens(self, text: str) -> set[str]:
        text = text.lower()
        chinese_chars = set(re.findall(r"[\u4e00-\u9fff]", text))
        latin_tokens = set(re.findall(r"[a-z0-9]+", text))
        return chinese_chars | latin_tokens

    def search(self, task: str, limit: int = 3) -> list[dict]:
        config = load_memory_config()
        retrieval = config.get("retrieval") or {}
        page_config = retrieval.get("page_explorer_agent") if isinstance(retrieval.get("page_explorer_agent"), dict) else {}
        hits = self.retriever.search(
            task,
            target_agent=str(page_config.get("target_agent") or "page_explorer_agent"),
            memory_types=[str(item) for item in page_config.get("memory_types") or ["page_path"]],
            limit=limit,
            retrieval_method=str(page_config.get("retrieval_method") or retrieval.get("default_method") or "bm25"),
        )
        if hits:
            return [
                {
                    "task": hit.get("user_request", ""),
                    "result_preview": _compact_text(hit.get("result", ""), 300),
                    "path": hit.get("steps") or [],
                    "memory_id": hit.get("memory_id", ""),
                    "match_score": hit.get("match_score", 0),
                    "reuse_level": hit.get("reuse_level", ""),
                }
                for hit in hits
            ]

        task_chars = self._tokens(task)
        scored = []
        for record in self.records:
            record_chars = self._tokens(record.task)
            score = len(task_chars & record_chars)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "task": record.task,
                "result_preview": _compact_text(record.result, 300),
                "path": [asdict(step) for step in record.path],
            }
            for _, record in scored[:limit]
        ]


class ExplorationTrace:
    """逐步记录探索动作和页面响应摘要，给下一轮决策提供短期经验。"""

    def __init__(self, path: Path = EXPLORATION_TRACE_PATH):
        self.path = path

    def _tokens(self, text: str) -> set[str]:
        text = text.lower()
        chinese_chars = set(re.findall(r"[\u4e00-\u9fff]", text))
        latin_tokens = set(re.findall(r"[a-z0-9]+", text))
        return chinese_chars | latin_tokens

    def append(self, entry: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def clear(self):
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass

    def recent(self, user_goal: str, limit: int = 10) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-300:]
        except Exception:
            return []

        goal_tokens = self._tokens(user_goal)
        scored = []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = " ".join(
                str(entry.get(key, ""))
                for key in ("user_goal", "element_text", "result", "observation")
            )
            score = len(goal_tokens & self._tokens(text))
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "user_goal": entry.get("user_goal", ""),
                "action": entry.get("action", ""),
                "element_text": entry.get("element_text", ""),
                "result": entry.get("result", ""),
                "observation": entry.get("observation", ""),
                "page_url": entry.get("page_url", ""),
            }
            for _, entry in scored[:limit]
        ]


class XhsPageExplorer:
    """用于没有明确 skill 的页面任务：观察、试探、复盘并记录成功路径。"""

    def __init__(self, memory: ExplorationMemory | None = None):
        self.memory = memory or ExplorationMemory()
        self.trace = ExplorationTrace()
        self.page_context = PageContextManager()

    def _history_lessons(self, history: list[ExplorationStep]) -> list[str]:
        lessons = []
        for index, step in enumerate(history[-10:], start=max(1, len(history) - 9)):
            action_text = _compact_text(step.action, 120)
            element_text = _compact_text(step.element_text, 120)
            result_text = _compact_text(step.result, 160)
            observation_text = _compact_text(step.observation, 220)
            lessons.append(
                f"步骤{index}: 动作={action_text}; 对象={element_text}; "
                f"结果={result_text}; 经验={observation_text}"
            )
        return lessons

    def _action_block_reason(self, action_name: str, scope_context: dict[str, Any]) -> str:
        if not action_name:
            return ""
        forbidden = set(str(item) for item in (scope_context.get("forbidden_actions") or []))
        allowed = set(str(item) for item in (scope_context.get("allowed_actions") or []))
        if action_name in forbidden:
            return f"动作 {action_name} 被当前 step scope 禁止。"
        if allowed and action_name not in allowed:
            return f"动作 {action_name} 不在当前 step scope 的 allowed_actions 中。"
        return ""

    def _classify_page_phase(self, snapshot: dict) -> str:
        url = snapshot.get("url", "")
        text = snapshot.get("visible_text", "")
        fields = snapshot.get("fields") or []
        field_hints = " ".join(
            f"{field.get('hint', '')} {field.get('value', '')}"
            for field in fields
        )
        semantic_hints = " ".join(
            f"{item.get('text', '')} {item.get('attrs', '')} {item.get('tag', '')}"
            for item in (snapshot.get("semantic_targets") or [])
        )
        combined = f"{url} {text} {field_hints} {semantic_hints}"

        for rule in get_prompt_list("page_phase", "rules"):
            if self._phase_rule_matches(rule, url, combined):
                return str(rule.get("name") or get_prompt_config("page_phase", "default", default="unknown"))
        return str(get_prompt_config("page_phase", "default", default="unknown"))

    def _phase_rule_matches(self, rule: dict, url: str, combined: str) -> bool:
        if not isinstance(rule, dict):
            return False
        url_contains = rule.get("url_contains")
        if url_contains:
            values = url_contains if isinstance(url_contains, list) else [url_contains]
            if not any(str(value) in url for value in values):
                return False
        all_terms = [str(value) for value in rule.get("all_terms") or []]
        if all_terms and not all(term in combined for term in all_terms):
            return False
        any_terms = [str(value) for value in rule.get("any_terms") or []]
        if any_terms and not any(term in combined for term in any_terms):
            return False
        for group in rule.get("any_groups") or []:
            terms = [str(value) for value in group]
            if terms and not any(term in combined for term in terms):
                return False
        return True

    async def _page_snapshot(self, page, elements: list[dict], state: dict) -> dict:
        dom = await page.evaluate(
            """() => {
                const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                const semanticAttrNames = [
                    'aria-label',
                    'title',
                    'data-text',
                    'data-title',
                    'data-name',
                    'data-label',
                    'save-text',
                    'submit-text',
                    'cancel-text',
                    'confirm-text',
                    'delete-text'
                ];
                const semanticSelector = [
                    'button',
                    'a',
                    '[role="button"]',
                    '[role="link"]',
                    '[onclick]',
                    '[tabindex]',
                    '[class*="btn"]',
                    '[class*="Btn"]',
                    '[class*="button"]',
                    '[class*="Button"]',
                    'xhs-publish-btn',
                    '[save-text]',
                    '[submit-text]',
                    '[cancel-text]',
                    '[confirm-text]',
                    '[delete-text]',
                    '[aria-label]',
                    '[title]',
                    '[data-text]',
                    '[data-title]'
                ].join(',');
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = (el, allowAttributeOnly = false) => {
                    if (!el || !el.getBoundingClientRect) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const rendered = style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                    if (!rendered) return false;
                    return rect.width > 0 && rect.height > 0 || allowAttributeOnly;
                };
                const attrText = el => semanticAttrNames
                    .map(name => el.getAttribute && el.getAttribute(name))
                    .filter(Boolean)
                    .map(normalize)
                    .join(' ');
                const collectSemanticNodes = root => {
                    const nodes = [];
                    const seen = new Set();
                    const visit = node => {
                        if (!node || !node.querySelectorAll) return;
                        for (const el of node.querySelectorAll(semanticSelector)) {
                            if (!seen.has(el)) {
                                seen.add(el);
                                nodes.push(el);
                            }
                        }
                        for (const el of node.querySelectorAll('*')) {
                            if (el.shadowRoot) visit(el.shadowRoot);
                        }
                    };
                    visit(root);
                    return nodes;
                };
                const semanticTargets = collectSemanticNodes(document)
                    .filter(el => visible(el, Boolean(attrText(el))))
                    .map(el => {
                        const attrs = normalize(attrText(el));
                        const inner = normalize(el.innerText || el.textContent);
                        const textParts = [attrs, inner].filter(Boolean);
                        return {
                            tag: el.tagName.toLowerCase(),
                            text: textParts.join(' | ').slice(0, 180),
                            attrs: attrs.slice(0, 180),
                            inner_text: inner.slice(0, 180),
                            role: el.getAttribute('role') || '',
                            class_name: String(el.className || '').slice(0, 120)
                        };
                    })
                    .filter(item => item.text)
                    .filter((item, index, array) => {
                        const key = `${item.tag}:${item.text}`;
                        return array.findIndex(other => `${other.tag}:${other.text}` === key) === index;
                    })
                    .slice(0, 80);
                const fields = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]'))
                    .filter(el => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0
                            && style.display !== 'none'
                            && style.visibility !== 'hidden';
                    })
                    .map((el, index) => ({
                        index,
                        tag: el.tagName.toLowerCase(),
                        hint: [
                            el.getAttribute('placeholder') || '',
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('data-placeholder') || ''
                        ].join(' ').trim(),
                        value: (el.value || el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 1200)
                    }))
                    .slice(0, 20);
                const mediaSelector = [
                    'img',
                    'video',
                    'canvas',
                    '[role="img"]',
                    '[class*="cover"]',
                    '[class*="Cover"]',
                    '[class*="image"]',
                    '[class*="Image"]',
                    '[class*="img"]',
                    '[class*="Img"]',
                    '[class*="thumb"]',
                    '[class*="Thumb"]',
                    '[style*="background-image"]'
                ].join(',');
                const mediaTargets = Array.from(document.querySelectorAll(mediaSelector))
                    .filter(el => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 20 && rect.height > 20
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && style.opacity !== '0';
                    })
                    .map(el => {
                        const rect = el.getBoundingClientRect();
                        const nearText = normalize(
                            el.closest('article,li,[class*="comment"],[class*="Comment"],[class*="card"],[class*="Card"],[class*="item"],[class*="Item"],section,div')
                                ?.innerText || ''
                        );
                        return {
                            tag: el.tagName.toLowerCase(),
                            alt: normalize(el.getAttribute('alt') || ''),
                            title: normalize(el.getAttribute('title') || ''),
                            class_name: String(el.className || '').slice(0, 120),
                            near_text: nearText.slice(0, 220),
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height)
                            }
                        };
                    })
                    .filter(item => item.near_text || item.alt || item.title || /cover|image|img|thumb/i.test(item.class_name))
                    .slice(0, 50);
                return {text, fields, semanticTargets, mediaTargets};
            }"""
        )
        snapshot = {
            "url": page.url,
            "site": "creator" if "creator.xiaohongshu.com" in page.url else "web" if "www.xiaohongshu.com" in page.url else "unknown",
            "browser_state": summarize_browser_state(state),
            "visible_text": _compact_text(dom.get("text", ""), 2600),
            "fields": dom.get("fields", []),
            "semantic_targets": dom.get("semanticTargets", []),
            "media_targets": dom.get("mediaTargets", []),
            "element_count": len(elements),
            "elements": [
                {
                    "index": item.get("index"),
                    "type": item.get("type"),
                    "text": item.get("text"),
                    "desc": item.get("desc"),
                }
                for item in elements[:160]
            ],
        }
        snapshot["page_phase"] = self._classify_page_phase(snapshot)
        return snapshot

    def _planner_prompt(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        memory_hits: list[dict],
        trace_notes: list[dict],
        worklog_hints: list[dict],
        scope_context: dict[str, Any] | None = None,
    ) -> str:
        template = str(get_prompt_config("page_explorer", "planner_prompt_template", default=""))
        return render_prompt_template(
            template,
            user_goal=user_goal,
            scope_context_json=json.dumps(scope_context or {}, ensure_ascii=False, indent=2),
            tool_catalog=PAGE_TOOL_REGISTRY.render_prompt_catalog(),
            json_rules=PAGE_TOOL_REGISTRY.render_json_rules(),
            history_json=json.dumps([asdict(step) for step in history[-8:]], ensure_ascii=False, indent=2),
            history_lessons_json=json.dumps(self._history_lessons(history), ensure_ascii=False, indent=2),
            memory_hits_json=json.dumps(memory_hits, ensure_ascii=False, indent=2),
            trace_notes_json=json.dumps(trace_notes[-12:], ensure_ascii=False, indent=2),
            worklog_hints_json=json.dumps(worklog_hints[-6:], ensure_ascii=False, indent=2),
            snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=2),
            page_context=self.page_context.render(),
        )

    def _print_snapshot_debug(self, snapshot: dict):
        print(
            "页面快照摘要："
            f"phase={snapshot.get('page_phase')} "
            f"url={snapshot.get('url')} "
            f"elements={snapshot.get('element_count', len(snapshot.get('elements') or []))} "
            f"fields={len(snapshot.get('fields') or [])}",
            flush=True,
        )
        visible_text = _compact_text(snapshot.get("visible_text", ""), 300)
        if visible_text:
            print(f"页面可见文本预览：{visible_text}", flush=True)
        semantic_targets = snapshot.get("semantic_targets") or []
        if semantic_targets:
            print("页面语义目标预览（前20个）：", flush=True)
            for item in semantic_targets[:20]:
                text = _compact_text(item.get("text") or item.get("attrs") or "", 120)
                print(f"  [{item.get('tag')}] {text}", flush=True)
        media_targets = snapshot.get("media_targets") or []
        if media_targets:
            print("页面媒体目标预览（前12个）：", flush=True)
            for item in media_targets[:12]:
                text = _compact_text(item.get("near_text") or item.get("alt") or item.get("title") or "", 140)
                rect = item.get("rect") or {}
                print(f"  [{item.get('tag')}] {text} rect={rect}", flush=True)
        print("当前可交互元素预览（前40个）：", flush=True)
        for item in (snapshot.get("elements") or [])[:40]:
            text = _compact_text(item.get("text") or item.get("desc") or "", 120)
            print(f"  [{item.get('index')}] [{item.get('type')}] {text}", flush=True)

    async def _plan_action(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        memory_hits: list[dict],
        trace_notes: list[dict],
        worklog_hints: list[dict],
        scope_context: dict[str, Any] | None = None,
    ) -> dict:
        prompt = self._planner_prompt(user_goal, snapshot, history, memory_hits, trace_notes, worklog_hints, scope_context)
        response = _client().chat.completions.create(
            model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max(int(MODEL_CONFIG.get("planner_max_tokens", 1000)), 1600),
            temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
        )
        raw_text = _extract_response_text(response)
        _debug_response("页面探索主规划模型", response, raw_text)
        parsed = _extract_json(raw_text)
        if _is_valid_action(parsed):
            return parsed

        repaired = await self._repair_action(
            user_goal=user_goal,
            snapshot=snapshot,
            history=history,
            raw_text=raw_text,
        )
        if _is_valid_action(repaired):
            return repaired

        return {
            "action": "fail",
            "reason": (
                "页面探索模型没有返回合法动作。"
                f"原始返回预览：{_compact_text(raw_text, 300)}"
            ),
        }

    async def _repair_action(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        raw_text: str,
    ) -> dict | None:
        template = str(get_prompt_config("page_explorer", "action_repair_prompt_template", default="")).strip()
        if not template:
            return None

        prompt = render_prompt_template(
            template,
            user_goal=user_goal,
            page_context=self.page_context.render(limit=2600),
            history_json=json.dumps([asdict(step) for step in history[-6:]], ensure_ascii=False, indent=2),
            snapshot_json=json.dumps(self._snapshot_brief(snapshot), ensure_ascii=False, indent=2),
            raw_model_output=_compact_text(raw_text, 3000),
            tool_catalog=PAGE_TOOL_REGISTRY.render_prompt_catalog(),
            json_rules=PAGE_TOOL_REGISTRY.render_json_rules(),
        )
        try:
            response = _client().chat.completions.create(
                model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("planner_model"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=min(int(MODEL_CONFIG.get("formatter_max_tokens", 1200)), 1600),
                temperature=MODEL_CONFIG.get("formatter_temperature", 0.1),
            )
            repaired_text = _extract_response_text(response)
            _debug_response("页面探索动作修复模型", response, repaired_text)
            repaired = _extract_json(repaired_text)
            if _is_valid_action(repaired):
                return repaired
            print(
                "页面探索动作修复模型未返回合法动作："
                f"{_compact_text(repaired_text, 300)}",
                flush=True,
            )
            return None
        except Exception as exc:
            print(f"页面探索动作修复失败：{exc}", flush=True)
            return None

    def _snapshot_brief(self, snapshot: dict) -> dict:
        return {
            "url": snapshot.get("url", ""),
            "browser_state": snapshot.get("browser_state", ""),
            "visible_text": _compact_text(snapshot.get("visible_text", ""), 800),
            "fields": [
                {
                    "hint": field.get("hint", ""),
                    "value": _compact_text(field.get("value", ""), 180),
                }
                for field in (snapshot.get("fields") or [])[:5]
            ],
            "semantic_targets": [
                {
                    "tag": item.get("tag", ""),
                    "text": _compact_text(item.get("text", ""), 160),
                    "attrs": _compact_text(item.get("attrs", ""), 160),
                }
                for item in (snapshot.get("semantic_targets") or [])[:12]
            ],
            "media_targets": [
                {
                    "tag": item.get("tag", ""),
                    "near_text": _compact_text(item.get("near_text", ""), 180),
                    "alt": _compact_text(item.get("alt", ""), 80),
                    "title": _compact_text(item.get("title", ""), 80),
                    "rect": item.get("rect", {}),
                }
                for item in (snapshot.get("media_targets") or [])[:12]
            ],
            "elements": [
                f"{item.get('index')}: {item.get('text') or item.get('desc')}"
                for item in (snapshot.get("elements") or [])[:18]
            ],
        }

    def _compact_action_context(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        trace_notes: list[dict],
        worklog_hints: list[dict],
        element_limit: int = 120,
    ) -> str:
        elements = []
        for item in (snapshot.get("elements") or [])[:element_limit]:
            text = item.get("text") or item.get("desc") or ""
            elements.append(
                {
                    "index": item.get("index"),
                    "type": item.get("type"),
                    "text": _compact_text(text, 180),
                }
            )
        compact_hints = [
            {
                "user_request": hint.get("user_request"),
                "reuse_level": hint.get("reuse_level"),
                "request_match_ratio": hint.get("request_match_ratio"),
                "summary": _compact_text(hint.get("summary", ""), 500),
                "steps": hint.get("steps", [])[:6],
            }
            for hint in (worklog_hints or [])[:4]
        ]
        return json.dumps(
            {
                "user_goal": user_goal,
            "url": snapshot.get("url", ""),
            "site": snapshot.get("site", ""),
            "page_phase": snapshot.get("page_phase", ""),
                "browser_state": snapshot.get("browser_state", ""),
                "visible_text": _compact_text(snapshot.get("visible_text", ""), 1800),
                "fields": snapshot.get("fields", [])[:12],
                "semantic_targets": snapshot.get("semantic_targets", [])[:40],
                "media_targets": snapshot.get("media_targets", [])[:30],
                "interactive_elements": elements,
                "recent_actions": [asdict(step) for step in history[-6:]],
                "recent_action_summaries": trace_notes[-8:],
                "successful_memory_hints": compact_hints,
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _summarize_transition(
        self,
        user_goal: str,
        action: dict,
        element_text: str,
        result: str,
        before_snapshot: dict,
        after_snapshot: dict,
    ) -> str:
        template = str(get_prompt_config("page_explorer", "transition_summary_prompt_template", default=""))
        prompt = render_prompt_template(
            template,
            user_goal=user_goal,
            action_json=json.dumps(action, ensure_ascii=False),
            element_text=element_text,
            result=result,
            before_snapshot_json=json.dumps(self._snapshot_brief(before_snapshot), ensure_ascii=False, indent=2),
            after_snapshot_json=json.dumps(self._snapshot_brief(after_snapshot), ensure_ascii=False, indent=2),
        )
        try:
            response = _client().chat.completions.create(
                model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("content_model"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=MODEL_CONFIG.get("formatter_temperature", 0.1),
            )
            text = (response.choices[0].message.content or "").strip()
            return _compact_text(text, 220)
        except Exception:
            before_text = before_snapshot.get("visible_text", "")
            after_text = after_snapshot.get("visible_text", "")
            if before_snapshot.get("url") != after_snapshot.get("url"):
                change = f"URL 从 {before_snapshot.get('url')} 变为 {after_snapshot.get('url')}"
            elif before_text[:300] != after_text[:300]:
                change = "页面文本发生变化"
            else:
                change = "页面没有明显变化"
            return _compact_text(f"执行 {action.get('action')} {element_text} 后，{change}；结果：{result}", 220)

    async def explore(
        self,
        page,
        user_goal: str,
        max_steps: int = 12,
        worklog_hints: list[dict] | None = None,
        switch_page=None,
        scope: str = "",
        success_criteria: str = "",
        allowed_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        scope_note: str = "",
    ) -> dict:
        self.trace.clear()
        history: list[ExplorationStep] = []
        memory_hits = self.memory.search(user_goal)
        trace_notes = []
        worklog_hints = worklog_hints or []
        scope_context = {
            "scope": scope or "unrestricted",
            "success_criteria": success_criteria,
            "allowed_actions": allowed_actions or [],
            "forbidden_actions": forbidden_actions or [],
            "scope_note": scope_note,
        }
        last_action_key = ""
        repeated_no_response = 0
        self.page_context.reset(user_goal)

        for step_index in range(1, max_steps + 1):
            state = await observe_browser_state(page)
            native_dialog = state.get("system_dialog") or get_native_dialog_state()
            if native_dialog.get("possible_native_dialog_open"):
                closed = close_native_dialog_with_escape()
                observation = (
                    "检测到疑似 Windows 原生文件选择弹窗阻塞网页操作，"
                    f"{'已发送 Escape 尝试关闭' if closed else '但自动关闭失败'}。"
                    "下一步应重新观察页面，不要把此时的网页无响应误判为按钮无效。"
                )
                print(f"系统弹窗处理：{observation}")
                trace_entry = {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "user_goal": user_goal,
                    "step_index": step_index,
                    "action": "system_dialog_escape",
                    "element_text": native_dialog.get("foreground", {}).get("title", ""),
                    "result": "closed" if closed else "close_failed",
                    "observation": observation,
                    "page_url": state.get("url", ""),
                }
                self.trace.append(trace_entry)
                trace_notes.append(trace_entry)
                history.append(
                    ExplorationStep(
                        action="system_dialog_escape",
                        reason="检测到系统原生弹窗会阻塞网页点击。",
                        element_text=native_dialog.get("foreground", {}).get("title", ""),
                        result="closed" if closed else "close_failed",
                        page_url=state.get("url", ""),
                        observation=observation,
                    )
                )
                await asyncio.sleep(1)
                continue

            if state.get("loading_phase") in {"dom_loading", "page_loading", "network_busy"}:
                print(f"探索步骤 {step_index}: 页面仍在加载，先等待")
                await asyncio.sleep(1.5)
                continue

            elements = await extract_interactive_elements(page, max_retries=1)
            snapshot = await self._page_snapshot(page, elements, state)
            self.page_context.apply_snapshot(snapshot)
            self._print_snapshot_debug(snapshot)
            action = await self._plan_action(user_goal, snapshot, history, memory_hits, trace_notes, worklog_hints, scope_context)
            action_name = action.get("action")
            reason = action.get("reason", "")
            print(f"探索步骤 {step_index}: {action}")

            block_reason = self._action_block_reason(str(action_name or ""), scope_context)
            if block_reason:
                observation = (
                    f"{block_reason} 当前 step 目标是：{user_goal}；"
                    f"scope={scope_context.get('scope')}；请改用允许动作完成该子任务。"
                )
                print(f"scope 阻止动作：{observation}")
                trace_entry = {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "user_goal": user_goal,
                    "step_index": step_index,
                    "action": json.dumps(action, ensure_ascii=False),
                    "element_text": "",
                    "result": "blocked_by_scope",
                    "observation": observation,
                    "page_url": snapshot.get("url", ""),
                }
                self.trace.append(trace_entry)
                trace_notes.append(trace_entry)
                history.append(
                    ExplorationStep(
                        action=json.dumps(action, ensure_ascii=False),
                        reason=reason,
                        element_text="",
                        result="blocked_by_scope",
                        page_url=snapshot.get("url", ""),
                        observation=observation,
                    )
                )
                continue

            if action_name in {"done", "extract_answer"}:
                answer = action.get("answer") or snapshot.get("visible_text", "")
                next_suggestion = _compact_text(action.get("next_suggestion") or "", 260)
                self.page_context.update(
                    user_goal=user_goal,
                    action=action,
                    result=answer,
                    observation="任务完成或已提取答案。",
                    before_snapshot=snapshot,
                    after_snapshot=snapshot,
                )
                self.memory.add(user_goal, answer, history)
                return {
                    "success": True,
                    "answer": answer,
                    "next_suggestion": next_suggestion,
                    "steps": [asdict(step) for step in history],
                }

            if action_name == "fail":
                self.page_context.update(
                    user_goal=user_goal,
                    action=action,
                    result=action.get("answer") or reason or "探索失败",
                    observation="页面探索模型判断任务失败。",
                    before_snapshot=snapshot,
                    after_snapshot=snapshot,
                )
                return {
                    "success": False,
                    "answer": action.get("answer") or reason or "探索失败",
                    "steps": [asdict(step) for step in history],
                }

            if action.get("requires_user_confirmation"):
                confirmation_message = (
                    action.get("confirmation_message")
                    or reason
                    or "模型判断该动作需要用户确认。"
                )
                print("页面动作需要确认：")
                print(f"- 动作：{json.dumps(action, ensure_ascii=False)}")
                print(f"- 原因：{confirmation_message}")
                confirmation = input("是否执行该动作？输入 y 执行，输入 n 取消 [y/N]：").strip().lower()
                if confirmation not in {"y", "yes"}:
                    observation = "用户拒绝执行模型标记为需确认的页面动作，任务已停止。"
                    history.append(
                        ExplorationStep(
                            action=json.dumps(action, ensure_ascii=False),
                            reason=reason,
                            element_text="",
                            result="用户取消",
                            page_url=snapshot.get("url", ""),
                            observation=observation,
                        )
                    )
                    return {
                        "success": True,
                        "answer": observation,
                        "steps": [asdict(step) for step in history],
                        "remember": False,
                    }

            before_state = await observe_browser_state(page)
            before_snapshot = snapshot
            result = ""
            element_text = ""
            action_key = (
                f"{action_name}:{action.get('element_index')}:"
                f"{action.get('text', '')[:40]}:{action.get('value', '')[:40]}"
            )

            try:
                tool_context = PageToolContext(page=page, elements=elements, switch_page=switch_page)
                tool_result = await PAGE_TOOL_REGISTRY.execute(action_name, tool_context, action)
                result = tool_result.message
                element_text = tool_result.element_text
                if tool_result.page is not None:
                    page = tool_result.page
            except Exception as exc:
                result = f"动作执行失败：{exc}"

            after_state, comparison = await wait_for_browser_feedback(page, before_state, timeout=3.0)
            response_status = comparison.get("status")
            result = f"{result}；页面反馈={response_status}"
            dialog_after_action = after_state.get("system_dialog") or get_native_dialog_state()
            if dialog_after_action.get("possible_native_dialog_open"):
                closed = close_native_dialog_with_escape()
                result = (
                    f"{result}；检测到疑似系统文件选择弹窗阻塞网页，"
                    f"{'已发送 Escape 关闭' if closed else '自动关闭失败'}"
                )

            after_elements = []
            after_snapshot = {
                "url": after_state.get("url", ""),
                "browser_state": summarize_browser_state(after_state),
                "visible_text": "",
                "fields": [],
                "elements": [],
            }
            if not after_state.get("page_closed"):
                try:
                    after_elements = await extract_interactive_elements(page, max_retries=1)
                    after_snapshot = await self._page_snapshot(page, after_elements, after_state)
                except Exception as exc:
                    after_snapshot["visible_text"] = f"动作后页面快照提取失败：{exc}"

            observation = await self._summarize_transition(
                user_goal=user_goal,
                action=action,
                element_text=element_text,
                result=result,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
            )
            self.page_context.update(
                user_goal=user_goal,
                action=action,
                result=result,
                observation=observation,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
            )

            if action_key == last_action_key and response_status == "no_visible_response":
                repeated_no_response += 1
            else:
                repeated_no_response = 0
            last_action_key = action_key

            history.append(
                ExplorationStep(
                    action=json.dumps(action, ensure_ascii=False),
                    reason=reason,
                    element_text=element_text,
                    result=result,
                    page_url=after_state.get("url", ""),
                    observation=observation,
                )
            )
            trace_entry = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "user_goal": user_goal,
                "step_index": step_index,
                "action": json.dumps(action, ensure_ascii=False),
                "element_text": element_text,
                "result": result,
                "observation": observation,
                "page_url": after_state.get("url", ""),
            }
            self.trace.append(trace_entry)
            trace_notes.append(trace_entry)
            trace_notes = trace_notes[-12:]
            print(f"动作效果摘要：{observation}")
            print(f"page_context 摘要：{self.page_context.brief()}")

            if repeated_no_response >= 1:
                print("探索检测到重复无响应，下一步会让模型换路径或返回。")

        return {
            "success": False,
            "answer": "达到最大探索步数，未能可靠完成任务。",
            "steps": [asdict(step) for step in history],
        }
