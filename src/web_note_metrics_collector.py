from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.browser_session import XHS_HOME_URL
from src.task_config_loader import _safe_load_yaml_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_DATA_CONFIG_PATH = PROJECT_ROOT / "cfg" / "account_data.yaml"


def load_account_data_config() -> dict[str, Any]:
    data = _safe_load_yaml_file(ACCOUNT_DATA_CONFIG_PATH)
    config = data.get("published_note_metrics")
    if not isinstance(config, dict):
        raise ValueError("cfg/account_data.yaml 中 published_note_metrics 必须是字典。")
    return config


def _resolve_project_path(path_value: str | Path | None, default_value: str) -> Path:
    raw_path = Path(str(path_value or default_value))
    if raw_path.is_absolute():
        return raw_path
    return PROJECT_ROOT / raw_path


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_published_at_text(text: str, now: datetime | None = None) -> str:
    text = _compact_spaces(text)
    if not text or re.fullmatch(r"\d+\s*/\s*\d+", text):
        return ""

    now = now or datetime.now().astimezone()
    text = re.sub(r"^(?:发布于|编辑于)\s*", "", text)
    text = text.replace("年", "-").replace("月", "-").replace("日", " ").replace("号", " ")
    text = re.sub(r"\s+", " ", text).strip()

    relative_match = re.search(r"(今天|昨天|前天)(?:\s*\d{1,2}:\d{2})?", text)
    if relative_match:
        delta_days = {"今天": 0, "昨天": 1, "前天": 2}[relative_match.group(1)]
        date_value = (now - timedelta(days=delta_days)).date()
        return f"{date_value:%Y-%m-%d}"

    full_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
    if full_match:
        year, month, day = [int(value) for value in full_match.groups()]
        return f"{year:04d}-{month:02d}-{day:02d}"

    month_day_match = re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", text)
    if month_day_match:
        month, day = [int(value) for value in month_day_match.groups()]
        year = now.year
        try:
            candidate = now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return ""
        if candidate.date() > now.date():
            year -= 1
        return f"{year:04d}-{month:02d}-{day:02d}"

    return ""


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalize_key_text(text: str) -> str:
    return _compact_spaces(text).lower()


def _parse_collected_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _published_at_key(note: dict[str, Any]) -> str:
    collected_at = _parse_collected_at(note.get("collected_at"))
    return _normalize_key_text(
        _normalize_published_at_text(note.get("published_at") or "", now=collected_at)
        or note.get("published_at")
        or ""
    )


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
    elif unit in {"w", "k"}:
        number *= 1000 if unit == "k" else 10000
    return int(number)


def _first_regex(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _compact_spaces(match.group(1))
    return ""


def _is_valid_published_at_candidate(text: str) -> bool:
    text = _compact_spaces(text)
    if not text:
        return False
    if re.fullmatch(r"\d+\s*/\s*\d+", text):
        return False
    if len(text) > 80:
        return False
    return bool(
        re.search(r"发布|编辑|今天|昨天|前天", text)
        or re.search(r"\d{1,2}:\d{2}", text)
        or re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text)
        or re.search(r"(?<!\d)\d{1,2}[-/月]\d{1,2}(?:日|号)?(?!\d)", text)
    )


def _pick_published_at(extracted: dict[str, Any], patterns: list[str], now: datetime | None = None) -> str:
    for candidate in extracted.get("publishTimeCandidates") or []:
        text = _compact_spaces(candidate)
        if not _is_valid_published_at_candidate(text):
            continue
        matched = _first_regex(patterns, text)
        if matched and _is_valid_published_at_candidate(matched):
            return _normalize_published_at_text(matched, now=now)
        normalized = _normalize_published_at_text(text, now=now)
        if normalized:
            return normalized

    body_text = str(extracted.get("bodyText") or "")
    for pattern in patterns:
        for match in re.finditer(pattern, body_text, flags=re.IGNORECASE):
            value = _compact_spaces(match.group(1))
            if _is_valid_published_at_candidate(value):
                return _normalize_published_at_text(value, now=now)
    return ""


