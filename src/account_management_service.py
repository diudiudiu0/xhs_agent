from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openai import OpenAI

from cfg.model_config import MODEL_CONFIG
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


def _note_key(note: dict[str, Any]) -> str:
    return f"{_compact(note.get('published_at'))}|{_compact(note.get('title'))}"


def get_metrics_file(path_value: str | Path | None = None) -> Path:
    config = load_account_manager_config()
    default_file = str((config.get("metrics") or {}).get("default_metrics_file") or "data/xhs_published_note_metrics.json")
    return _resolve_project_path(path_value, default_file)


def get_overview_file(path_value: str | Path | None = None) -> Path:
    config = load_account_manager_config()
    default_file = str((config.get("metrics") or {}).get("default_overview_file") or "data/account_insights/account_overview.json")
    return _resolve_project_path(path_value, default_file)


def load_note_metrics(metrics_file: str | Path | None = None) -> dict[str, Any]:
    path = get_metrics_file(metrics_file)
    data = _load_json(path, {"version": 1, "updated_at": "", "notes": []})
    notes = data.get("notes")
    if not isinstance(notes, list):
        data["notes"] = []
    data["source_file"] = str(path)
    return data


def load_account_overview(overview_file: str | Path | None = None) -> dict[str, Any]:
    path = get_overview_file(overview_file)
    data = _load_json(path, {"version": 1, "updated_at": "", "overview": {}})
    if not isinstance(data.get("overview"), dict):
        data["overview"] = {}
    data["source_file"] = str(path)
    return data


