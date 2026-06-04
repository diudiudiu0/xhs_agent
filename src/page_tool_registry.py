from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.browser_tools import (
    click_by_index,
    click_media_near_text,
    click_near_text,
    click_save_and_leave,
    click_semantic_target,
    click_text_in_element,
    fill_by_index,
    fill_content_direct,
    fill_textbox_by_hint,
    fill_title_direct,
    go_back,
)
from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE_TOOL_CONFIG_PATH = PROJECT_ROOT / "cfg" / "page_tools.yaml"


@dataclass
class PageToolSpec:
    name: str
    description: str
    category: str = "browser_action"
    required: list[str] = field(default_factory=list)
    required_any: list[list[str]] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    use_when: list[str] = field(default_factory=list)
    avoid_when: list[str] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "PageToolSpec":
        def normalize_group_values(values) -> list[list[str]]:
            groups = []
            if not values:
                return groups
            if all(isinstance(item, str) for item in values):
                # YAML 写成 required_any: ["a", "b"] 时表示一个 any 组。
                if len(values) == 1:
                    try:
                        parsed = json.loads(values[0])
                        if isinstance(parsed, list):
                            return [[str(item) for item in parsed]]
                    except Exception:
                        pass
                return [list(values)]
            for item in values:
                if isinstance(item, list):
                    groups.append([str(value) for value in item])
                elif isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, list):
                            groups.append([str(value) for value in parsed])
                        else:
                            groups.append([item])
                    except Exception:
                        groups.append([item])
            return groups

        def normalize_examples(values) -> list[dict[str, Any]]:
            examples = []
            for item in values or []:
                if isinstance(item, dict) and "action" in item:
                    examples.append(item)
                elif isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            examples.append(parsed)
                    except Exception:
                        continue
            return examples

        required_any = []
        for key in ("required_any", "required_any_groups"):
            required_any.extend(normalize_group_values(config.get(key) or []))
        return cls(
            name=str(config.get("name") or "").strip(),
            description=str(config.get("description") or "").strip(),
            category=str(config.get("category") or "browser_action").strip(),
            required=list(config.get("required") or []),
            required_any=required_any,
            optional=list(config.get("optional") or []),
            use_when=list(config.get("use_when") or []),
            avoid_when=list(config.get("avoid_when") or []),
            examples=normalize_examples(config.get("examples") or []),
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        data = {
            "action": self.name,
            "description": self.description,
            "category": self.category,
        }
        if self.required:
            data["required"] = self.required
        if self.required_any:
            data["required_any"] = self.required_any
        if self.optional:
            data["optional"] = self.optional
        if self.use_when:
            data["use_when"] = self.use_when
        if self.avoid_when:
            data["avoid_when"] = self.avoid_when
        if self.examples:
            data["examples"] = self.examples[:2]
        return data


@dataclass
class PageToolContext:
    page: Any
    elements: list[dict[str, Any]]
    switch_page: Callable[[str], Awaitable[Any]] | None = None


@dataclass
class PageToolResult:
    success: bool
    message: str
    element_text: str = ""
    page: Any = None


Executor = Callable[[PageToolContext, dict[str, Any]], Awaitable[PageToolResult]]


class PageToolRegistry:
    def __init__(self):
        self._specs: dict[str, PageToolSpec] = {}
        self._executors: dict[str, Executor] = {}

    def register(self, spec: PageToolSpec, executor: Executor | None = None):
        if not spec.name:
            raise ValueError("Page tool name cannot be empty.")
        if spec.name in self._specs:
            raise ValueError(f"Duplicate page tool registered: {spec.name}")
        self._specs[spec.name] = spec
        if executor is not None:
            self._executors[spec.name] = executor

    def names(self) -> list[str]:
        return sorted(self._specs)

    def spec(self, name: str) -> PageToolSpec:
        return self._specs[name]

    def is_control(self, name: str) -> bool:
        spec = self._specs.get(name)
        return bool(spec and spec.category == "control")

    def validate(self, action: Any) -> bool:
        if not isinstance(action, dict):
            return False
        name = action.get("action")
        if name not in self._specs:
            return False
        spec = self._specs[name]
        for key in spec.required:
            if action.get(key) is None:
                return False
        for group in spec.required_any:
            if not any(action.get(key) is not None and action.get(key) != "" for key in group):
                return False
        return True

    async def execute(self, name: str, context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
        if name not in self._executors:
            raise KeyError(f"Page tool has no executor: {name}")
        return await self._executors[name](context, action)

    def render_prompt_catalog(self) -> str:
        prompt_items = [self._specs[name].to_prompt_dict() for name in self.names()]
        return json.dumps(prompt_items, ensure_ascii=False, indent=2)

    def render_json_rules(self) -> str:
        lines = [
            "- 只输出一个最小 JSON 对象，不要输出无用空字段。",
            "- 所有动作都必须包含 action、requires_user_confirmation、reason。",
            "- reason 不超过 30 个中文字符。",
        ]
        for name in self.names():
            spec = self._specs[name]
            parts = []
            if spec.required:
                parts.append(f"required={spec.required}")
            if spec.required_any:
                parts.append(f"required_any={spec.required_any}")
            if spec.optional:
                parts.append(f"optional={spec.optional}")
            if parts:
                lines.append(f"- {name}: " + "；".join(parts))
            else:
                lines.append(f"- {name}: 无必填参数。")
        return "\n".join(lines)


def _text_arg(action: dict[str, Any], *names: str) -> str:
    for name in names:
        value = action.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


async def _execute_click(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    index = int(action.get("element_index"))
    if index < 0 or index >= len(context.elements):
        return PageToolResult(False, "点击索引无效")
    element = context.elements[index]
    element_text = element.get("text") or element.get("desc") or ""
    await click_by_index(context.page, index, context.elements)
    return PageToolResult(True, f"已点击 {element_text}", element_text=element_text)


async def _execute_click_semantic_target(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    target_text = _text_arg(action, "text", "value")
    element_text = f"语义目标：{target_text}"
    success = await click_semantic_target(
        context.page,
        target_text,
        intent=action.get("intent", ""),
        avoid_texts=action.get("avoid_texts") or [],
        event_names=action.get("event_names") or [],
    )
    return PageToolResult(
        success,
        f"{'已点击语义目标' if success else '语义目标点击失败'}：{target_text}",
        element_text=element_text,
    )


async def _execute_click_text_in_element(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    index = int(action.get("element_index"))
    target_text = _text_arg(action, "text", "value")
    if index < 0 or index >= len(context.elements):
        return PageToolResult(False, "内部文本点击索引无效")
    element = context.elements[index]
    element_text = element.get("text") or element.get("desc") or ""
    await click_text_in_element(context.page, index, target_text, context.elements)
    return PageToolResult(True, f"已点击 {element_text} 内部文本：{target_text}", element_text=element_text)


async def _execute_click_near_text(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    near_text = _text_arg(action, "near_text", "nearText", "anchor_text", "scope_text")
    target_text = _text_arg(action, "text", "target_text", "value")
    element_text = f"near={near_text}; target={target_text}"
    success = await click_near_text(
        context.page,
        near_text,
        target_text,
        intent=action.get("intent", ""),
        avoid_texts=action.get("avoid_texts") or [],
    )
    return PageToolResult(
        success,
        f"{'已点击附近目标' if success else '附近目标点击失败'}：{element_text}",
        element_text=element_text,
    )


async def _execute_click_media_near_text(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    near_text = _text_arg(action, "near_text", "nearText", "anchor_text", "scope_text")
    element_text = f"near={near_text}; media"
    success = await click_media_near_text(
        context.page,
        near_text,
        intent=action.get("intent", ""),
        avoid_texts=action.get("avoid_texts") or [],
    )
    return PageToolResult(
        success,
        f"{'已点击锚点附近媒体' if success else '锚点附近媒体点击失败'}：{element_text}",
        element_text=element_text,
    )


async def _execute_fill(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    index = int(action.get("element_index"))
    value = str(action.get("value", ""))
    if index < 0 or index >= len(context.elements):
        return PageToolResult(False, "填写索引无效")
    element = context.elements[index]
    element_text = element.get("text") or element.get("desc") or ""
    await fill_by_index(context.page, index, value, context.elements)
    return PageToolResult(True, f"已填写 {element_text}", element_text=element_text)


async def _execute_fill_textbox(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    value = str(action.get("value", ""))
    hint_text = _text_arg(action, "hint_text", "hintText", "near_text", "text")
    element_text = f"动态输入框：{hint_text or '当前焦点'}"
    success = await fill_textbox_by_hint(
        context.page,
        value,
        hint_text=hint_text,
        prefer_focused=bool(action.get("prefer_focused", True)),
    )
    return PageToolResult(
        success,
        "已填写动态输入框" if success else "动态输入框填写失败",
        element_text=element_text,
    )


async def _execute_fill_title(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    value = str(action.get("value", ""))
    success = await fill_title_direct(context.page, value)
    return PageToolResult(success, f"{'已修改标题' if success else '标题修改失败'}：{value}", element_text="标题输入框")


async def _execute_fill_content(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    value = str(action.get("value", ""))
    success = await fill_content_direct(context.page, value)
    return PageToolResult(success, "已修改正文" if success else "正文修改失败", element_text="正文编辑区")


async def _execute_save_and_leave(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    success = await click_save_and_leave(context.page)
    return PageToolResult(
        success,
        "已执行暂存离开，草稿已保存" if success else "暂存离开工具未找到可点击的保存按钮",
        element_text="暂存离开/保存草稿专用工具",
    )


async def _execute_switch_site(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    target_site = _text_arg(action, "target_site", "site", "value")
    element_text = f"切换站点：{target_site}"
    if context.switch_page is None:
        return PageToolResult(False, "站点切换失败：当前探索器没有可用的 switch_page 回调", element_text=element_text)
    page = await context.switch_page(target_site)
    return PageToolResult(True, f"已切换站点：{target_site} -> {page.url}", element_text=element_text, page=page)


async def _execute_back(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    await go_back(context.page)
    return PageToolResult(True, "已返回上一页")


async def _execute_wait(context: PageToolContext, action: dict[str, Any]) -> PageToolResult:
    await asyncio.sleep(float(action.get("seconds") or 1.5))
    return PageToolResult(True, "已等待")


EXECUTORS: dict[str, Executor] = {
    "click": _execute_click,
    "click_semantic_target": _execute_click_semantic_target,
    "click_text_in_element": _execute_click_text_in_element,
    "click_near_text": _execute_click_near_text,
    "click_media_near_text": _execute_click_media_near_text,
    "fill": _execute_fill,
    "fill_textbox": _execute_fill_textbox,
    "fill_title": _execute_fill_title,
    "fill_content": _execute_fill_content,
    "save_and_leave": _execute_save_and_leave,
    "switch_site": _execute_switch_site,
    "back": _execute_back,
    "wait": _execute_wait,
}


def build_page_tool_registry(path: Path = PAGE_TOOL_CONFIG_PATH) -> PageToolRegistry:
    data = _safe_load_yaml_file(path)
    tools = data.get("page_tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("cfg/page_tools.yaml 中 page_tools 必须是非空列表")
    registry = PageToolRegistry()
    for item in tools:
        spec = PageToolSpec.from_config(item)
        registry.register(spec, EXECUTORS.get(spec.name))
    return registry


PAGE_TOOL_REGISTRY = build_page_tool_registry()
