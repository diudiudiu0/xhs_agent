import asyncio
import hashlib
from typing import Any


async def _safe_evaluate(page, script: str, fallback: Any = None, timeout: float = 2.0):
    try:
        return await asyncio.wait_for(page.evaluate(script), timeout=timeout)
    except Exception:
        return fallback


async def _quick_load_state(page) -> dict:
    result = {
        "domcontentloaded": False,
        "load": False,
        "networkidle": False,
    }
    for state_name, timeout in (("domcontentloaded", 300), ("load", 300), ("networkidle", 500)):
        try:
            await page.wait_for_load_state(state_name, timeout=timeout)
            result[state_name] = True
        except Exception:
            result[state_name] = False
    return result


def _short_hash(value: str) -> str:
    return hashlib.md5((value or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _loading_phase(ready_state: str, load_state: dict) -> str:
    if ready_state in {"loading", "interactive"}:
        return "dom_loading"
    if not load_state.get("load"):
        return "page_loading"
    if not load_state.get("networkidle"):
        return "network_busy"
    return "stable"


async def observe_browser_state(page) -> dict:
    """
    采集当前浏览器/页面状态，供 Agent 判断是否应该等待、重试或继续操作。

    说明：
    - 网页弹窗可以通过 DOM 检测。
    - 操作系统原生文件选择器不能被 Playwright 被动读取；本项目通过 set_input_files
      绕开原生弹窗，因此这里只提供状态说明和上传 input 线索。
    """
    if page.is_closed():
        return {
            "page_closed": True,
            "page_responsive": False,
            "loading_phase": "closed",
            "url": "",
            "title": "",
            "ready_state": "closed",
            "load_state": {},
            "dom": {},
            "dialogs": {},
            "file_inputs": [],
            "system_dialog": {
                "native_file_chooser_observable": False,
                "hint": "页面已关闭，无法判断系统弹窗。",
            },
        }

    ready_state = await _safe_evaluate(page, "() => document.readyState", "unknown", timeout=1.5)
    load_state = await _quick_load_state(page)
    title = ""
    try:
        title = await asyncio.wait_for(page.title(), timeout=1.5)
    except Exception:
        title = ""

    dom_state = await _safe_evaluate(
        page,
        """() => {
            const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
            const visibleText = bodyText.slice(0, 3000);
            const buttons = Array.from(document.querySelectorAll('button, [role="button"], a, [tabindex]'))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden';
                })
                .map(el => (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '')
                    .replace(/\\s+/g, ' ')
                    .trim())
                .filter(Boolean)
                .slice(0, 30);
            const spinners = Array.from(document.querySelectorAll(
                '[class*="loading"], [class*="Loading"], [class*="spinner"], [class*="Spinner"], [aria-busy="true"]'
            )).filter(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
            }).length;
            return {
                text_length: bodyText.length,
                visible_text_sample: visibleText,
                button_texts: buttons,
                spinner_count: spinners,
                has_body: !!document.body
            };
        }""",
        {
            "text_length": 0,
            "visible_text_sample": "",
            "button_texts": [],
            "spinner_count": 0,
            "has_body": False,
        },
    )

    dialogs = await _safe_evaluate(
        page,
        """() => {
            const selectors = [
                '[role="dialog"]',
                '[aria-modal="true"]',
                '[class*="modal"]',
                '[class*="Modal"]',
                '[class*="dialog"]',
                '[class*="Dialog"]',
                '[class*="popover"]',
                '[class*="Popover"]'
            ];
            const items = [];
            for (const selector of selectors) {
                for (const el of Array.from(document.querySelectorAll(selector))) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden') {
                        items.push({
                            selector,
                            text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
                            rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                        });
                    }
                }
            }
            return {
                visible: items.length > 0,
                count: items.length,
                items: items.slice(0, 5)
            };
        }""",
        {"visible": False, "count": 0, "items": []},
    )

    file_inputs = await _safe_evaluate(
        page,
        """() => Array.from(document.querySelectorAll('input[type="file"]')).map((el, index) => {
            const rect = el.getBoundingClientRect();
            const accept = el.getAttribute('accept') || '';
            const nearText = (el.closest('div,section,main,form')?.innerText || '')
                .replace(/\\s+/g, ' ')
                .slice(0, 300);
            return {
                index,
                accept,
                multiple: !!el.multiple,
                visible: rect.width > 0 && rect.height > 0,
                nearText
            };
        })""",
        [],
    )

    visible_text = dom_state.get("visible_text_sample", "")
    button_texts = "|".join(dom_state.get("button_texts", []))
    signature = _short_hash("|".join([page.url, str(title), visible_text[:1000], button_texts]))
    loading_phase = _loading_phase(str(ready_state), load_state)
    page_responsive = bool(dom_state.get("has_body")) and ready_state != "unknown"

    return {
        "page_closed": False,
        "page_responsive": page_responsive,
        "loading_phase": loading_phase,
        "url": page.url,
        "title": title,
        "ready_state": ready_state,
        "load_state": load_state,
        "dom": {
            "text_length": dom_state.get("text_length", 0),
            "text_signature": _short_hash(visible_text),
            "page_signature": signature,
            "spinner_count": dom_state.get("spinner_count", 0),
            "button_texts": dom_state.get("button_texts", []),
        },
        "dialogs": dialogs,
        "file_inputs": file_inputs,
        "system_dialog": {
            "native_file_chooser_observable": False,
            "hint": "Playwright 不能被动读取操作系统文件选择器；本项目用 input[type=file].set_input_files 绕开系统弹窗。",
        },
    }


def compare_browser_states(before: dict | None, after: dict | None) -> dict:
    before = before or {}
    after = after or {}
    if after.get("page_closed"):
        status = "page_closed"
    elif after.get("loading_phase") in {"dom_loading", "page_loading", "network_busy"}:
        status = "loading_or_network_busy"
    elif before.get("url") != after.get("url"):
        status = "responded_url_changed"
    elif before.get("dom", {}).get("page_signature") != after.get("dom", {}).get("page_signature"):
        status = "responded_dom_changed"
    elif before.get("dialogs", {}).get("visible") != after.get("dialogs", {}).get("visible"):
        status = "responded_dialog_changed"
    else:
        status = "no_visible_response"

    return {
        "status": status,
        "url_changed": before.get("url") != after.get("url"),
        "ready_state_changed": before.get("ready_state") != after.get("ready_state"),
        "loading_phase": after.get("loading_phase"),
        "dom_changed": before.get("dom", {}).get("page_signature") != after.get("dom", {}).get("page_signature"),
        "dialog_visible": after.get("dialogs", {}).get("visible", False),
        "file_input_count": len(after.get("file_inputs") or []),
    }


def summarize_browser_state(state: dict | None) -> str:
    state = state or {}
    dialogs = state.get("dialogs") or {}
    dom = state.get("dom") or {}
    return (
        f"phase={state.get('loading_phase')} "
        f"ready={state.get('ready_state')} "
        f"responsive={state.get('page_responsive')} "
        f"dialog={dialogs.get('visible', False)} "
        f"file_inputs={len(state.get('file_inputs') or [])} "
        f"spinners={dom.get('spinner_count', 0)} "
        f"url={state.get('url', '')}"
    )


async def wait_for_browser_feedback(
    page,
    before_state: dict | None,
    timeout: float = 3.0,
    interval: float = 0.5,
) -> tuple[dict, dict]:
    """
    点击/填写后短时间轮询页面状态，直到看到可见反馈或超时。
    返回：(after_state, comparison)
    """
    deadline = asyncio.get_event_loop().time() + timeout
    latest_state = await observe_browser_state(page)
    latest_comparison = compare_browser_states(before_state, latest_state)

    while (
        latest_comparison.get("status") == "no_visible_response"
        and asyncio.get_event_loop().time() < deadline
    ):
        await asyncio.sleep(interval)
        latest_state = await observe_browser_state(page)
        latest_comparison = compare_browser_states(before_state, latest_state)

    return latest_state, latest_comparison
