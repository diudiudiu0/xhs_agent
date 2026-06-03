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
from src.core_function.browser_skills import (
    click_by_index,
    click_text_in_element,
    fill_by_index,
    fill_content_direct,
    fill_title_direct,
    go_back,
)
from src.core_function.browser_state_observer import (
    observe_browser_state,
    summarize_browser_state,
    wait_for_browser_feedback,
)
from src.core_function.element_extractor import extract_interactive_elements
from src.core_function.system_dialog_observer import close_native_dialog_with_escape, get_native_dialog_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPLORATION_MEMORY_PATH = PROJECT_ROOT / "agent_memory" / "xhs_exploration_memory.json"
EXPLORATION_TRACE_PATH = PROJECT_ROOT / "agent_memory" / "xhs_exploration_trace.jsonl"
VALID_ACTIONS = {
    "click",
    "click_text_in_element",
    "fill",
    "fill_title",
    "fill_content",
    "back",
    "wait",
    "extract_answer",
    "done",
    "fail",
}


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
    if not isinstance(action, dict) or action.get("action") not in VALID_ACTIONS:
        return False
    if action["action"] == "click" and action.get("element_index") is None:
        return False
    if action["action"] == "click_text_in_element" and (
        action.get("element_index") is None or not (action.get("text") or action.get("value"))
    ):
        return False
    if action["action"] == "fill" and (action.get("element_index") is None or action.get("value") is None):
        return False
    if action["action"] in {"fill_title", "fill_content"} and action.get("value") is None:
        return False
    return True