def _extract_metric_from_text(text: str, patterns: list[str]) -> int | None:
    value = _first_regex(patterns, text)
    return _parse_count(value)


def _extract_metric_from_candidates(candidates: list[dict[str, Any]], keywords: list[str]) -> int | None:
    keyword_values = [str(item).lower() for item in keywords]
    for item in candidates:
        text = _compact_spaces(
            " ".join(
                str(item.get(key) or "")
                for key in ("text", "aria", "title", "className")
            )
        )
        lowered = text.lower()
        if not text or not any(keyword in lowered for keyword in keyword_values):
            continue
        parsed = _parse_count(text)
        if parsed is not None:
            return parsed
    return None


def _append_note_metrics_history(notes: list[dict[str, Any]], config: dict[str, Any], collected_at: str | None = None):
    history_file = config.get("history_file")
    if not history_file:
        return
    path = _resolve_project_path(history_file, "data/account_insights/note_metrics_history.json")
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

    collected_at = collected_at or _now_iso()
    for note in notes:
        snapshots.append(
            {
                "collected_at": collected_at,
                "title": note.get("title", ""),
                "published_at": note.get("published_at", ""),
                "view_count": note.get("view_count"),
                "like_count": note.get("like_count"),
                "collect_count": note.get("collect_count"),
                "comment_count": note.get("comment_count"),
                "share_count": note.get("share_count"),
                "source_url": note.get("source_url", ""),
            }
        )
    data["updated_at"] = collected_at
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _should_append_metrics_history(output_file: str | Path | None, path: Path, config: dict[str, Any]) -> bool:
    default_path = _resolve_project_path(config.get("output_file"), "data/xhs_published_note_metrics.json")
    if output_file is not None and path.resolve() != default_path.resolve():
        return False
    return bool(config.get("history_file"))


def _parse_comment_item(item: Any) -> dict[str, str] | None:
    if isinstance(item, dict):
        raw_text = item.get("raw") or item.get("text") or ""
        is_reply = bool(item.get("isReply"))
    else:
        raw_text = str(item or "")
        is_reply = False

    text = _compact_spaces(raw_text)
    if is_reply or not text:
        return None

    text = re.sub(r"\s*赞(?:\s*\d+)?\s*$", "", text).strip()
    pattern = (
        r"^(?P<author>\S+)"
        r"(?:\s+作者)?\s+"
        r"(?P<content>.+?)"
        r"(?:\s+"
        r"(?P<time>(?:今天|昨天|前天)?\s*(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?\s*)?"
        r"(?:\d{1,2}[-/月]\d{1,2}[日号]?\s*)?\d{1,2}:\d{2})"
        r"(?P<location>[\u4e00-\u9fff]{0,6})?"
        r")?"
        r"$"
    )
    match = re.match(pattern, text)
    if not match:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return None
        return {
            "author": parts[0],
            "content": parts[1],
        }

    return {
        "author": _compact_spaces(match.group("author")),
        "content": _compact_spaces(match.group("content")),
    }


def _clean_comment_items(items: list[Any], config: dict[str, Any]) -> list[dict[str, str]]:
    extraction = config.get("extraction") or {}
    exclude_terms = [str(item) for item in extraction.get("comment_exclude_terms") or []]
    max_items = int(extraction.get("max_comment_items") or 80)
    cleaned = []
    seen = set()
    for item in items:
        raw_text = _compact_spaces(item.get("raw") if isinstance(item, dict) else item)
        if len(raw_text) < 2 or len(raw_text) > 500:
            continue
        if any(term and term in raw_text for term in exclude_terms):
            continue
        if re.search(r"共\s*\d+\s*条评论", raw_text) or "- THE END -" in raw_text:
            continue
        if "作者" in raw_text and re.search(r"\s回复\s*$", raw_text):
            continue
        parsed = _parse_comment_item(item)
        if not parsed or not parsed["author"] or not parsed["content"]:
            continue
        key = _normalize_key_text(parsed["author"] + "|" + parsed["content"])
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(parsed)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _pick_title(extracted: dict[str, Any]) -> str:
    for title in extracted.get("titleCandidates") or []:
        text = _compact_spaces(title)
        if text and text not in {"小红书", "笔记"}:
            return text[:180]
    meta_title = _compact_spaces(extracted.get("documentTitle") or "")
    meta_title = re.sub(r"\s*-\s*小红书.*$", "", meta_title)
    return meta_title[:180]


