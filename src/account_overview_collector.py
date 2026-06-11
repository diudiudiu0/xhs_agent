from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.browser_session import CREATOR_HOME_URL
from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_DATA_CONFIG_PATH = PROJECT_ROOT / "cfg" / "account_data.yaml"


def load_account_overview_config() -> dict[str, Any]:
    data = _safe_load_yaml_file(ACCOUNT_DATA_CONFIG_PATH)
    config = data.get("account_overview")
    if not isinstance(config, dict):
        raise ValueError("cfg/account_data.yaml 中 account_overview 必须是字典。")
    return config


def _resolve_project_path(path_value: str | Path | None, default_value: str) -> Path:
    raw_path = Path(str(path_value or default_value))
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _parse_count(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([万wWkK]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "万":
        number *= 10000
    elif unit == "w":
        number *= 10000
    elif unit == "k":
        number *= 1000
    return int(number)


async def _wait_for_page_settle(page, settle_ms: int = 1000, timeout_ms: int = 10000):
    await page.wait_for_selector("body", timeout=timeout_ms)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
    except Exception:
        pass
    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)


async def _click_first_visible_text(page, texts: list[str], wait_ms: int) -> bool:
    for text in texts:
        if not text:
            continue
        try:
            await page.get_by_text(text, exact=True).first.click(timeout=3500)
            await page.wait_for_timeout(wait_ms)
            return True
        except Exception:
            pass
    return bool(
        await page.evaluate(
            """(labels) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],span,div'));
                for (const label of labels) {
                    const target = nodes.find(el => visible(el) && (el.innerText || el.textContent || '').trim() === label);
                    if (target) {
                        target.click();
                        return true;
                    }
                }
                return false;
            }""",
            texts,
        )
    )


def _classify_metric_card(text: str, aliases: dict[str, list[str]]) -> dict[str, Any] | None:
    text = _compact_spaces(text)
    if not text or len(text) > 160:
        return None
    parsed = _parse_count(text)
    if parsed is None:
        return None
    lowered = text.lower()
    for metric_name, words in aliases.items():
        if any(str(word).lower() in lowered for word in words):
            return {"metric": metric_name, "value": parsed, "raw": text}
    return {"metric": "unknown", "value": parsed, "raw": text}


def _merge_metrics(cards: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for card in cards:
        name = str(card.get("metric") or "")
        if not name or name == "unknown" or name in metrics:
            continue
        metrics[name] = card.get("value")
    return metrics


def _append_overview_history(overview: dict[str, Any], config: dict[str, Any]):
    path = _resolve_project_path(
        config.get("history_file"),
        "data/account_insights/account_overview_history.json",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"version": 1, "snapshots": []}
    else:
        data = {"version": 1, "snapshots": []}
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list):
        snapshots = []
        data["snapshots"] = snapshots
    snapshots.append(
        {
            "collected_at": overview.get("collected_at"),
            "url": overview.get("url", ""),
            "metrics": overview.get("metrics") or {},
            "metric_cards": overview.get("metric_cards") or [],
        }
    )
    data["updated_at"] = overview.get("collected_at") or _now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_account_overview(overview: dict[str, Any], output_file: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_account_overview_config()
    path = _resolve_project_path(output_file, str(config.get("output_file") or "data/account_insights/account_overview.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": overview.get("collected_at") or _now_iso(),
        "overview": overview,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_overview_history(overview, config)
    return {"output_file": str(path), "history_file": str(_resolve_project_path(config.get("history_file"), "data/account_insights/account_overview_history.json"))}


async def collect_account_overview(page, output_file: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_account_overview_config()
    navigation = config.get("navigation") or {}
    extraction = config.get("extraction") or {}
    wait_ms = int(navigation.get("wait_after_navigation_ms") or 2000)
    settle_ms = int(navigation.get("settle_after_ready_ms") or 1000)

    if "creator.xiaohongshu.com" not in page.url:
        await page.goto(CREATOR_HOME_URL, wait_until="domcontentloaded", timeout=30000)
    await _wait_for_page_settle(page, settle_ms=settle_ms)

    clicked_dashboard = await _click_first_visible_text(
        page,
        [str(item) for item in navigation.get("dashboard_entry_texts") or []],
        wait_ms,
    )
    await _wait_for_page_settle(page, settle_ms=settle_ms)
    clicked_note_overview = await _click_first_visible_text(
        page,
        [str(item) for item in navigation.get("note_overview_texts") or []],
        wait_ms,
    )
    await _wait_for_page_settle(page, settle_ms=settle_ms)

    extracted = await page.evaluate(
        """(limit) => {
            const visible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none'
                    && rect.width > 0 && rect.height > 0;
            };
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const nodes = Array.from(document.querySelectorAll('body *'));
            const texts = [];
            const seen = new Set();
            for (const el of nodes) {
                if (!visible(el)) continue;
                const text = textOf(el);
                if (!text || seen.has(text) || text.length > 180) continue;
                seen.add(text);
                if (/\\d|万|浏览|观看|阅读|播放|曝光|点赞|收藏|评论|分享|粉丝|互动|访问/.test(text)) {
                    texts.push(text);
                    if (texts.length >= limit) break;
                }
            }
            return {
                url: location.href,
                title: document.title || '',
                bodyText: document.body ? document.body.innerText : '',
                metricTextCandidates: texts,
            };
        }""",
        int(extraction.get("max_metric_cards") or 120),
    )

    aliases = extraction.get("metric_aliases") or {}
    cards = []
    seen = set()
    for text in extracted.get("metricTextCandidates") or []:
        card = _classify_metric_card(text, aliases)
        if not card:
            continue
        key = (card.get("metric"), card.get("value"), card.get("raw"))
        if key in seen:
            continue
        seen.add(key)
        cards.append(card)

    max_chars = int(extraction.get("max_visible_text_chars") or 12000)
    overview = {
        "collected_at": _now_iso(),
        "url": extracted.get("url") or page.url,
        "title": extracted.get("title") or "",
        "navigation": {
            "clicked_dashboard": clicked_dashboard,
            "clicked_note_overview": clicked_note_overview,
        },
        "metrics": _merge_metrics(cards),
        "metric_cards": cards,
        "visible_text_sample": _compact_spaces(extracted.get("bodyText") or "")[:max_chars],
    }
    storage = save_account_overview(overview, output_file=output_file, config=config)
    return {"success": True, "overview": overview, "storage": storage}
