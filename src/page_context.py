from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from openai import OpenAI

from cfg.model_config import PAGE_CONTEXT_MODEL_CONFIG
from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE_CONTEXT_CONFIG_PATH = PROJECT_ROOT / "cfg" / "page_context.yaml"


def _compact_text(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


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
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _client() -> OpenAI:
    return OpenAI(
        api_key=PAGE_CONTEXT_MODEL_CONFIG["api_key"],
        base_url=PAGE_CONTEXT_MODEL_CONFIG["base_url"],
        timeout=PAGE_CONTEXT_MODEL_CONFIG.get("timeout", 30),
    )


def _load_config() -> dict:
    data = _safe_load_yaml_file(PAGE_CONTEXT_CONFIG_PATH)
    config = data.get("page_context") or {}
    if not isinstance(config, dict):
        raise ValueError("cfg/page_context.yaml 中 page_context 必须是字典")
    return config


def _normalize_context(value: dict | None, default_context: dict) -> dict:
    context = deepcopy(default_context)
    if isinstance(value, dict):
        context.update(value)
    context["schema_version"] = context.get("schema_version") or default_context.get("schema_version", 1)
    context["target"] = context.get("target") if isinstance(context.get("target"), dict) else {}
    context["collected"] = context.get("collected") if isinstance(context.get("collected"), dict) else {}
    context["ui_state"] = context.get("ui_state") if isinstance(context.get("ui_state"), dict) else {}
    missing = context.get("missing")
    if isinstance(missing, list):
        context["missing"] = [str(item) for item in missing if str(item).strip()]
    elif isinstance(missing, str) and missing.strip() in {"", "[]"}:
        context["missing"] = []
    elif missing:
        context["missing"] = [str(missing)]
    else:
        context["missing"] = []
    navigation_path = context.get("navigation_path")
    if isinstance(navigation_path, list):
        normalized_path = []
        for item in navigation_path:
            if isinstance(item, dict):
                normalized_path.append(
                    {
                        "from": _compact_text(item.get("from", ""), 120),
                        "action": _compact_text(item.get("action", ""), 120),
                        "to": _compact_text(item.get("to", ""), 120),
                        "effect": _compact_text(item.get("effect", ""), 220),
                    }
                )
        context["navigation_path"] = normalized_path[-10:]
    else:
        context["navigation_path"] = []
    for key in ("site", "page_phase", "current_url", "current_task", "task_stage", "last_action_effect"):
        context[key] = str(context.get(key) or "")
    if not context["task_stage"]:
        context["task_stage"] = "not_started"
    return context


def _page_label(snapshot: dict | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    site = str(snapshot.get("site") or "").strip()
    phase = str(snapshot.get("page_phase") or "").strip()
    url = str(snapshot.get("url") or snapshot.get("current_url") or "").strip()
    label = " / ".join(part for part in (site, phase) if part)
    if url:
        label = f"{label} @ {url}" if label else url
    return _compact_text(label, 160)


def _action_label(action: dict | None) -> str:
    if not isinstance(action, dict):
        return ""
    name = str(action.get("action") or "").strip()
    parts = [name] if name else []
    for key in ("element_index", "text", "near_text", "target_text", "value"):
        value = action.get(key)
        if value is not None and str(value).strip():
            parts.append(f"{key}={_compact_text(value, 60)}")
    return _compact_text(" ".join(parts), 160)


class PageContextManager:
    """Maintain short-lived structured context for one page exploration task."""

    def __init__(self, config_path: Path = PAGE_CONTEXT_CONFIG_PATH):
        self.config_path = config_path
        self.config = _load_config()
        self.default_context = _normalize_context(
            self.config.get("default_context") or {},
            {
                "schema_version": 1,
                "site": "",
                "page_phase": "",
                "current_url": "",
                "current_task": "",
                "task_stage": "not_started",
                "target": {},
                "collected": {},
                "ui_state": {},
                "missing": [],
                "last_action_effect": "",
                "navigation_path": [],
            },
        )
        self.context = deepcopy(self.default_context)

    def reset(self, user_goal: str = "", snapshot: dict | None = None) -> dict:
        self.context = deepcopy(self.default_context)
        self.context["current_task"] = str(user_goal or "")
        self.context["task_stage"] = "not_started"
        self.apply_snapshot(snapshot or {})
        return self.context

    def apply_snapshot(self, snapshot: dict) -> dict:
        if not isinstance(snapshot, dict):
            return self.context
        self.context["site"] = str(snapshot.get("site") or self.context.get("site") or "")
        self.context["page_phase"] = str(snapshot.get("page_phase") or self.context.get("page_phase") or "")
        self.context["current_url"] = str(snapshot.get("url") or self.context.get("current_url") or "")
        return self.context

    def _append_navigation_step(
        self,
        action: dict,
        result: str,
        observation: str,
        before_snapshot: dict,
        after_snapshot: dict,
    ) -> None:
        path = self.context.get("navigation_path")
        if not isinstance(path, list):
            path = []
        entry = {
            "from": _page_label(before_snapshot),
            "action": _action_label(action),
            "to": _page_label(after_snapshot),
            "effect": _compact_text(observation or result, 220),
        }
        if not any(entry.values()):
            return
        path.append(entry)
        self.context["navigation_path"] = path[-10:]

    def render(self, limit: int = 2200) -> str:
        return _compact_text(json.dumps(self.context, ensure_ascii=False, indent=2), limit)

    def _fallback_update(
        self,
        user_goal: str,
        action: dict,
        result: str,
        observation: str,
        before_snapshot: dict,
        after_snapshot: dict,
    ) -> dict:
        self.context["current_task"] = self.context.get("current_task") or str(user_goal or "")
        self.apply_snapshot(after_snapshot)
        self.context["last_action_effect"] = _compact_text(observation or result, 300)
        self._append_navigation_step(action, result, observation, before_snapshot, after_snapshot)
        action_name = action.get("action") if isinstance(action, dict) else ""
        if action_name == "done":
            self.context["task_stage"] = "done"
            self.context["missing"] = []
        elif action_name == "fail":
            self.context["task_stage"] = "failed"
        elif "回复" in self.context["last_action_effect"] and "输入框" in self.context["last_action_effect"]:
            self.context["task_stage"] = "reply_box_opened"
            self.context.setdefault("ui_state", {})["reply_box_visible"] = True
        return self.context

    def update(
        self,
        user_goal: str,
        action: dict,
        result: str,
        observation: str,
        before_snapshot: dict,
        after_snapshot: dict,
    ) -> dict:
        self.apply_snapshot(after_snapshot)
        previous_path = list(self.context.get("navigation_path") or [])
        prompt_template = self.config.get("update_prompt_template") or ""
        if not prompt_template:
            return self._fallback_update(user_goal, action, result, observation, before_snapshot, after_snapshot)

        prompt = prompt_template.format(
            user_goal=_compact_text(user_goal, 1000),
            old_context=self.render(limit=2400),
            action=json.dumps(action or {}, ensure_ascii=False),
            result=_compact_text(result, 800),
            observation=_compact_text(observation, 800),
            before_snapshot=json.dumps(before_snapshot or {}, ensure_ascii=False, indent=2)[:3000],
            after_snapshot=json.dumps(after_snapshot or {}, ensure_ascii=False, indent=2)[:3000],
        )

        try:
            response = _client().chat.completions.create(
                model=PAGE_CONTEXT_MODEL_CONFIG.get("model", "deepseek-v4-flash"),
                messages=[
                    {"role": "system", "content": self.config.get("system_prompt", "")},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=PAGE_CONTEXT_MODEL_CONFIG.get("max_tokens", 1800),
                temperature=PAGE_CONTEXT_MODEL_CONFIG.get("temperature", 0.1),
            )
            raw_text = response.choices[0].message.content or ""
            parsed = _extract_json(raw_text)
            if not parsed:
                raise ValueError(f"page_context 模型未返回合法 JSON：{_compact_text(raw_text, 300)}")
            self.context = _normalize_context(parsed, self.default_context)
            self.context["navigation_path"] = previous_path
            self.apply_snapshot(after_snapshot)
            self._append_navigation_step(action, result, observation, before_snapshot, after_snapshot)
            if not self.context.get("current_task"):
                self.context["current_task"] = str(user_goal or "")
            return self.context
        except Exception as exc:
            print(f"page_context 更新失败，使用本地兜底：{exc}")
            return self._fallback_update(user_goal, action, result, observation, before_snapshot, after_snapshot)

    def brief(self) -> str:
        target = self.context.get("target") or {}
        collected = self.context.get("collected") or {}
        ui_state = self.context.get("ui_state") or {}
        return (
            f"stage={self.context.get('task_stage')} "
            f"site={self.context.get('site')} "
            f"phase={self.context.get('page_phase')} "
            f"missing={self.context.get('missing', [])} "
            f"target_keys={list(target)[:6]} "
            f"collected_keys={list(collected)[:6]} "
            f"ui_keys={list(ui_state)[:6]} "
            f"path_len={len(self.context.get('navigation_path') or [])}"
        )