def _is_useful_content_candidate(text: str, title: str, comments: list[dict[str, str]]) -> bool:
    text = _compact_spaces(text)
    if len(text) < 6:
        return False
    if title and _normalize_key_text(text) == _normalize_key_text(title):
        return False
    if _is_valid_published_at_candidate(text) and len(text) <= 100:
        return False
    if re.search(r"共\s*\d+\s*条评论|THE END|说点什么|发送|取消", text):
        return False
    comment_hits = 0
    for comment in comments[:5]:
        content = _compact_spaces(comment.get("content") or "")
        if content and content in text:
            comment_hits += 1
    return comment_hits < 2


def _pick_content(extracted: dict[str, Any], title: str, comments: list[dict[str, str]]) -> str:
    candidates = []
    seen = set()
    for candidate in extracted.get("contentCandidates") or []:
        text = _compact_spaces(candidate)
        key = _normalize_key_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        if _is_useful_content_candidate(text, title, comments):
            candidates.append(text)
    if candidates:
        return max(candidates, key=len)[:5000]
    return ""


async def _click_visible_text(page, texts: list[str], wait_ms: int) -> bool:
    for text in texts:
        if not text:
            continue
        try:
            await page.get_by_text(text, exact=True).first.click(timeout=3500)
            await _wait_for_page_settle(page, settle_ms=wait_ms)
            return True
        except Exception:
            pass

    clicked = await page.evaluate(
        """(labels) => {
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none'
                    && rect.width > 0 && rect.height > 0;
            };
            const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],span,div'));
            for (const label of labels) {
                const target = nodes.find(el => isVisible(el) && (el.innerText || el.textContent || '').trim() === label);
                if (target) {
                    target.click();
                    return true;
                }
            }
            return false;
        }""",
        texts,
    )
    if clicked:
        await _wait_for_page_settle(page, settle_ms=wait_ms)
    return bool(clicked)


async def _wait_for_page_settle(page, settle_ms: int = 800, timeout_ms: int = 10000):
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


async def _wait_for_note_cards_ready(page, config: dict[str, Any]):
    navigation = config.get("navigation") or {}
    timeout_ms = int(navigation.get("note_cards_ready_timeout_ms") or 10000)
    settle_ms = int(navigation.get("settle_after_ready_ms") or 600)
    selectors = (config.get("selectors") or {}).get("note_card_selectors") or []
    await _wait_for_page_settle(page, settle_ms=settle_ms, timeout_ms=timeout_ms)
    try:
        await page.wait_for_function(
            """(selectors) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width >= 60 && rect.height >= 60;
                };
                return selectors.some(selector =>
                    Array.from(document.querySelectorAll(selector)).some(visible)
                );
            }""",
            selectors,
            timeout=timeout_ms,
        )
    except Exception:
        pass
    await page.wait_for_timeout(settle_ms)