def _engagement_score(note: dict[str, Any], weights: dict[str, Any]) -> int:
    return (
        _count(note.get("view_count")) * _count(weights.get("view", 0))
        + _count(note.get("like_count")) * _count(weights.get("like", 1))
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
    totals = {"view_count": 0, "like_count": 0, "collect_count": 0, "comment_count": 0, "share_count": 0}
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
    rates = {
        "like_rate": round(totals["like_count"] / totals["view_count"], 4) if totals["view_count"] else None,
        "collect_rate": round(totals["collect_count"] / totals["view_count"], 4) if totals["view_count"] else None,
        "comment_rate": round(totals["comment_count"] / totals["view_count"], 4) if totals["view_count"] else None,
        "engagement_rate": round(
            (totals["like_count"] + totals["collect_count"] + totals["comment_count"] + totals["share_count"])
            / totals["view_count"],
            4,
        )
        if totals["view_count"]
        else None,
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
        if rates["engagement_rate"] is None:
            insights.append("当前笔记详情数据中未稳定采集到浏览量，建议结合创作中心数据看板补充阅读/浏览指标。")
        if comments:
            insights.append("评论区已有用户反馈，可用于下一轮选题和回复策略。")

    return {
        "source_file": data.get("source_file", ""),
        "note_count": note_count,
        "totals": totals,
        "averages": averages,
        "rates": rates,
        "top_notes": [
            {
                "title": item.get("title", ""),
                "published_at": item.get("published_at", ""),
                "engagement_score": item.get("engagement_score", 0),
                "view_count": item.get("view_count"),
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


def _default_creative_strategy(
    analysis: dict[str, Any],
    overview_data: dict[str, Any],
    user_goal: str,
    topic_count: int,
) -> dict[str, Any]:
    config = load_account_manager_config()
    topic_config = config.get("topic_planning") or {}
    focus = _compact(topic_config.get("default_focus") or "小红书商品推荐")
    keyword = _topic_keyword(analysis, fallback=focus)
    overview = overview_data.get("overview") or {}
    overview_metrics = overview.get("metrics") or {}

    data_gaps = []
    if not overview_metrics:
        data_gaps.append("缺少创作中心数据总览，需要先采集账号 overview。")
    if (analysis.get("rates") or {}).get("engagement_rate") is None:
        data_gaps.append("缺少稳定浏览量，互动率需要结合创作中心浏览/阅读数据计算。")
    if not analysis.get("comment_samples"):
        data_gaps.append("评论样本较少，用户需求判断需要继续积累。")

    next_topics = []
    templates = topic_config.get("topic_templates") or []
    for index in range(topic_count):
        template = templates[index % len(templates)] if templates else {}
        title_template = str(template.get("title") or "{keyword}内容选题")
        angle_template = str(template.get("angle") or "围绕用户反馈和账号数据继续深化。")
        item = {
            "title": title_template.format(keyword=keyword, focus=focus),
            "angle": angle_template.format(keyword=keyword, focus=focus),
            "why": "基于已采集笔记的互动数据、评论样本和关键词表现生成。",
            "suggested_assets": ["产品主图", "参数对比图", "使用场景图"],
        }
        next_topics.append(
            {
                "title": item.get("title", ""),
                "reason": item.get("why", ""),
                "angle": item.get("angle", ""),
                "target_audience": "需要解决推车、货架、家具移动和静音承重问题的用户",
                "image_plan": item.get("suggested_assets") or ["封面图", "参数图", "场景图"],
                "copywriting_brief": f"围绕 {keyword} 做实用推荐，减少疑问句，突出参数、场景和避坑。",
                "success_metric": "重点观察收藏数、评论咨询量和浏览后的互动率。",
            }
        )

    return {
        "summary": "当前数据适合继续走实用参数和场景推荐方向。",
        "data_gaps": data_gaps,
        "audience_hypothesis": "目标用户更关注承重、静音、刹车、安装方式和使用场景。",
        "content_opportunities": analysis.get("insights") or ["继续采集数据后再细化机会。"],
        "avoid_patterns": ["避免空泛种草，避免只展示产品不解释参数和适配场景。"],
        "next_topics": next_topics,
        "recommended_next_action": "选择一个 next_topics，调用 generate_image_prompts 和 create_generated_note_draft 生成草稿。",
        "user_goal": user_goal,
    }


def generate_creative_strategy(
    metrics_file: str | Path | None = None,
    overview_file: str | Path | None = None,
    user_goal: str | None = None,
    topic_count: int | None = None,
    output_file: str | Path | None = None,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    config = load_account_manager_config()
    strategy_config = config.get("creative_strategy") or {}
    topic_config = config.get("topic_planning") or {}
    topic_count = int(topic_count or strategy_config.get("default_topic_count") or topic_config.get("default_topic_count") or 5)
    user_goal = _compact(user_goal or "")
    analysis = analyze_account_performance(metrics_file=metrics_file)
    overview_data = load_account_overview(overview_file)
    overview = overview_data.get("overview") or {}
    should_use_llm = bool(strategy_config.get("use_llm", True) if use_llm is None else use_llm)

    strategy = None
    if should_use_llm and MODEL_CONFIG.get("api_key"):
        prompt_template = str(strategy_config.get("prompt_template") or "").strip()
        if prompt_template:
            prompt = prompt_template.format(
                account_focus=_compact(topic_config.get("default_focus") or "小红书商品推荐"),
                overview_json=json.dumps(overview, ensure_ascii=False, indent=2)[:5000],
                analysis_json=json.dumps(analysis, ensure_ascii=False, indent=2)[:7000],
                user_goal=user_goal or "根据账号数据规划下一轮创作。",
            )
            try:
                client = OpenAI(
                    api_key=MODEL_CONFIG["api_key"],
                    base_url=MODEL_CONFIG["base_url"],
                    timeout=MODEL_CONFIG.get("timeout", 30),
                )
                response = client.chat.completions.create(
                    model=MODEL_CONFIG.get("content_model", MODEL_CONFIG.get("planner_model")),
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=MODEL_CONFIG.get("content_max_tokens", 1800),
                    temperature=MODEL_CONFIG.get("content_temperature", 0.7),
                )
                strategy = _extract_json(response.choices[0].message.content or "")
            except Exception:
                strategy = None

    if not isinstance(strategy, dict):
        strategy = _default_creative_strategy(analysis, overview_data, user_goal, topic_count)

    strategy.setdefault("generated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    strategy.setdefault("source_metrics_file", analysis.get("source_file", ""))
    strategy.setdefault("source_overview_file", overview_data.get("source_file", ""))
    strategy.setdefault("analysis", analysis)
    strategy.setdefault("overview_metrics", overview.get("metrics") or {})

    default_output = str(strategy_config.get("output_file") or "data/account_insights/creative_strategy.json")
    output_path = _resolve_project_path(output_file, default_output)
    _write_json(output_path, strategy)
    strategy["output_file"] = str(output_path)
    return strategy


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
