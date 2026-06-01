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
from src.core_function.browser_skills import click_by_index, fill_by_index, go_back
from src.core_function.browser_state_observer import (
    observe_browser_state,
    summarize_browser_state,
    wait_for_browser_feedback,
)
from src.core_function.element_extractor import extract_interactive_elements


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPLORATION_MEMORY_PATH = PROJECT_ROOT / "agent_memory" / "xhs_exploration_memory.json"


@dataclass
class ExplorationStep:
    action: str
    reason: str = ""
    element_text: str = ""
    result: str = ""
    page_url: str = ""


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


class XhsPageExplorer:
    """用于没有明确 skill 的页面任务：观察、试探、复盘并记录成功路径。"""

    def __init__(self, memory: ExplorationMemory | None = None):
        self.memory = memory or ExplorationMemory()

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
        return {
            "url": page.url,
            "browser_state": summarize_browser_state(state),
            "visible_text": _compact_text(dom.get("text", ""), 2600),
            "fields": dom.get("fields", []),
            "elements": [
                {
                    "index": item.get("index"),
                    "type": item.get("type"),
                    "text": item.get("text"),
                    "desc": item.get("desc"),
                }
                for item in elements[:60]
            ],
        }

    def _planner_prompt(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        memory_hits: list[dict],
    ) -> str:
        return f"""
你是小红书创作中心的页面探索 Agent。你不是固定流程脚本，而是在网页里像人一样观察、尝试、返回、复盘。

用户目标：
{user_goal}

你可以选择的动作：
- click: 点击一个可交互元素，必须给 element_index
- fill: 填写一个输入元素，必须给 element_index 和 value
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
- 成功完成后用 done 或 extract_answer，不要继续乱点。
- 只输出 JSON，不要 Markdown。

JSON 格式：
{{
  "action": "click|fill|back|wait|extract_answer|done|fail",
  "element_index": 0,
  "value": "",
  "answer": "",
  "reason": "为什么这样做"
}}

历史动作：
{json.dumps([asdict(step) for step in history[-8:]], ensure_ascii=False, indent=2)}

可复用的历史成功路径：
{json.dumps(memory_hits, ensure_ascii=False, indent=2)}

当前页面快照：
{json.dumps(snapshot, ensure_ascii=False, indent=2)}
""".strip()

    async def _plan_action(
        self,
        user_goal: str,
        snapshot: dict,
        history: list[ExplorationStep],
        memory_hits: list[dict],
    ) -> dict:
        response = _client().chat.completions.create(
            model=MODEL_CONFIG.get("planner_model", MODEL_CONFIG.get("content_model")),
            messages=[{"role": "user", "content": self._planner_prompt(user_goal, snapshot, history, memory_hits)}],
            max_tokens=MODEL_CONFIG.get("planner_max_tokens", 1000),
            temperature=MODEL_CONFIG.get("planner_temperature", 0.1),
        )
        parsed = _extract_json(response.choices[0].message.content or "")
        if not isinstance(parsed, dict) or not parsed.get("action"):
            return {"action": "fail", "reason": "页面探索模型没有返回合法动作。"}
        return parsed

    async def explore(self, page, user_goal: str, max_steps: int = 12) -> dict:
        history: list[ExplorationStep] = []
        memory_hits = self.memory.search(user_goal)
        last_action_key = ""
        repeated_no_response = 0

        for step_index in range(1, max_steps + 1):
            state = await observe_browser_state(page)
            if state.get("loading_phase") in {"dom_loading", "page_loading", "network_busy"}:
                print(f"探索步骤 {step_index}: 页面仍在加载，先等待")
                await asyncio.sleep(1.5)
                continue

            elements = await extract_interactive_elements(page, max_retries=1)
            snapshot = await self._page_snapshot(page, elements, state)
            action = await self._plan_action(user_goal, snapshot, history, memory_hits)
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
            result = ""
            element_text = ""
            action_key = f"{action_name}:{action.get('element_index')}:{action.get('value', '')[:40]}"

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
                )
            )

            if repeated_no_response >= 1:
                print("探索检测到重复无响应，下一步会让模型换路径或返回。")

        return {
            "success": False,
            "answer": "达到最大探索步数，未能可靠完成任务。",
            "steps": [asdict(step) for step in history],
        }