async def _wait_for_note_detail_ready(page, config: dict[str, Any]):
    navigation = config.get("navigation") or {}
    selectors = config.get("selectors") or {}
    timeout_ms = int(navigation.get("detail_ready_timeout_ms") or 12000)
    settle_ms = int(navigation.get("settle_after_ready_ms") or 800)
    await _wait_for_page_settle(page, settle_ms=settle_ms, timeout_ms=timeout_ms)
    try:
        await page.wait_for_function(
            """(config) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                };
                const hasSelectorText = (selectors) => (selectors || []).some(selector =>
                    Array.from(document.querySelectorAll(selector)).some(el =>
                        visible(el) && (el.innerText || el.textContent || '').trim().length > 0
                    )
                );
                const bodyText = document.body ? document.body.innerText : '';
                const hasPublishTime = /发布|编辑|今天|昨天|前天|\\d{4}[-/年]\\d{1,2}[-/月]\\d{1,2}|(^|\\s)\\d{1,2}[-/月]\\d{1,2}(日|号)?(\\s|$)/.test(bodyText);
                const hasMetrics = /评论|收藏|点赞|分享|赞/.test(bodyText);
                return hasSelectorText(config.title_selectors) || (hasPublishTime && hasMetrics);
            }""",
            {
                "title_selectors": selectors.get("title_selectors") or [],
            },
            timeout=timeout_ms,
        )
    except Exception:
        pass
    await page.wait_for_timeout(settle_ms)


