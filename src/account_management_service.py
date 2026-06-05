from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_MANAGER_CONFIG_PATH = PROJECT_ROOT / "cfg" / "account_manager.yaml"


def load_account_manager_config() -> dict[str, Any]:
    data = _safe_load_yaml_file(ACCOUNT_MANAGER_CONFIG_PATH)
    config = data.get("account_manager")
    if not isinstance(config, dict):
        raise ValueError("cfg/account_manager.yaml 中 account_manager 必须是字典。")
    return config


def _resolve_project_path(path_value: str | Path | None, default_value: str) -> Path:
    raw_path = Path(str(path_value or default_value))
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback
    return data if isinstance(data, dict) else fallback


def _write_json(path: Path, data: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _count(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _note_key(note: dict[str, Any]) -> str:
    return f"{_compact(note.get('published_at'))}|{_compact(note.get('title'))}"


def get_metrics_file(path_value: str | Path | None = None) -> Path:
    config = load_account_manager_config()
    default_file = str((config.get("metrics") or {}).get("default_metrics_file") or "data/xhs_published_note_metrics.json")
    return _resolve_project_path(path_value, default_file)


def load_note_metrics(metrics_file: str | Path | None = None) -> dict[str, Any]:
    path = get_metrics_file(metrics_file)
    data = _load_json(path, {"version": 1, "updated_at": "", "notes": []})
    notes = data.get("notes")
    if not isinstance(notes, list):
        data["notes"] = []
    data["source_file"] = str(path)
    return data


def _engagement_score(note: dict[str, Any], weights: dict[str, Any]) -> int:
    return (
        _count(note.get("like_count")) * _count(weights.get("like", 1))
        + _count(note.get("collect_count")) * _count(weights.get("collect", 2))
        + _count(note.get("comment_count")) * _count(weights.get("comment", 3))
        + _count(note.get("share_count")) * _count(weights.get("share", 2))
    )


def _tokenize_cn(text: str) -> list[str]:
    terms = []
    text = _compact(text)
    for token in re.findall(r"[A-Za-z0-9]+", text):
        if len(token) >= 2:
            terms.append(token.lower())
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) <= 2:
            terms.append(chunk)
        else:
            for index in range(len(chunk) - 1):
                terms.append(chunk[index : index + 2])
    return terms


def analyze_account_performance(metrics_file: str | Path | None = None, top_n: int | None = None) -> dict[str, Any]:
    config = load_account_manager_config()
    metric_config = config.get("metrics") or {}
    weights = metric_config.get("weights") or {}
    top_n = int(top_n or metric_config.get("top_note_count") or 5)
    data = load_note_metrics(metrics_file)
    notes = [note for note in data.get("notes", []) if isinstance(note, dict)]

    scored_notes = []
    totals = {"like_count": 0, "collect_count": 0, "comment_count": 0, "share_count": 0}
    comments = []
    keyword_counter = Counter()

    for note in notes:
        score = _engagement_score(note, weights)
        enriched = dict(note)
        enriched["engagement_score"] = score
        scored_notes.append(enriched)
        for key in totals:
            totals[key] += _count(note.get(key))
        for comment in note.get("comments") or []:
            if isinstance(comment, dict):
                content = _compact(comment.get("content"))
                if content:
                    comments.append({"note_title": note.get("title", ""), "author": comment.get("author", ""), "content": content})
                    keyword_counter.update(_tokenize_cn(content))
        keyword_counter.update(_tokenize_cn(str(note.get("title") or "")))

    scored_notes.sort(key=lambda item: item.get("engagement_score", 0), reverse=True)
    note_count = len(notes)
    averages = {
        key: round(value / note_count, 2) if note_count else 0
        for key, value in totals.items()
    }
    top_keywords = [
        {"keyword": keyword, "count": count}
        for keyword, count in keyword_counter.most_common(20)
        if keyword not in {"小红", "红书", "笔记", "推荐", "内容"}
    ][:10]
    insights = []
    if note_count == 0:
        insights.append("还没有可分析的已发布笔记数据，需要先采集主页笔记指标。")
    else:
        best = scored_notes[0]
        insights.append(f"当前最高互动笔记是《{best.get('title', '')}》，综合分 {best.get('engagement_score', 0)}。")
        if totals["collect_count"] >= totals["like_count"]:
            insights.append("收藏数相对突出，说明内容更偏实用型，适合继续做参数、清单和避坑类选题。")
        if comments:
            insights.append("评论区已有用户反馈，可用于下一轮选题和回复策略。")

    return {
        "source_file": data.get("source_file", ""),
        "note_count": note_count,
        "totals": totals,
        "averages": averages,
        "top_notes": [
            {
                "title": item.get("title", ""),
                "published_at": item.get("published_at", ""),
                "engagement_score": item.get("engagement_score", 0),
                "like_count": item.get("like_count"),
                "collect_count": item.get("collect_count"),
                "comment_count": item.get("comment_count"),
                "share_count": item.get("share_count"),
            }
            for item in scored_notes[:top_n]
        ],
        "top_keywords": top_keywords,
        "comment_samples": comments[:10],
        "insights": insights,
    }


def _topic_keyword(analysis: dict[str, Any], fallback: str) -> str:
    for item in analysis.get("top_keywords") or []:
        keyword = _compact(item.get("keyword"))
        if keyword and len(keyword) >= 2:
            return keyword
    for item in analysis.get("top_notes") or []:
        title = _compact(item.get("title"))
        if title:
            return title[:8]
    return fallback


def plan_content_topics(
    analysis: dict[str, Any] | None = None,
    topic_count: int | None = None,
    focus: str | None = None,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    config = load_account_manager_config()
    topic_config = config.get("topic_planning") or {}
    analysis = analysis or analyze_account_performance()
    topic_count = int(topic_count or topic_config.get("default_topic_count") or 5)
    focus = _compact(focus or topic_config.get("default_focus") or "小红书商品推荐")
    templates = topic_config.get("topic_templates") or []
    keyword = _topic_keyword(analysis, fallback=focus)

    topics = []
    for index in range(topic_count):
        template = templates[index % len(templates)] if templates else {}
        title_template = str(template.get("title") or "{keyword}内容选题")
        angle_template = str(template.get("angle") or "围绕用户反馈和账号数据继续深化。")
        title = title_template.format(keyword=keyword, focus=focus)
        topics.append(
            {
                "id": index + 1,
                "title": title,
                "focus": focus,
                "angle": angle_template.format(keyword=keyword, focus=focus),
                "why": "基于已采集笔记的互动数据、评论样本和关键词表现生成。",
                "suggested_assets": ["产品主图", "参数对比图", "使用场景图"],
                "status": "candidate",
            }
        )

    default_output = str(topic_config.get("output_file") or "data/content_topics.json")
    output_path = _resolve_project_path(output_file, default_output)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "focus": focus,
        "source_metrics_file": analysis.get("source_file", ""),
        "topics": topics,
    }
    _write_json(output_path, payload)
    payload["output_file"] = str(output_path)
    return payload


def schedule_content_calendar(
    topics: list[dict[str, Any]] | None = None,
    start_date: str | None = None,
    days_between_posts: int | None = None,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    config = load_account_manager_config()
    calendar_config = config.get("calendar") or {}
    if topics is None:
        topic_plan = plan_content_topics()
        topics = topic_plan.get("topics") or []

    start = date.fromisoformat(start_date) if start_date else date.today()
    days_between = int(days_between_posts or calendar_config.get("default_days_between_posts") or 2)
    default_status = str(calendar_config.get("default_status") or "planned")
    items = []
    for index, topic in enumerate(topics):
        publish_date = start + timedelta(days=index * days_between)
        items.append(
            {
                "date": publish_date.isoformat(),
                "title": topic.get("title", ""),
                "focus": topic.get("focus", ""),
                "angle": topic.get("angle", ""),
                "status": default_status,
                "source_topic_id": topic.get("id", index + 1),
            }
        )

    default_output = str(calendar_config.get("output_file") or "data/content_calendar.json")
    output_path = _resolve_project_path(output_file, default_output)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "days_between_posts": days_between,
        "items": items,
    }
    _write_json(output_path, payload)
    payload["output_file"] = str(output_path)
    return payload


def review_risky_action(action_description: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    config = load_account_manager_config()
    risk_config = config.get("risk_review") or {}
    text = _compact(action_description)
    high_terms = [str(item) for item in risk_config.get("high_risk_terms") or []]
    medium_terms = [str(item) for item in risk_config.get("medium_risk_terms") or []]
    matched_high = [term for term in high_terms if term and term in text]
    matched_medium = [term for term in medium_terms if term and term in text]

    if matched_high:
        risk_level = "high"
        requires_confirmation = True
    elif matched_medium:
        risk_level = "medium"
        requires_confirmation = True
    else:
        risk_level = "low"
        requires_confirmation = False

    return {
        "risk_level": risk_level,
        "requires_confirmation": requires_confirmation,
        "matched_terms": matched_high or matched_medium,
        "action_description": text,
        "context": context or {},
        "recommendation": "需要用户确认后再执行。" if requires_confirmation else "可以直接执行或继续规划。",
    }
