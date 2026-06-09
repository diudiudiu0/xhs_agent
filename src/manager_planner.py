from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from cfg.model_config import MANAGER_MODEL_CONFIG
from skills.catalog import render_skill_catalog
from src.manager_config import load_manager_config, manager_config_get
from src.manager_state import ManagerState
from src.prompt_config import render_prompt_template


VALID_DECISION_TYPES = {"call_skill", "ask_user", "final_answer", "wait"}


def _client() -> OpenAI:
    return OpenAI(
        api_key=MANAGER_MODEL_CONFIG["api_key"],
        base_url=MANAGER_MODEL_CONFIG["base_url"],
        timeout=MANAGER_MODEL_CONFIG.get("timeout", 45),
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
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_text_from_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (_extract_text_from_value(item) for item in value) if part)
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


def _normalize_decision(decision: dict | None) -> dict:
    if not isinstance(decision, dict):
        return {
            "type": "final_answer",
            "message": str(manager_config_get("fallback_final_answer", "")),
            "reason": "manager_planner_empty_decision",
        }

    decision_type = decision.get("type")
    if decision_type not in VALID_DECISION_TYPES:
        decision_type = "final_answer"

    if decision_type == "call_skill":
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        allowed_actions = decision.get("allowed_actions")
        forbidden_actions = decision.get("forbidden_actions")
        return {
            "type": "call_skill",
            "skill_name": str(decision.get("skill_name") or ""),
            "sub_goal": str(decision.get("sub_goal") or ""),
            "scope": str(decision.get("scope") or ""),
            "success_criteria": str(decision.get("success_criteria") or ""),
            "allowed_actions": allowed_actions if isinstance(allowed_actions, list) else [],
            "forbidden_actions": forbidden_actions if isinstance(forbidden_actions, list) else [],
            "args": args,
            "requires_user_confirmation": bool(decision.get("requires_user_confirmation", False)),
            "confirmation_message": str(decision.get("confirmation_message") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    if decision_type == "ask_user":
        return {
            "type": "ask_user",
            "message": str(decision.get("message") or ""),
            "reason": str(decision.get("reason") or ""),
        }

    if decision_type == "wait":
        seconds = decision.get("seconds", 1)
        try:
            seconds = max(0.1, min(float(seconds), 10.0))
        except (TypeError, ValueError):
            seconds = 1
        return {
            "type": "wait",
            "seconds": seconds,
            "reason": str(decision.get("reason") or ""),
        }

    return {
        "type": "final_answer",
        "message": str(decision.get("message") or manager_config_get("fallback_final_answer", "")),
        "reason": str(decision.get("reason") or ""),
    }


class ManagerPlanner:
    def __init__(self):
        self.config = load_manager_config()

    def build_prompt(
        self,
        user_message: str,
        state: ManagerState,
        memory_hints: list[dict[str, Any]] | None = None,
        last_skill_result: dict[str, Any] | None = None,
    ) -> str:
        behavior_rules = "\n".join(f"- {item}" for item in self.config.get("behavior_rules", []))
        template = str(self.config.get("planner_prompt_template") or "")
        recent_steps = int(self.config.get("max_recent_steps") or 10)
        memory_package = {
            "usage": {
                "goal": "These memories were retrieved once from the user's overall goal and apply to the whole task.",
                "current_step": "These memories were retrieved for the current planning step only. Do not treat them as evidence about already completed steps.",
            },
            "items": memory_hints or [],
        }
        return render_prompt_template(
            template,
            system_prompt=self.config.get("system_prompt", ""),
            behavior_rules=behavior_rules,
            decision_schema=self.config.get("decision_schema", ""),
            user_message=user_message,
            manager_state_json=json.dumps(state.to_dict(recent_steps=recent_steps), ensure_ascii=False, indent=2),
            skill_catalog=render_skill_catalog(),
            memory_hints_json=json.dumps(memory_package, ensure_ascii=False, indent=2),
            last_skill_result_json=json.dumps(last_skill_result or state.last_skill_result or {}, ensure_ascii=False, indent=2),
        )

    def plan(
        self,
        user_message: str,
        state: ManagerState,
        memory_hints: list[dict[str, Any]] | None = None,
        last_skill_result: dict[str, Any] | None = None,
    ) -> dict:
        prompt = self.build_prompt(user_message, state, memory_hints, last_skill_result)
        response = _client().chat.completions.create(
            model=MANAGER_MODEL_CONFIG.get("model", "deepseek-v4-pro"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MANAGER_MODEL_CONFIG.get("max_tokens", 2200),
            temperature=MANAGER_MODEL_CONFIG.get("temperature", 0.2),
        )
        raw_text = _extract_response_text(response)
        parsed = _extract_json(raw_text)
        decision = _normalize_decision(parsed)
        if parsed is None:
            decision["raw_response"] = raw_text[:1000]
        return decision