async def open_own_profile_notes(page, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_account_data_config()
    navigation = config.get("navigation") or {}
    wait_ms = int(navigation.get("wait_after_navigation_ms") or 1500)

    if "www.xiaohongshu.com" not in page.url:
        await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
    await _wait_for_page_settle(page, settle_ms=int(navigation.get("settle_after_ready_ms") or 800))

    clicked_profile = await _click_visible_text(
        page,
        [str(item) for item in navigation.get("profile_entry_texts") or []],
        wait_ms,
    )
    clicked_notes = await _click_visible_text(
        page,
        [str(item) for item in navigation.get("note_tab_texts") or []],
        wait_ms,
    )
    await _wait_for_note_cards_ready(page, config)
    return {
        "clicked_profile": clicked_profile,
        "clicked_notes": clicked_notes,
        "url": page.url,
    }


async def _find_note_card_candidates(page, config: dict[str, Any]) -> list[dict[str, Any]]:
    selectors = (config.get("selectors") or {}).get("note_card_selectors") or []
    return await page.evaluate(
        """(selectors) => {
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none'
                    && rect.width >= 60 && rect.height >= 60;
            };
            const seen = new Set();
            const candidates = [];
            for (const selector of selectors) {
                for (const el of Array.from(document.querySelectorAll(selector))) {
                    if (seen.has(el) || !isVisible(el)) continue;
                    seen.add(el);
                    const rect = el.getBoundingClientRect();
                    const href = el.href || el.getAttribute('href') || '';
                    const className = String(el.className || '');
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const hrefMatch = /\\/explore\\/|\\/discovery\\/item\\//.test(href);
                    const id = `xhs-note-card-${candidates.length}`;
                    el.setAttribute('data-xhs-note-card-id', id);
                    candidates.push({
                        id,
                        href,
                        text,
                        className,
                        priority: hrefMatch ? 0 : (/note/i.test(className) ? 1 : 2),
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        area: Math.round(rect.width * rect.height),
                    });
                }
            }
            return candidates
                .filter(item => item.area >= 6000 && item.y >= 80 && (item.priority < 2 || item.text.length >= 2))
                .sort((a, b) => (a.priority - b.priority) || (a.y - b.y) || (a.x - b.x))
                .slice(0, 30);
        }""",
        selectors,
    )


async def _load_note_card_candidates(page, config: dict[str, Any]) -> list[dict[str, Any]]:
    navigation = config.get("navigation") or {}
    max_notes = int(config.get("max_notes_per_refresh") or 30)
    max_scroll_rounds = int(navigation.get("max_scroll_rounds_per_refresh") or 8)
    scroll_wait_ms = int(navigation.get("scroll_after_load_ms") or 900)
    previous_count = -1
    stable_rounds = 0
    candidates = []
    for _ in range(max_scroll_rounds + 1):
        candidates = await _find_note_card_candidates(page, config)
        if len(candidates) >= max_notes:
            break
        if len(candidates) == previous_count:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        else:
            stable_rounds = 0
            previous_count = len(candidates)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(scroll_wait_ms)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(scroll_wait_ms)
    return await _find_note_card_candidates(page, config)


async def click_profile_note_by_index(page, note_index: int = 0, config: dict[str, Any] | None = None):
    config = config or load_account_data_config()
    note_index = max(0, int(note_index))
    await _wait_for_note_cards_ready(page, config)
    candidates = await (_find_note_card_candidates(page, config) if note_index == 0 else _load_note_card_candidates(page, config))
    if not candidates:
        raise RuntimeError("未找到个人主页笔记卡片，请确认已经进入“我 -> 笔记”页面。")
    if note_index >= len(candidates):
        raise IndexError(f"笔记索引超出范围：{note_index}，当前候选数量：{len(candidates)}")

    selected = candidates[note_index]
    pages_before = set(page.context.pages)
    await page.locator(f'[data-xhs-note-card-id="{selected["id"]}"]').first.click(timeout=8000)
    wait_ms = int((config.get("navigation") or {}).get("wait_after_note_open_ms") or 2500)
    await page.wait_for_timeout(wait_ms)
    pages_after = set(page.context.pages)
    new_pages = [item for item in page.context.pages if item not in pages_before and not item.is_closed()]
    detail_page = new_pages[-1] if new_pages else page
    await _wait_for_note_detail_ready(detail_page, config)
    return detail_page, selected


async def extract_note_metrics_from_detail_page(page, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_account_data_config()
    selectors = config.get("selectors") or {}
    extraction = config.get("extraction") or {}
    await _wait_for_note_detail_ready(page, config)
    extracted = await page.evaluate(
        """(config) => {
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style && style.visibility !== 'hidden' && style.display !== 'none'
                    && rect.width > 0 && rect.height > 0;
            };
            const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            const pickTexts = (selectors, limit) => {
                const out = [];
                const seen = new Set();
                for (const selector of selectors || []) {
                    for (const el of Array.from(document.querySelectorAll(selector))) {
                        if (!isVisible(el)) continue;
                        const text = textOf(el);
                        if (!text || seen.has(text)) continue;
                        seen.add(text);
                        out.push(text);
                        if (out.length >= limit) return out;
                    }
                }
                return out;
            };
            const pickInteractionCandidates = (selectors, limit) => {
                const out = [];
                const seen = new Set();
                for (const selector of selectors || []) {
                    for (const el of Array.from(document.querySelectorAll(selector))) {
                        if (!isVisible(el) || seen.has(el)) continue;
                        seen.add(el);
                        const rect = el.getBoundingClientRect();
                        out.push({
                            text: textOf(el),
                            aria: el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            className: String(el.className || ''),
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                        });
                        if (out.length >= limit) return out;
                    }
                }
                return out;
            };
            const classOf = (el) => String(el && el.className || '');
            const looksReplyNode = (el) => {
                let current = el;
                while (current && current !== document.body) {
                    if (/reply|sub[-_]?comment|children|child[-_]?comment/i.test(classOf(current))) {
                        return true;
                    }
                    current = current.parentElement;
                }
                return false;
            };
            const pickCommentCandidates = (selectors, limit) => {
                const out = [];
                const seen = new Set();
                const specificSelectors = (selectors || []).filter(selector => !selector.includes('[class*="comment"]'));
                const fallbackSelectors = selectors || [];
                const selectorGroups = [specificSelectors, fallbackSelectors];
                for (const group of selectorGroups) {
                    if (!group.length) continue;
                    for (const selector of group) {
                        for (const el of Array.from(document.querySelectorAll(selector))) {
                            if (!isVisible(el) || seen.has(el)) continue;
                            seen.add(el);
                            const raw = textOf(el);
                            if (!raw || raw.length > 500) continue;
                            if (/共\\s*\\d+\\s*条评论|THE END|说点什么/.test(raw)) continue;
                            out.push({
                                raw,
                                isReply: looksReplyNode(el),
                                className: classOf(el),
                            });
                            if (out.length >= limit) return out;
                        }
                    }
                    if (out.length) return out;
                }
                return out;
            };
            const pickPublishTimeCandidates = (limit) => {
                const out = [];
                const seen = new Set();
                const selectors = [
                    '[class*="date"]',
                    '[class*="time"]',
                    '[class*="publish"]',
                    '[class*="desc"]',
                    '[class*="content"]',
                    'span',
                    'div'
                ];
                for (const selector of selectors) {
                    for (const el of Array.from(document.querySelectorAll(selector))) {
                        if (!isVisible(el)) continue;
                        const text = textOf(el);
                        if (!text || text.length > 80 || seen.has(text)) continue;
                        if (/^\\d+\\s*\\/\\s*\\d+$/.test(text)) continue;
                        if (/发布|编辑|今天|昨天|前天|\\d{1,2}:\\d{2}|\\d{4}[-/年]\\d{1,2}[-/月]\\d{1,2}|(^|\\s)\\d{1,2}[-/月]\\d{1,2}(日|号)?(\\s|$)/.test(text)) {
                            seen.add(text);
                            out.push(text);
                            if (out.length >= limit) return out;
                        }
                    }
                }
                return out;
            };
            return {
                url: location.href,
                documentTitle: document.title || '',
                bodyText: document.body ? document.body.innerText : '',
                titleCandidates: pickTexts(config.title_selectors, 20),
                contentCandidates: pickTexts(config.content_selectors, 30),
                publishTimeCandidates: pickPublishTimeCandidates(30),
                commentCandidates: pickCommentCandidates(config.comment_selectors, config.max_comment_items || 80),
                interactionCandidates: pickInteractionCandidates(config.interaction_selectors, 240),
            };
        }""",
        {
            "title_selectors": selectors.get("title_selectors") or [],
            "content_selectors": selectors.get("content_selectors") or [],
            "comment_selectors": selectors.get("comment_selectors") or [],
            "interaction_selectors": selectors.get("interaction_selectors") or [],
            "max_comment_items": extraction.get("max_comment_items") or 80,
        },
    )

    body_text = str(extracted.get("bodyText") or "")
    metric_keywords = extraction.get("metric_keywords") or {}
    interaction_candidates = extracted.get("interactionCandidates") or []
    comments = _clean_comment_items(extracted.get("commentCandidates") or [], config)
    comment_count = _extract_metric_from_text(body_text, extraction.get("comment_count_patterns") or [])
    published_at = _pick_published_at(extracted, extraction.get("published_at_patterns") or [])
    title = _pick_title(extracted)

    return {
        "title": title,
        "content": _pick_content(extracted, title, comments),
        "published_at": published_at,
        "comment_count": comment_count if comment_count is not None else len(comments),
        "comments": comments,
        "view_count": _extract_metric_from_candidates(interaction_candidates, metric_keywords.get("view") or []),
        "collect_count": _extract_metric_from_candidates(interaction_candidates, metric_keywords.get("collect") or []),
        "like_count": _extract_metric_from_candidates(interaction_candidates, metric_keywords.get("like") or []),
        "share_count": _extract_metric_from_candidates(interaction_candidates, metric_keywords.get("share") or []),
        "source_url": extracted.get("url") or page.url,
        "collected_at": _now_iso(),
    }


def _load_note_metrics_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": "", "notes": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + ".broken")
        path.replace(backup)
        return {"version": 1, "updated_at": "", "notes": []}
    if isinstance(data, list):
        return {"version": 1, "updated_at": "", "notes": data}
    if not isinstance(data, dict):
        return {"version": 1, "updated_at": "", "notes": []}
    notes = data.get("notes")
    if not isinstance(notes, list):
        data["notes"] = []
    data.setdefault("version", 1)
    data.setdefault("updated_at", "")
    return data


def save_note_metrics_if_new(note: dict[str, Any], output_file: str | Path | None = None) -> dict[str, Any]:
    config = load_account_data_config()
    path = _resolve_project_path(output_file, str(config.get("output_file") or "data/xhs_published_note_metrics.json"))
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _load_note_metrics_file(path)
    title_key = _normalize_key_text(note.get("title") or "")
    time_key = _published_at_key(note)
    duplicate = False
    for item in data["notes"]:
        if (
            _normalize_key_text(item.get("title") or "") == title_key
            and _published_at_key(item) == time_key
        ):
            duplicate = True
            break

    added = False
    if not duplicate:
        data["notes"].append(note)
        data["updated_at"] = _now_iso()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if _should_append_metrics_history(output_file, path, config):
            _append_note_metrics_history([note], config, collected_at=data["updated_at"])
        added = True

    return {
        "output_file": str(path),
        "added": added,
        "duplicate": duplicate,
        "note_count": len(data["notes"]),
    }


def save_note_metrics_snapshot(notes: list[dict[str, Any]], output_file: str | Path | None = None) -> dict[str, Any]:
    config = load_account_data_config()
    path = _resolve_project_path(output_file, str(config.get("output_file") or "data/xhs_published_note_metrics.json"))
    path.parent.mkdir(parents=True, exist_ok=True)

    deduped_notes = []
    seen = set()
    duplicate_count = 0
    for note in notes:
        title_key = _normalize_key_text(note.get("title") or "")
        time_key = _published_at_key(note)
        key = (title_key, time_key)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        deduped_notes.append(note)

    data = {
        "version": 2,
        "updated_at": _now_iso(),
        "refresh_mode": "full_published_notes_snapshot",
        "dedupe_key": ["title", "published_at"],
        "notes": deduped_notes,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if _should_append_metrics_history(output_file, path, config):
        _append_note_metrics_history(deduped_notes, config, collected_at=data["updated_at"])
    return {
        "output_file": str(path),
        "added": True,
        "duplicate": False,
        "overwritten": True,
        "duplicate_count": duplicate_count,
        "note_count": len(deduped_notes),
    }


async def _return_to_note_list(page, config: dict[str, Any]):
    navigation = config.get("navigation") or {}
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=10000)
    except Exception:
        pass
    await _wait_for_page_settle(
        page,
        settle_ms=int(navigation.get("settle_after_ready_ms") or 800),
        timeout_ms=int(navigation.get("note_cards_ready_timeout_ms") or 12000),
    )


async def collect_all_published_note_metrics(
    page,
    output_file: str | Path | None = None,
    max_notes: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or load_account_data_config()
    navigation_result = await open_own_profile_notes(page, config)
    initial_candidates = await _load_note_card_candidates(page, config)
    limit = int(max_notes if max_notes is not None else config.get("max_notes_per_refresh", 30))
    total = min(max(0, limit), len(initial_candidates))
    notes = []
    cards = []
    errors = []

    for note_index in range(total):
        try:
            await open_own_profile_notes(page, config)
            detail_page, selected_card = await click_profile_note_by_index(page, note_index=note_index, config=config)
            note = await extract_note_metrics_from_detail_page(detail_page, config)
            notes.append(note)
            cards.append(selected_card)
            if detail_page is page:
                await _return_to_note_list(page, config)
            elif not detail_page.is_closed():
                await detail_page.close()
        except Exception as exc:
            errors.append({"note_index": note_index, "error": str(exc)})
            if page.is_closed():
                break
            try:
                await open_own_profile_notes(page, config)
            except Exception:
                pass

    save_result = save_note_metrics_snapshot(notes, output_file=output_file)
    return {
        "success": not errors,
        "navigation": navigation_result,
        "notes": notes,
        "cards": cards,
        "errors": errors,
        "storage": save_result,
    }


async def collect_latest_published_note_metrics(
    page,
    output_file: str | Path | None = None,
    note_index: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or load_account_data_config()
    note_index = int(note_index if note_index is not None else config.get("default_note_index", 0))
    navigation_result = await open_own_profile_notes(page, config)
    detail_page, selected_card = await click_profile_note_by_index(page, note_index=note_index, config=config)
    note = await extract_note_metrics_from_detail_page(detail_page, config)
    save_result = save_note_metrics_if_new(note, output_file=output_file)
    return {
        "success": True,
        "navigation": navigation_result,
        "selected_card": selected_card,
        "note": note,
        "storage": save_result,
    }