class ExplorationMemory:
    """保存成功探索路径，让 Agent 下次能像人一样复用经验。"""

    def __init__(self, path: Path = EXPLORATION_MEMORY_PATH):
        self.path = path
        self.records: list[ExplorationRecord] = []
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
            steps = [ExplorationStep(**step) for step in item.get("path", [])]
            item["path"] = steps
            records.append(ExplorationRecord(**item))
        self.records = records

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"records": [asdict(record) for record in self.records[-80:]]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, task: str, result: str, path: list[ExplorationStep]):
        if not result:
            return
        self.records.append(ExplorationRecord(task=task, result=result, path=deepcopy(path)))
        self.save()

    def _tokens(self, text: str) -> set[str]:
        text = text.lower()
        chinese_chars = set(re.findall(r"[\u4e00-\u9fff]", text))
        latin_tokens = set(re.findall(r"[a-z0-9]+", text))
        return chinese_chars | latin_tokens

    def search(self, task: str, limit: int = 3) -> list[dict]:
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

    def _classify_page_phase(self, snapshot: dict) -> str:
        url = snapshot.get("url", "")
        text = snapshot.get("visible_text", "")
        fields = snapshot.get("fields") or []
        field_hints = " ".join(
            f"{field.get('hint', '')} {field.get('value', '')}"
            for field in fields
        )
        combined = f"{url} {text} {field_hints}"

        if "标题" in combined and ("正文" in combined or "发布" in combined or "暂存离开" in combined):
            return "note_editing"
        if "草稿箱(" in combined or "草稿箱（" in combined:
            return "publish_entry_with_drafts"
        if all(word in combined for word in ("全部", "已发布", "审核中")) and "笔记管理" in combined:
            return "note_management"
        if "草稿箱中有未发布的作品" in combined or "新的创作" in combined:
            return "creator_home"
        if "拖拽视频" in combined or "上传图文" in combined or "上传视频" in combined:
            return "publish_entry"
        if "creator.xiaohongshu.com" in url:
            return "creator_unknown"
        return "unknown"

    async def _page_snapshot(self, page, elements: list[dict], state: dict) -> dict:
        dom = await page.evaluate(
            """() => {
                const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
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
                return {text, fields};
            }"""
        )
        snapshot = {
            "url": page.url,
            "browser_state": summarize_browser_state(state),
            "visible_text": _compact_text(dom.get("text", ""), 2600),
            "fields": dom.get("fields", []),
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
    ) -> str:
        return f"""
你是小红书创作中心的页面探索 Agent。你不是固定流程脚本，而是在网页里像人一样观察、尝试、返回、复盘。

用户目标：
{user_goal}

你可以选择的动作：
- click: 点击一个可交互元素，必须给 element_index
- click_text_in_element: 点击某个元素内部的指定文字，必须给 element_index 和 text；当目标按钮文字在卡片/列表项内部但没有独立元素索引时使用，例如点击第三篇草稿卡片里的“删除”
- fill: 填写一个输入元素，必须给 element_index 和 value
- fill_title: 修改当前编辑页标题，必须给 value；当用户明确要修改标题时优先使用
- fill_content: 修改当前编辑页正文，必须给 value；当用户明确要修改正文时优先使用
- back: 返回上一页
- wait: 等待页面加载或响应
- extract_answer: 当前页面已经包含用户要的信息，提取并返回 answer
- done: 任务已完成，返回 answer
- fail: 多次探索仍无法完成，说明原因

行为规则：
- 如果任务没有现成 skill，要先观察页面文字和可交互元素，按最可能路径小步探索。
- 不要连续重复点击同一个无响应元素；无进展时优先换路径或 back。
- 如果用户要“较早/最早”的草稿，通常需要进入草稿箱/笔记管理/发布入口后比较列表顺序或时间信息。
- 如果用户要正文内容，打开目标草稿后优先从正文编辑框、contenteditable 或页面字段中提取。
- 如果用户要修改标题，且当前 page_phase=note_editing，优先使用 fill_title，不要对侧边栏或页面容器使用 fill。
- 如果用户要修改正文，且当前 page_phase=note_editing，优先使用 fill_content，不要对标题框或页面容器使用 fill。
- 如果用户目标涉及删除、移除、清空、撤回：必须先定位与用户描述匹配的目标对象；不要删除不匹配对象；如果页面出现二次确认弹窗，只有在目标对象明确匹配时才点击确认。
- 如果用户要求删除“保存于某个时间”的帖子/草稿/笔记，要把保存时间视为目标对象的唯一定位线索：先找到包含该保存时间的那条记录，再选择该记录对应的删除按钮；如果当前还没看到记录列表，先进入草稿箱/图文笔记列表。
- 如果某篇草稿/帖子作为一个整体卡片元素出现，且卡片文本里包含“编辑/删除”，但“删除”没有单独元素索引，使用 click_text_in_element，element_index 取目标卡片索引，text 取“删除”。
- 必须参考“本地行动记忆”。如果里面记录某个动作没有达到目标，不要重复同一路径；如果某个动作暴露了目标线索，优先沿着那个线索继续。
- 必须参考“来自 xhs_agent_worklog.json 的历史成功经验”中的 user_request、reuse_level、match_score、request_match_ratio、request_overlap_terms、match_ratio 和 overlap_terms。user_request 是当时用户的原始请求；复用前必须先判断它和当前用户目标是否语义一致或高度相关。
- reuse_level=same_goal_candidate 时，可以把 steps 当作已验证路线优先参考；reuse_level=context_reference_only 或 weak_context_only 时，只能把它当作页面路径/线索，不能照搬最终动作。
- 如果历史 user_request 只是共享了少量词汇，但目标不同，不要照搬它的步骤；如果语义一致，可以把 steps 当作已验证路线来优先参考。
- 成功完成后用 done 或 extract_answer，不要继续乱点。
- 只输出 JSON，不要 Markdown。
- 严禁输出思考过程、分析文字、解释文字；回复的第一个字符必须是 {{，最后一个字符必须是 }}。

JSON 格式：
{{
  "action": "click|click_text_in_element|fill|fill_title|fill_content|back|wait|extract_answer|done|fail",
  "element_index": 0,
  "text": "",
  "value": "",
  "answer": "",
  "reason": "为什么这样做"
}}

历史动作：
{json.dumps([asdict(step) for step in history[-8:]], ensure_ascii=False, indent=2)}

本轮已尝试路径摘要：
{json.dumps(self._history_lessons(history), ensure_ascii=False, indent=2)}

可复用的历史成功路径：
{json.dumps(memory_hits, ensure_ascii=False, indent=2)}

本地行动记忆（动作后的页面响应摘要）：
{json.dumps(trace_notes[-12:], ensure_ascii=False, indent=2)}

来自 xhs_agent_worklog.json 的历史成功经验：
{json.dumps(worklog_hints[-6:], ensure_ascii=False, indent=2)}
先比较每条历史经验的 user_request 与当前用户目标是否同义或高度相关，再决定是否复用；不要因为 overlap_terms 有重合就盲目复用。
reuse_level=same_goal_candidate 才表示可能是同类任务；context_reference_only/weak_context_only 只能作为页面路径线索。

当前页面快照：
{json.dumps(snapshot, ensure_ascii=False, indent=2)}
""".strip()

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
    ) -> dict:
        prompt = self._planner_prompt(user_goal, snapshot, history, memory_hits, trace_notes, worklog_hints)
        response = _client().chat.completions.create(
            model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MODEL_CONFIG.get("planner_max_tokens", 1000),
            temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
        )
        raw_text = _extract_response_text(response)
        _debug_response("页面探索主规划模型", response, raw_text)
        parsed = _extract_json(raw_text)
        if _is_valid_action(parsed):
            return parsed

        retry_action = await self._retry_plan_action(user_goal, snapshot, history, trace_notes, worklog_hints, raw_text)
        if _is_valid_action(retry_action):
            return retry_action

        print(f"页面探索模型返回不可解析，尝试修复。原始返回预览：{_compact_text(raw_text, 500)}")
        repaired = await self._repair_action(user_goal, snapshot, history, trace_notes, worklog_hints, raw_text)
        if _is_valid_action(repaired):
            return repaired

        return {
            "action": "fail",
            "reason": (
                "页面探索模型和动作修复模型都没有返回合法动作。"
                f"原始返回预览：{_compact_text(raw_text, 300)}"
            ),
        }

    async def _retry_plan_action(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        trace_notes: list[dict],
        worklog_hints: list[dict],
        raw_text: str,
    ) -> dict | None:
        compact_context = self._compact_action_context(
            user_goal=user_goal,
            snapshot=snapshot,
            history=history,
            trace_notes=trace_notes,
            worklog_hints=worklog_hints,
        )
        retry_prompt = f"""
你是小红书页面操作决策模型。上一轮模型没有返回可解析动作。
请在内部根据“压缩页面上下文”分析下一步应该做什么，但最终只输出一个 JSON 动作。
严禁输出思考过程、分析文字、解释文字；回复的第一个字符必须是 {{，最后一个字符必须是 }}。

用户目标：{user_goal}

上一轮模型输出不可解析：
{_compact_text(raw_text, 300) or "<empty>"}

压缩页面上下文：
{compact_context}

决策要求：
- 你必须根据 user_goal、visible_text、fields 和 interactive_elements 自己推理下一步。
- interactive_elements 中的 index 是真实点击索引；选择 click 时必须使用它。
- 如果用户要求删除某篇帖子/草稿/笔记，要先根据标题、保存时间、列表顺序等线索定位目标，再选择目标对象对应的删除按钮；如果还没进入列表，先选择能进入列表的入口。
- 如果目标删除按钮在目标草稿卡片内部但没有独立元素索引，输出 click_text_in_element，element_index 使用目标卡片索引，text 使用“删除”。
- 如果页面出现删除确认语义，再选择确认/确定类按钮；不要点取消。
- 如果历史经验 reuse_level 不是 same_goal_candidate，只能参考页面路径，不要照搬最终动作。
- 不要返回空内容。

合法动作只能是：
{{"action":"click","element_index":数字,"reason":"..."}}
{{"action":"click_text_in_element","element_index":数字,"text":"删除","reason":"..."}}
{{"action":"fill","element_index":数字,"value":"...","reason":"..."}}
{{"action":"fill_title","value":"...","reason":"..."}}
{{"action":"fill_content","value":"...","reason":"..."}}
{{"action":"done","answer":"...","reason":"..."}}
{{"action":"back","reason":"..."}}
{{"action":"wait","seconds":1.5,"reason":"..."}}
{{"action":"extract_answer","answer":"...","reason":"..."}}
{{"action":"fail","reason":"..."}}
""".strip()
        try:
            response = _client().chat.completions.create(
                model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
                messages=[{"role": "user", "content": retry_prompt}],
                max_tokens=350,
                temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
            )
        except Exception as exc:
            print(f"页面探索短提示重试失败：{exc}")
            return None
        retry_raw = _extract_response_text(response)
        _debug_response("页面探索短提示模型", response, retry_raw)
        retry_parsed = _extract_json(retry_raw)
        if not _is_valid_action(retry_parsed):
            print(f"页面探索短提示重试仍不可解析。返回预览：{_compact_text(retry_raw, 300)}")
        return retry_parsed

    async def _repair_action(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        trace_notes: list[dict],
        worklog_hints: list[dict],
        raw_text: str,
    ) -> dict | None:
        compact_context = self._compact_action_context(
            user_goal=user_goal,
            snapshot=snapshot,
            history=history,
            trace_notes=trace_notes,
            worklog_hints=worklog_hints,
        )
        repair_prompt = f"""
你是 JSON 动作修复器。上一轮页面探索模型的输出无法被程序解析。

请根据用户目标、压缩页面上下文和上一轮原始输出，重新给出一个合法动作。
只输出一个 JSON 对象，不要 Markdown，不要解释。
严禁输出思考过程、分析文字、解释文字；回复的第一个字符必须是 {{，最后一个字符必须是 }}。

合法动作：
{{"action":"click","element_index":0,"reason":"..."}}
{{"action":"click_text_in_element","element_index":0,"text":"删除","reason":"..."}}
{{"action":"fill","element_index":0,"value":"...","reason":"..."}}
{{"action":"fill_title","value":"...","reason":"..."}}
{{"action":"fill_content","value":"...","reason":"..."}}
{{"action":"back","reason":"..."}}
{{"action":"wait","seconds":1.5,"reason":"..."}}
{{"action":"extract_answer","answer":"...","reason":"..."}}
{{"action":"done","answer":"...","reason":"..."}}
{{"action":"fail","reason":"..."}}

用户目标：{user_goal}

历史动作：
{json.dumps([asdict(step) for step in history[-5:]], ensure_ascii=False, indent=2)}

本轮已尝试路径摘要：
{json.dumps(self._history_lessons(history), ensure_ascii=False, indent=2)}

本地行动记忆：
{json.dumps(trace_notes[-12:], ensure_ascii=False, indent=2)}

来自 xhs_agent_worklog.json 的历史成功经验：
{json.dumps(worklog_hints[-6:], ensure_ascii=False, indent=2)}
历史成功经验里的 user_request 是当时用户原始请求。必须先判断 user_request 与当前用户目标是否语义一致或高度相关；如果只是词汇重合但目标不同，不要复用。
reuse_level=same_goal_candidate 才表示可能是同类任务；context_reference_only/weak_context_only 只能作为页面路径线索。

压缩页面上下文：
{compact_context}

额外要求：
- 你必须自己分析 user_goal 与 interactive_elements 的关系。
- click 动作必须使用 interactive_elements 里真实存在的 index。
- 用户要求删除具体帖子/草稿时，应根据标题、保存时间、列表顺序等线索定位目标，再选择目标对象对应的删除按钮；如果还没进入列表，先选择能进入列表的入口。
- 如果目标删除按钮在目标草稿卡片内部但没有独立元素索引，输出 click_text_in_element，element_index 使用目标卡片索引，text 使用“删除”。
- 不要返回空内容。

上一轮原始输出：
{raw_text}
""".strip()
        try:
            response = _client().chat.completions.create(
                model=MODEL_CONFIG.get("formatter_model") or MODEL_CONFIG.get("content_model"),
                messages=[{"role": "user", "content": repair_prompt}],
                max_tokens=800,
                temperature=MODEL_CONFIG.get("formatter_temperature", 0.1),
            )
        except Exception as exc:
            print(f"动作修复模型调用失败：{exc}")
            return None
        repair_raw = _extract_response_text(response)
        _debug_response("页面探索动作修复模型", response, repair_raw)
        repaired = _extract_json(repair_raw)
        if not _is_valid_action(repaired):
            print(f"动作修复模型仍不可解析。返回预览：{_compact_text(repair_raw, 500)}", flush=True)
        return repaired

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
                "page_phase": snapshot.get("page_phase", ""),
                "browser_state": snapshot.get("browser_state", ""),
                "visible_text": _compact_text(snapshot.get("visible_text", ""), 1800),
                "fields": snapshot.get("fields", [])[:12],
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
        prompt = f"""
你是网页探索日志压缩器。请总结这一步动作对完成用户目标是否有帮助。

要求：
- 输出 1 到 3 句中文。
- 必须说明：点了什么、页面发生了什么、下一步应避免或利用什么。
- 不要泛泛而谈，不要超过 180 字。

用户目标：{user_goal}
动作：{json.dumps(action, ensure_ascii=False)}
点击/填写对象：{element_text}
执行结果：{result}

动作前页面摘要：
{json.dumps(self._snapshot_brief(before_snapshot), ensure_ascii=False, indent=2)}

动作后页面摘要：
{json.dumps(self._snapshot_brief(after_snapshot), ensure_ascii=False, indent=2)}
""".strip()
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
    ) -> dict:
        self.trace.clear()
        history: list[ExplorationStep] = []
        memory_hits = self.memory.search(user_goal)
        trace_notes = []
        worklog_hints = worklog_hints or []
        last_action_key = ""
        repeated_no_response = 0

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
            self._print_snapshot_debug(snapshot)
            action = await self._plan_action(user_goal, snapshot, history, memory_hits, trace_notes, worklog_hints)
            action_name = action.get("action")
            reason = action.get("reason", "")
            print(f"探索步骤 {step_index}: {action}")

            if action_name in {"done", "extract_answer"}:
                answer = action.get("answer") or snapshot.get("visible_text", "")
                self.memory.add(user_goal, answer, history)
                return {"success": True, "answer": answer, "steps": [asdict(step) for step in history]}

            if action_name == "fail":
                return {
                    "success": False,
                    "answer": action.get("answer") or reason or "探索失败",
                    "steps": [asdict(step) for step in history],
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
                if action_name == "click":
                    index = action.get("element_index")
                    if index is None or int(index) >= len(elements):
                        result = "点击索引无效"
                    else:
                        element = elements[int(index)]
                        element_text = element.get("text") or element.get("desc") or ""
                        await click_by_index(page, int(index), elements)
                        result = f"已点击 {element_text}"
                elif action_name == "click_text_in_element":
                    index = action.get("element_index")
                    target_text = action.get("text") or action.get("value") or ""
                    if index is None or int(index) >= len(elements):
                        result = "内部文本点击索引无效"
                    else:
                        element = elements[int(index)]
                        element_text = element.get("text") or element.get("desc") or ""
                        await click_text_in_element(page, int(index), target_text, elements)
                        result = f"已点击 {element_text} 内部文本：{target_text}"
                elif action_name == "fill_title":
                    value = action.get("value", "")
                    element_text = "标题输入框"
                    success = await fill_title_direct(page, value)
                    result = f"{'已修改标题' if success else '标题修改失败'}：{value}"
                elif action_name == "fill_content":
                    value = action.get("value", "")
                    element_text = "正文编辑区"
                    success = await fill_content_direct(page, value)
                    result = f"{'已修改正文' if success else '正文修改失败'}"
                elif action_name == "fill":
                    index = action.get("element_index")
                    value = action.get("value", "")
                    if index is None or int(index) >= len(elements):
                        result = "填写索引无效"
                    else:
                        element = elements[int(index)]
                        element_text = element.get("text") or element.get("desc") or ""
                        await fill_by_index(page, int(index), value, elements)
                        result = f"已填写 {element_text}"
                elif action_name == "back":
                    await go_back(page)
                    result = "已返回上一页"
                elif action_name == "wait":
                    await asyncio.sleep(float(action.get("seconds") or 1.5))
                    result = "已等待"
                else:
                    result = f"未知动作 {action_name}"
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

            if repeated_no_response >= 1:
                print("探索检测到重复无响应，下一步会让模型换路径或返回。")

        return {
            "success": False,
            "answer": "达到最大探索步数，未能可靠完成任务。",
            "steps": [asdict(step) for step in history],
        }
