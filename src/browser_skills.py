# src/browser_skills.py
import asyncio
import random
import sys
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.prompt_config import get_prompt_config, get_prompt_list


def _browser_words(*keys: str) -> list[str]:
    return [str(value) for value in get_prompt_list("browser_skills", *keys)]


def _browser_text(*keys: str, default: str = "") -> str:
    return str(get_prompt_config("browser_skills", *keys, default=default))


def _text_from_desc(desc: str) -> str:
    return desc.split('] ', 1)[1] if '] ' in desc else desc


def _locator_for_element(page, element):
    selector = element.get("selector")
    if selector:
        return page.locator(selector)
    return page.get_by_text(_text_from_desc(element.get("desc", "")), exact=True)


async def _click_actionable_ancestor(locator):
    return await locator.evaluate(
        """el => {
            const clickable = el.closest(
                'button,a,[role="button"],[role="link"],[onclick],[tabindex],'
                + '[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"],'
                + '[class*="publish"],[class*="Publish"],[class*="upload"],[class*="Upload"],'
                + '[class*="menu"],[class*="Menu"],[class*="item"],[class*="Item"]'
            ) || el;
            clickable.scrollIntoView({block: 'center', inline: 'center'});
            clickable.click();
            return true;
        }"""
    )


async def click_semantic_target(page, target_text, intent="", avoid_texts=None, event_names=None):
    """
    按语义点击一个目标，而不要求它一定出现在 extract_interactive_elements 的列表里。

    适用于小红书这类前端组件场景：按钮文字可能藏在自定义组件属性、Shadow DOM、
    aria/title/data 属性或固定底部工具条中。这个函数只在 Agent 明确返回
    click_semantic_target 动作时执行，不作为页面探索的自动兜底。
    """
    target_text = str(target_text or "").strip()
    if not target_text:
        raise Exception("语义点击目标文本不能为空")

    avoid_texts = [str(item).strip() for item in (avoid_texts or []) if str(item).strip()]
    event_names = [str(item).strip() for item in (event_names or []) if str(item).strip()]
    payload = {
        "targetText": target_text,
        "intent": str(intent or ""),
        "avoidTexts": avoid_texts,
        "eventNames": event_names,
    }
    script = """payload => {
        const targetText = payload.targetText || '';
        const intent = payload.intent || '';
        const avoidTexts = payload.avoidTexts || [];
        const explicitEventNames = payload.eventNames || [];
        const clickableSelector = [
            'button',
            'a',
            '[role="button"]',
            '[role="link"]',
            '[onclick]',
            '[tabindex]',
            '[class*="ce-btn"]',
            '[class*="btn"]',
            '[class*="Btn"]',
            '[class*="button"]',
            '[class*="Button"]',
            'xhs-publish-btn',
            '[save-text]',
            '[submit-text]',
            '[cancel-text]',
            '[confirm-text]',
            '[delete-text]',
            '[data-text]',
            '[data-title]',
            '[aria-label]',
            '[title]',
            'span',
            'div'
        ].join(',');
        const attrNames = [
            'aria-label',
            'title',
            'alt',
            'placeholder',
            'value',
            'data-text',
            'data-title',
            'data-name',
            'data-label',
            'save-text',
            'submit-text',
            'cancel-text',
            'confirm-text',
            'delete-text'
        ];

        function norm(text) {
            return String(text || '').replace(/\\s+/g, '').trim();
        }

        const target = norm(targetText);
        const avoid = avoidTexts.map(norm).filter(Boolean);

        function textOf(el) {
            if (!el) return '';
            const parts = [];
            for (const name of attrNames) {
                const value = el.getAttribute && el.getAttribute(name);
                if (value) parts.push(value);
            }
            parts.push(el.innerText || '');
            parts.push(el.textContent || '');
            return norm(parts.filter(Boolean).join(' '));
        }

        function attrTextOf(el) {
            if (!el || !el.getAttribute) return '';
            return norm(attrNames.map(name => el.getAttribute(name) || '').filter(Boolean).join(' '));
        }

        function visible(el, allowComponent = false) {
            if (!el || !el.getBoundingClientRect) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const rendered = style.display !== 'none'
                && style.visibility !== 'hidden'
                && style.opacity !== '0'
                && style.pointerEvents !== 'none';
            if (!rendered) return false;
            if (rect.width > 0 && rect.height > 0) return true;
            return allowComponent && textOf(el).includes(target);
        }

        function collect(root) {
            const result = [];
            const seen = new Set();
            function add(el) {
                if (!el || seen.has(el)) return;
                seen.add(el);
                result.push(el);
            }
            function visit(node) {
                if (!node) return;
                if (node.nodeType === Node.ELEMENT_NODE) add(node);
                if (node.querySelectorAll) {
                    for (const el of node.querySelectorAll(clickableSelector)) {
                        add(el);
                    }
                    for (const el of node.querySelectorAll('*')) {
                        if (el.shadowRoot) visit(el.shadowRoot);
                    }
                }
            }
            visit(root);
            return result;
        }

        function isAvoided(text) {
            return avoid.some(word => word && text.includes(word));
        }

        function defaultEventsFor(el) {
            if (explicitEventNames.length) return explicitEventNames;
            const combined = `${target} ${intent}`.toLowerCase();
            if (
                combined.includes('save')
                || target.includes('暂存')
                || target.includes('存草稿')
                || target.includes('保存')
            ) {
                return ['save', 'save-draft', 'saveDraft'];
            }
            if (
                combined.includes('delete')
                || combined.includes('remove')
                || target.includes('删除')
                || target.includes('移除')
            ) {
                return ['delete', 'remove'];
            }
            if (
                combined.includes('confirm')
                || target.includes('确认')
                || target.includes('确定')
            ) {
                return ['confirm'];
            }
            return [];
        }

        function score(el) {
            const fullText = textOf(el);
            const attrText = attrTextOf(el);
            if (!fullText || !fullText.includes(target)) return -9999;
            if (isAvoided(fullText) || isAvoided(attrText)) return -9999;
            if (!visible(el, true)) return -9999;

            const tag = el.tagName.toLowerCase();
            const cls = String(el.className || '');
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            let value = 0;
            if (fullText === target) value += 800;
            if (attrText === target) value += 700;
            if (attrText.includes(target)) value += 500;
            if (tag === 'button') value += 420;
            if (tag === 'a') value += 260;
            if (el.getAttribute('role') === 'button') value += 340;
            if (tag.includes('-')) value += 220;
            if (/btn|button|ce-btn/i.test(cls)) value += 220;
            if (el.hasAttribute('onclick') || el.hasAttribute('tabindex')) value += 80;
            if (style.position === 'fixed' || style.position === 'sticky') value += 60;
            if (rect.bottom > window.innerHeight * 0.65) value += 30;
            if (fullText.length <= Math.max(12, target.length + 4)) value += 120;
            value -= Math.max(0, fullText.length - target.length) * 2;
            return value;
        }

        const nodes = collect(document)
            .map(el => ({el, text: textOf(el), attrText: attrTextOf(el), score: score(el)}))
            .filter(item => item.score > -9999)
            .sort((a, b) => b.score - a.score);

        if (!nodes.length) {
            return {
                clicked: false,
                candidates: collect(document)
                    .map(el => textOf(el))
                    .filter(text => text && text.includes(target.slice(0, Math.min(2, target.length))))
                    .filter(text => !isAvoided(text))
                    .slice(0, 30)
            };
        }

        const picked = nodes[0];
        let el = picked.el;
        const customEvents = defaultEventsFor(el);
        const canDispatchComponentEvent = el.tagName.toLowerCase().includes('-')
            || attrTextOf(el).includes(target)
            || el.hasAttribute('save-text')
            || el.hasAttribute('submit-text')
            || el.hasAttribute('confirm-text')
            || el.hasAttribute('delete-text');

        if (canDispatchComponentEvent) {
            for (const eventName of customEvents) {
                el.dispatchEvent(new CustomEvent(eventName, {
                    bubbles: true,
                    composed: true,
                    detail: {source: 'xhs-agent', targetText}
                }));
                return {
                    clicked: true,
                    mode: `custom-event:${eventName}`,
                    text: picked.text,
                    attrText: picked.attrText,
                    score: picked.score,
                    tag: el.tagName.toLowerCase()
                };
            }
        }

        const actionable = el.closest && el.closest(
            'button,a,[role="button"],[role="link"],[onclick],[tabindex],'
            + '[class*="ce-btn"],[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"]'
        );
        if (actionable && textOf(actionable).includes(target) && !isAvoided(textOf(actionable))) {
            el = actionable;
        }
        el.scrollIntoView({block: 'center', inline: 'center'});
        el.click();
        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
        return {
            clicked: true,
            mode: 'dom-click',
            text: textOf(el) || picked.text,
            attrText: attrTextOf(el) || picked.attrText,
            score: picked.score,
            tag: el.tagName.toLowerCase()
        };
    }"""

    all_candidates = []
    for frame in page.frames:
        try:
            result = await frame.evaluate(script, payload)
        except Exception:
            continue
        if result.get("clicked"):
            print(
                "已通过语义目标点击："
                f"{target_text} mode={result.get('mode')} text={result.get('text')}"
            )
            await asyncio.sleep(1.2)
            return True
        all_candidates.extend(result.get("candidates") or [])

    if all_candidates:
        print(f"未点到语义目标“{target_text}”，相关候选：{all_candidates[:20]}")
    else:
        print(f"未点到语义目标“{target_text}”，页面中未发现相关候选")
    return False


async def click_near_text(page, near_text, target_text, intent="", avoid_texts=None):
    """
    在包含 near_text 的页面区域附近点击 target_text。

    这是锚点区域点击工具：当页面上有多个同名操作时，Agent 先从当前页面
    信息中选择能唯一定位目标对象的锚点文本，再在该区域内或附近点击目标操作。
    """
    near_text = str(near_text or "").strip()
    target_text = str(target_text or "").strip()
    if not near_text:
        raise Exception("near_text 不能为空")
    if not target_text:
        raise Exception("target_text 不能为空")

    avoid_texts = [str(item).strip() for item in (avoid_texts or []) if str(item).strip()]
    payload = {
        "nearText": near_text,
        "targetText": target_text,
        "intent": str(intent or ""),
        "avoidTexts": avoid_texts,
    }
    script = """payload => {
        const nearText = payload.nearText || '';
        const targetText = payload.targetText || '';
        const avoidTexts = payload.avoidTexts || [];
        const clickableSelector = [
            'button',
            'a',
            '[role="button"]',
            '[role="link"]',
            '[onclick]',
            '[tabindex]',
            '[class*="btn"]',
            '[class*="Btn"]',
            '[class*="button"]',
            '[class*="Button"]',
            'span',
            'div'
        ].join(',');

        function norm(text) {
            return String(text || '').replace(/\\s+/g, '').trim();
        }

        const near = norm(nearText);
        const target = norm(targetText);
        const avoid = avoidTexts.map(norm).filter(Boolean);

        function textOf(el) {
            if (!el) return '';
            const attrs = [
                'aria-label',
                'title',
                'data-text',
                'data-title',
                'data-name',
                'data-label'
            ].map(name => el.getAttribute && el.getAttribute(name)).filter(Boolean);
            return norm([attrs.join(' '), el.innerText || '', el.textContent || ''].join(' '));
        }

        function visible(el) {
            if (!el || !el.getBoundingClientRect) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden'
                && style.opacity !== '0'
                && style.pointerEvents !== 'none';
        }

        function isAvoided(text) {
            return avoid.some(word => word && text.includes(word));
        }

        const all = Array.from(document.querySelectorAll('*')).filter(visible);
        const nearNodes = all
            .map(el => ({el, text: textOf(el)}))
            .filter(item => item.text && item.text.includes(near) && !isAvoided(item.text))
            .sort((a, b) => a.text.length - b.text.length);

        function scoreContainer(el) {
            const text = textOf(el);
            if (!text.includes(near)) return -9999;
            let value = 0;
            if (text.includes(target)) value += 700;
            if (/comment|reply|note|card|item|list|content|interact/i.test(String(el.className || ''))) value += 120;
            const rect = el.getBoundingClientRect();
            if (rect.width > 120 && rect.height > 20) value += 40;
            value -= Math.max(0, text.length - near.length) * 0.03;
            return value;
        }

        const containers = [];
        const seen = new Set();
        for (const item of nearNodes.slice(0, 30)) {
            let node = item.el;
            for (let depth = 0; node && depth < 8; depth += 1) {
                if (!seen.has(node)) {
                    seen.add(node);
                    containers.push({el: node, score: scoreContainer(node), text: textOf(node)});
                }
                node = node.parentElement;
            }
        }
        containers.sort((a, b) => b.score - a.score);

        function candidatesIn(root) {
            return Array.from(root.querySelectorAll(clickableSelector))
                .filter(visible)
                .map(el => ({el, text: textOf(el)}))
                .filter(item => item.text && item.text.includes(target) && !isAvoided(item.text))
                .sort((a, b) => {
                    const aExact = a.text === target ? 1 : 0;
                    const bExact = b.text === target ? 1 : 0;
                    if (aExact !== bExact) return bExact - aExact;
                    return a.text.length - b.text.length;
                });
        }

        for (const container of containers) {
            const candidates = candidatesIn(container.el);
            if (candidates.length) {
                let el = candidates[0].el;
                const actionable = el.closest && el.closest(
                    'button,a,[role="button"],[role="link"],[onclick],[tabindex],'
                    + '[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"]'
                );
                if (actionable && textOf(actionable).includes(target)) {
                    el = actionable;
                }
                el.scrollIntoView({block: 'center', inline: 'center'});
                el.click();
                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                return {
                    clicked: true,
                    mode: 'near-text',
                    nearText: container.text.slice(0, 180),
                    clickedText: textOf(el).slice(0, 180)
                };
            }
        }

        return {
            clicked: false,
            nearCandidates: nearNodes.map(item => item.text).slice(0, 20),
            targetCandidates: all.map(el => textOf(el)).filter(text => text.includes(target)).slice(0, 20)
        };
    }"""

    all_candidates = []
    for frame in page.frames:
        try:
            result = await frame.evaluate(script, payload)
        except Exception:
            continue
        if result.get("clicked"):
            print(
                "已点击附近语义目标："
                f"near={near_text} target={target_text} mode={result.get('mode')} "
                f"clicked={result.get('clickedText')}"
            )
            await asyncio.sleep(1.2)
            return True
        all_candidates.append(result)

    print(
        f"未点到 near='{near_text}' 附近的 target='{target_text}'，"
        f"候选：{all_candidates[:3]}"
    )
    return False


async def click_media_near_text(page, near_text, intent="", avoid_texts=None):
    """
    在包含 near_text 的页面区域附近点击媒体缩略图/封面。

    这是通用媒体锚点工具：当列表项、消息、评论、卡片旁边有可点击图片/视频封面时，
    Agent 先用 near_text 定位目标对象，再点击该区域内或附近最像媒体封面的元素。
    """
    near_text = str(near_text or "").strip()
    if not near_text:
        raise Exception("near_text 不能为空")

    avoid_texts = [str(item).strip() for item in (avoid_texts or []) if str(item).strip()]
    payload = {
        "nearText": near_text,
        "intent": str(intent or ""),
        "avoidTexts": avoid_texts,
    }
    script = """payload => {
        const nearText = payload.nearText || '';
        const avoidTexts = payload.avoidTexts || [];
        const mediaSelector = [
            'img',
            'video',
            'canvas',
            '[role="img"]',
            '[class*="cover"]',
            '[class*="Cover"]',
            '[class*="image"]',
            '[class*="Image"]',
            '[class*="img"]',
            '[class*="Img"]',
            '[class*="thumb"]',
            '[class*="Thumb"]',
            '[style*="background-image"]'
        ].join(',');
        const clickableSelector = [
            'button',
            'a',
            '[role="button"]',
            '[role="link"]',
            '[onclick]',
            '[tabindex]',
            '[class*="card"]',
            '[class*="Card"]',
            '[class*="item"]',
            '[class*="Item"]',
            '[class*="cover"]',
            '[class*="Cover"]'
        ].join(',');

        function norm(text) {
            return String(text || '').replace(/\\s+/g, '').trim();
        }

        const near = norm(nearText);
        const avoid = avoidTexts.map(norm).filter(Boolean);

        function textOf(el) {
            if (!el) return '';
            const attrs = [
                'aria-label',
                'title',
                'alt',
                'data-text',
                'data-title',
                'data-name',
                'data-label'
            ].map(name => el.getAttribute && el.getAttribute(name)).filter(Boolean);
            return norm([attrs.join(' '), el.innerText || '', el.textContent || ''].join(' '));
        }

        function visible(el) {
            if (!el || !el.getBoundingClientRect) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 12 && rect.height > 12
                && style.display !== 'none'
                && style.visibility !== 'hidden'
                && style.opacity !== '0'
                && style.pointerEvents !== 'none';
        }

        function isAvoided(text) {
            return avoid.some(word => word && text.includes(word));
        }

        const all = Array.from(document.querySelectorAll('*')).filter(visible);
        const nearNodes = all
            .map(el => ({el, text: textOf(el)}))
            .filter(item => item.text && item.text.includes(near) && !isAvoided(item.text))
            .sort((a, b) => a.text.length - b.text.length);

        const containers = [];
        const seen = new Set();
        for (const item of nearNodes.slice(0, 30)) {
            let node = item.el;
            for (let depth = 0; node && depth < 9; depth += 1) {
                if (!seen.has(node)) {
                    seen.add(node);
                    containers.push(node);
                }
                node = node.parentElement;
            }
        }

        function mediaScore(el, container) {
            if (!visible(el)) return -9999;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const tag = el.tagName.toLowerCase();
            const cls = String(el.className || '');
            let value = 0;
            if (tag === 'img' || tag === 'video') value += 400;
            if (tag === 'canvas' || el.getAttribute('role') === 'img') value += 260;
            if (/cover|image|img|thumb|photo|media|preview/i.test(cls)) value += 220;
            if ((style.backgroundImage || '').includes('url(')) value += 180;
            if (rect.width >= 40 && rect.height >= 40) value += 80;
            if (rect.width >= 80 && rect.height >= 80) value += 60;
            const containerRect = container.getBoundingClientRect();
            const rightSide = rect.left > containerRect.left + containerRect.width * 0.45;
            if (rightSide) value += 45;
            value -= Math.abs(rect.width - rect.height) * 0.2;
            value -= Math.max(0, textOf(el).length - 80) * 0.4;
            return value;
        }

        for (const container of containers) {
            const media = Array.from(container.querySelectorAll(mediaSelector))
                .filter(visible)
                .map(el => ({el, score: mediaScore(el, container), text: textOf(el)}))
                .filter(item => item.score > -9999 && !isAvoided(item.text))
                .sort((a, b) => b.score - a.score);
            if (!media.length) continue;

            let el = media[0].el;
            const actionable = el.closest && el.closest(clickableSelector);
            if (actionable && visible(actionable)) {
                el = actionable;
            }
            el.scrollIntoView({block: 'center', inline: 'center'});
            el.click();
            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
            return {
                clicked: true,
                mode: 'media-near-text',
                nearText: textOf(container).slice(0, 180),
                clickedText: textOf(el).slice(0, 180),
                tag: el.tagName.toLowerCase()
            };
        }

        return {
            clicked: false,
            nearCandidates: nearNodes.map(item => item.text).slice(0, 20),
            mediaCount: Array.from(document.querySelectorAll(mediaSelector)).filter(visible).length
        };
    }"""

    all_candidates = []
    for frame in page.frames:
        try:
            result = await frame.evaluate(script, payload)
        except Exception:
            continue
        if result.get("clicked"):
            print(
                "已点击锚点附近媒体："
                f"near={near_text} mode={result.get('mode')} clicked={result.get('clickedText')}"
            )
            await asyncio.sleep(1.2)
            return True
        all_candidates.append(result)

    print(f"未点到 near='{near_text}' 附近的媒体，候选：{all_candidates[:3]}")
    return False


async def click_by_index(page, index, elements_cache):
    """
    根据元素索引点击。elements_cache 必须为提取的元素列表。
    """
    if not elements_cache:
        raise Exception("元素列表为空，无法执行点击")
    if index < 0 or index >= len(elements_cache):
        raise Exception(f"索引 {index} 超出范围（列表长度 {len(elements_cache)}）")
    element = elements_cache[index]
    desc = element['desc']
    text = _text_from_desc(desc)
    print(f"即将点击: {text}")
    locator = _locator_for_element(page, element)
    try:
        await locator.scroll_into_view_if_needed(timeout=5000)
        await locator.click(timeout=10000)
    except Exception as first_error:
        try:
            await _click_actionable_ancestor(locator)
        except Exception:
            print(f"稳定选择器点击失败，改用文本点击：{first_error}")
            await page.get_by_text(text, exact=False).first.click(timeout=10000)


async def click_text_in_element(page, index, text, elements_cache):
    """
    点击某个已提取元素内部的指定文本。

    用于列表卡片这类场景：卡片整体被提取为一个元素，但“删除/编辑”等文字按钮
    没有被单独提取成元素。Agent 可以表达：
    {"action": "click_text_in_element", "element_index": 42, "text": "删除"}
    """
    if not elements_cache:
        raise Exception("元素列表为空，无法执行内部文本点击")
    if index < 0 or index >= len(elements_cache):
        raise Exception(f"索引 {index} 超出范围（列表长度 {len(elements_cache)}）")
    if not text or not str(text).strip():
        raise Exception("内部点击文本不能为空")

    element = elements_cache[index]
    locator = _locator_for_element(page, element)
    target_text = str(text).strip()
    print(f"即将在元素 [{index}] 内点击文本: {target_text}")

    point = await locator.evaluate(
        """(root, targetText) => {
            const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = el => {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
            };

            root.scrollIntoView({block: 'center', inline: 'center'});

            const descendants = Array.from(root.querySelectorAll('*'))
                .filter(visible)
                .map(el => ({el, text: normalize(el.innerText || el.textContent)}))
                .filter(item => item.text && item.text.includes(targetText))
                .sort((a, b) => {
                    const aExact = a.text === targetText ? 1 : 0;
                    const bExact = b.text === targetText ? 1 : 0;
                    if (aExact !== bExact) return bExact - aExact;
                    return a.text.length - b.text.length;
                });

            if (descendants.length) {
                const el = descendants[0].el;
                const rect = el.getBoundingClientRect();
                return {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    mode: 'descendant',
                    matchedText: descendants[0].text
                };
            }

            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                const value = node.nodeValue || '';
                const start = value.indexOf(targetText);
                if (start < 0) continue;
                const range = document.createRange();
                range.setStart(node, start);
                range.setEnd(node, start + targetText.length);
                const rects = Array.from(range.getClientRects())
                    .filter(rect => rect.width > 0 && rect.height > 0);
                range.detach();
                if (rects.length) {
                    const rect = rects[0];
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        mode: 'text-range',
                        matchedText: targetText
                    };
                }
            }

            return null;
        }""",
        target_text,
    )
    if not point:
        raise Exception(f"元素 [{index}] 内未找到可点击文本：{target_text}")
    await page.mouse.click(point["x"], point["y"])
    print(f"已点击元素 [{index}] 内文本：{point.get('matchedText')} mode={point.get('mode')}")


async def fill_by_index(page, index, value, elements_cache):
    """
    根据索引填充输入框或 contenteditable。
    """
    if not elements_cache:
        raise Exception("元素列表为空，无法执行填充")
    if index < 0 or index >= len(elements_cache):
        raise Exception(f"索引 {index} 超出范围（列表长度 {len(elements_cache)}）")
    element = elements_cache[index]
    desc = element['desc']
    print(f"填充元素 [{index}] {desc} -> {value}")
    locator = _locator_for_element(page, element)
    try:
        await locator.fill(value, timeout=10000)
    except Exception:
        # 兼容部分富文本编辑器不支持 fill 的情况。
        await locator.click(timeout=10000)
        await page.keyboard.press("Control+A")
        await page.keyboard.type(value)


async def fill_textbox_by_hint(page, value, hint_text="", prefer_focused=True):
    """
    按提示文本/焦点填写可见输入框。

    适用于弹出的回复框、评论框、搜索框等动态输入区域。Agent 可以用 hint_text
    描述目标输入框；为空时优先填写当前焦点输入框，再选择最合理的空输入框。
    """
    value = str(value or "")
    hint_text = str(hint_text or "").strip()
    result = await page.evaluate(
        """payload => {
            const value = payload.value || '';
            const hintText = String(payload.hintText || '').replace(/\\s+/g, '').trim();
            const preferFocused = payload.preferFocused;
            const selectors = 'input, textarea, [contenteditable="true"], [contenteditable="plaintext-only"], [role="textbox"]';

            function norm(text) {
                return String(text || '').replace(/\\s+/g, '').trim();
            }

            function visible(el) {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
            }

            function hintOf(el) {
                return norm([
                    el.getAttribute('placeholder') || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('data-placeholder') || '',
                    el.getAttribute('title') || '',
                    el.innerText || '',
                    el.textContent || '',
                    el.value || ''
                ].join(' '));
            }

            function setValue(el) {
                el.scrollIntoView({block: 'center', inline: 'center'});
                el.focus();
                if (el.isContentEditable || el.getAttribute('contenteditable')) {
                    el.innerText = value;
                } else {
                    el.value = value;
                }
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return {
                    filled: true,
                    tag: el.tagName.toLowerCase(),
                    hint: hintOf(el).slice(0, 160)
                };
            }

            const nodes = Array.from(document.querySelectorAll(selectors)).filter(visible);
            const active = document.activeElement;
            if (preferFocused && active && nodes.includes(active)) {
                return setValue(active);
            }

            const scored = nodes.map(el => {
                const hint = hintOf(el);
                let score = 0;
                if (hintText && hint.includes(hintText)) score += 1000;
                if (/回复|评论|发送|留言|输入|搜索/.test(hint)) score += 180;
                if (!norm(el.value || el.innerText || el.textContent)) score += 70;
                if (el.tagName.toLowerCase() === 'textarea') score += 50;
                if (el.isContentEditable || el.getAttribute('contenteditable')) score += 40;
                return {el, score, hint};
            }).sort((a, b) => b.score - a.score);

            const picked = scored.find(item => item.score > 0) || scored[0];
            if (!picked) {
                return {filled: false, candidates: []};
            }
            return setValue(picked.el);
        }""",
        {
            "value": value,
            "hintText": hint_text,
            "preferFocused": prefer_focused,
        },
    )
    if result.get("filled"):
        print(f"已填写输入框：hint={result.get('hint')}")
        await asyncio.sleep(0.8)
        return True
    print(f"未找到可填写输入框，候选：{result.get('candidates')}")
    return False


async def fill_title_direct(page, title):
    """优先使用标题专用定位器填写标题，并用 DOM 事件通知前端框架。"""
    selectors = [
        'input[placeholder*="标题"]',
        'textarea[placeholder*="标题"]',
        '[contenteditable="true"][placeholder*="标题"]',
        '[role="textbox"][aria-label*="标题"]',
    ]
    for selector in selectors:
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            item = locator.nth(index)
            try:
                if not await item.is_visible(timeout=500):
                    continue
                await item.fill(title, timeout=5000)
                print("已通过标题专用定位器填写标题")
                return True
            except Exception:
                try:
                    await item.click(timeout=3000)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(title)
                    print("已通过键盘方式填写标题")
                    return True
                except Exception:
                    continue

    changed = await page.evaluate(
        """title => {
            const nodes = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]'));
            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
            }
            function score(el) {
                const hint = [
                    el.getAttribute('placeholder') || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('data-placeholder') || '',
                    el.innerText || '',
                    el.textContent || ''
                ].join(' ');
                let value = 0;
                if (/标题|title/i.test(hint)) value += 100;
                if (el.tagName.toLowerCase() === 'input') value += 20;
                if ((el.getAttribute('maxlength') || 0) && Number(el.getAttribute('maxlength')) <= 100) value += 10;
                value -= Math.max(0, (el.innerText || el.value || '').length - 30);
                return value;
            }
            const candidates = nodes.filter(visible).sort((a, b) => score(b) - score(a));
            const target = candidates.find(el => score(el) > 0);
            if (!target) return false;
            target.focus();
            if (target.isContentEditable || target.getAttribute('contenteditable') === 'true') {
                target.innerText = title;
            } else {
                target.value = title;
            }
            target.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: title}));
            target.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        title,
    )
    if changed:
        print("已通过 DOM 兜底方式填写标题")
    return bool(changed)


async def fill_content_direct(page, content):
    """优先填写正文编辑区，避免误把正文写进标题。"""
    selectors = [
        '[contenteditable="true"]',
        'textarea[placeholder*="正文"]',
        'textarea[placeholder*="内容"]',
        'textarea[placeholder*="分享"]',
        '[role="textbox"]',
    ]
    for selector in selectors:
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            item = locator.nth(index)
            try:
                if not await item.is_visible(timeout=500):
                    continue
                hint = await item.evaluate(
                    """el => [
                        el.getAttribute('placeholder') || '',
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('data-placeholder') || '',
                        el.innerText || '',
                        el.textContent || ''
                    ].join(' ')"""
                )
                if "标题" in hint:
                    continue
                try:
                    await item.fill(content, timeout=5000)
                except Exception:
                    await item.click(timeout=3000)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(content)
                print("已通过正文专用定位器填写正文")
                return True
            except Exception:
                continue
    return False


async def page_has_text_value(page, expected):
    return await page.evaluate(
        """expected => {
            const nodes = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]'));
            return nodes.some(el => ((el.value || el.innerText || el.textContent || '').trim()).includes(expected.slice(0, 30)));
        }""",
        expected,
    )

async def go_back(page):
    await page.go_back()

async def wait_seconds(page, seconds=1):
    await asyncio.sleep(seconds)


def _press_escape_globally():
    """Windows 兜底：如果原生文件选择器仍在前台，发送 Esc 尝试关闭。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        vk_escape = 0x1B
        key_event = 0x0001
        key_up = 0x0002
        user32.keybd_event(vk_escape, 0, key_event, 0)
        user32.keybd_event(vk_escape, 0, key_event | key_up, 0)
    except Exception:
        pass


def _pick_images(folder_path, num_images=3):
    folder = Path(folder_path).expanduser()
    if not folder.exists() or not folder.is_dir():
        print(f"图片文件夹不存在，跳过上传：{folder}")
        return []

    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')
    all_images = [f for f in folder.iterdir() if f.suffix.lower() in image_exts]
    if not all_images:
        print(f"图片文件夹无可用图片：{folder}")
        return []

    selected = random.sample(all_images, min(num_images, len(all_images)))
    return [str(img.resolve()) for img in selected]


def _pick_media_files(folder_path, default_file, count, exts, label):
    folder = Path(folder_path).expanduser() if folder_path else None
    if folder and folder.exists() and folder.is_dir():
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
        if files:
            selected = random.sample(files, min(count, len(files)))
            return [str(item.resolve()) for item in selected]
        print(f"{label}文件夹没有可用素材，将尝试默认素材：{folder}")
    elif folder_path:
        print(f"{label}文件夹不存在，将尝试默认素材：{folder}")
    else:
        print(f"未配置{label}文件夹，将尝试默认素材")

    fallback = Path(default_file).expanduser() if default_file else None
    if fallback and fallback.exists() and fallback.is_file() and fallback.suffix.lower() in exts:
        print(f"使用默认{label}素材：{fallback}")
        return [str(fallback.resolve())]

    print(f"默认{label}素材不可用：{fallback}")
    return []


def _pick_explicit_media_files(file_paths, count, exts, label):
    if not file_paths:
        return []

    selected = []
    for value in file_paths:
        path = Path(value).expanduser()
        if not path.exists() or not path.is_file():
            print(f"指定{label}素材不存在，已跳过：{path}")
            continue
        if path.suffix.lower() not in exts:
            print(f"指定{label}素材格式不支持，已跳过：{path}")
            continue
        selected.append(str(path.resolve()))

    if not selected:
        print(f"指定{label}素材列表中没有可用文件")
        return []
    return selected[:count]


async def _click_exact_text(page, text, timeout=3000):
    locator = page.get_by_text(text, exact=True)
    count = await locator.count()
    for index in range(count):
        item = locator.nth(index)
        try:
            if await item.is_visible(timeout=500):
                await item.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def switch_to_image_upload_tab(page):
    """确保页面处于图文上传模式，避免误用视频上传 input。"""
    for text in _browser_words("upload_modes", "image_entry_texts"):
        if await _click_exact_text(page, text):
            print(f"已切换/点击图文入口：{text}")
            await asyncio.sleep(1)
            return True
    return False


async def switch_to_video_upload_tab(page):
    """确保页面处于视频上传模式。"""
    candidates = await _file_input_candidates(page)
    if _choose_video_file_input(candidates):
        return True

    for text in _browser_words("upload_modes", "video_entry_texts"):
        if await _click_exact_text(page, text):
            print(f"已切换/点击视频入口：{text}")
            await asyncio.sleep(1)
            _press_escape_globally()
            return True
    return False


async def _file_input_candidates(page):
    return await page.evaluate(
        """terms => Array.from(document.querySelectorAll('input[type="file"]')).map((el, index) => {
            const accept = (el.getAttribute('accept') || '').toLowerCase();
            const rect = el.getBoundingClientRect();
            const nearText = (el.closest('div,section,main,form')?.innerText || '')
                .replace(/\\s+/g, ' ')
                .slice(0, 500);
            const includesAny = (text, words) => (words || []).some(word => text.includes(word));
            const isImage = accept.includes('image') || includesAny(nearText, terms.imageNearTerms);
            const isVideo = accept.includes('video') || (includesAny(nearText, terms.videoNearTerms) && !isImage);
            return {
                index,
                accept,
                multiple: el.multiple,
                visible: rect.width > 0 && rect.height > 0,
                nearText,
                isVideo,
                isImage
            };
        })""",
        {
            "imageNearTerms": _browser_words("file_input_detection", "image_near_terms"),
            "videoNearTerms": _browser_words("file_input_detection", "video_near_terms"),
        },
    )


def _choose_image_file_input(candidates):
    candidates = [item for item in candidates if not item["isVideo"]]
    candidates = [item for item in candidates if item["isImage"] or "video" not in item["accept"]]
    if not candidates:
        return None

    def score(item):
        value = 0
        if item["isImage"]:
            value += 100
        if "image" in item["accept"]:
            value += 50
        if item["multiple"]:
            value += 20
        if item["visible"]:
            value += 5
        value -= len(item["nearText"]) / 1000
        return value

    return sorted(candidates, key=score, reverse=True)[0]


def _choose_video_file_input(candidates):
    candidates = [item for item in candidates if item["isVideo"] or "video" in item["accept"]]
    if not candidates:
        return None

    def score(item):
        value = 0
        if item["isVideo"]:
            value += 100
        if "video" in item["accept"]:
            value += 50
        if item["visible"]:
            value += 5
        value -= len(item["nearText"]) / 1000
        return value

    return sorted(candidates, key=score, reverse=True)[0]


async def wait_for_image_upload_done(page, expected_count=1, timeout=60000):
    """等待图片上传后的页面信号。这个函数观察网页 DOM，不依赖系统弹窗。"""
    try:
        await page.wait_for_function(
            """args => {
                const bodyText = document.body?.innerText || '';
                const imageLikeNodes = Array.from(document.querySelectorAll(
                    'img, canvas, [class*="thumb"], [class*="Thumb"], [class*="image"], [class*="Image"], [class*="preview"], [class*="Preview"]'
                ));
                const visibleImageNodes = imageLikeNodes.filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 20 && rect.height > 20 && style.display !== 'none' && style.visibility !== 'hidden';
                });
                return visibleImageNodes.length >= args.expected
                    || (args.signals || []).some(word => bodyText.includes(word));
            }""",
            {
                "expected": expected_count,
                "signals": _browser_words("upload_done_signals", "image"),
            },
            timeout=timeout,
        )
        return True
    except Exception:
        print("未等到明确上传完成信号，继续观察页面")
        return False


async def wait_for_video_upload_done(page, timeout=120000):
    """等待视频文件提交后的页面信号。"""
    try:
        await page.wait_for_function(
            """signals => {
                const bodyText = document.body?.innerText || '';
                const videoNodes = Array.from(document.querySelectorAll(
                    'video, canvas, [class*="video"], [class*="Video"], [class*="preview"], [class*="Preview"]'
                ));
                const visibleVideoNodes = videoNodes.filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 20 && rect.height > 20 && style.display !== 'none' && style.visibility !== 'hidden';
                });
                return visibleVideoNodes.length > 0
                    || (signals || []).some(word => bodyText.includes(word));
            }""",
            _browser_words("upload_done_signals", "video"),
            timeout=timeout,
        )
        return True
    except Exception:
        print("未等到明确视频上传完成信号，继续观察页面")
        return False


async def close_upload_dialog_if_present(page):
    """
    上传图片后收尾网页弹窗。优先点击确认类按钮，避免点到最终发布按钮。
    """
    async def modal_still_visible():
        return await page.evaluate(
            """() => {
                const selectors = [
                    '[role="dialog"]',
                    '[aria-modal="true"]',
                    '[class*="modal"]',
                    '[class*="Modal"]',
                    '[class*="dialog"]',
                    '[class*="Dialog"]'
                ];
                return selectors.some(selector => Array.from(document.querySelectorAll(selector)).some(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden';
                }));
            }"""
        )

    async def has_close_candidate():
        return await page.evaluate(
            """safeTexts => {
                return Array.from(document.querySelectorAll('button, [role="button"], [tabindex], span, div')).some(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && safeTexts.some(word => text === word || (text.includes(word) && text.length <= 20));
                });
            }""",
            _browser_words("upload_dialog", "safe_texts"),
        )

    if not await modal_still_visible() and not await has_close_candidate():
        print("未检测到上传弹窗或收尾按钮")
        return False

    for _ in range(5):
        clicked_text = await page.evaluate(
            """terms => {
                const safeTexts = terms.safeTexts || [];
                const dangerousTexts = terms.dangerousTexts || [];
                const candidates = Array.from(document.querySelectorAll(
                    'button, [role="button"], [tabindex], .btn, [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"], span, div'
                ));

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.pointerEvents !== 'none'
                        && !el.disabled
                        && el.getAttribute('aria-disabled') !== 'true';
                }

                function inDialog(el) {
                    return !!el.closest('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="dialog"], [class*="Dialog"], [class*="upload"], [class*="Upload"]');
                }

                for (const targetText of safeTexts) {
                    const exact = candidates
                        .filter(el => visible(el) && (el.innerText || el.textContent || '').trim() === targetText)
                        .sort((a, b) => Number(inDialog(b)) - Number(inDialog(a)));
                    const loose = candidates
                        .filter(el => {
                            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                            return visible(el)
                                && text.includes(targetText)
                                && text.length <= 20
                                && !dangerousTexts.some(word => text.includes(word));
                        })
                        .sort((a, b) => Number(inDialog(b)) - Number(inDialog(a)));

                    for (const el of [...exact, ...loose]) {
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (dangerousTexts.some(word => text.includes(word))) continue;
                        const clickable = el.closest('button, [role="button"], [tabindex], .btn, [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"]') || el;
                        clickable.scrollIntoView({block: 'center', inline: 'center'});
                        clickable.click();
                        return text || targetText;
                    }
                }
                return '';
            }""",
            {
                "safeTexts": _browser_words("upload_dialog", "safe_texts"),
                "dangerousTexts": _browser_words("upload_dialog", "dangerous_texts"),
            },
        )
        if clicked_text:
            print(f"已点击上传弹窗收尾按钮：{clicked_text}")
            await asyncio.sleep(2)
            if not await modal_still_visible():
                return True
            print("点击后弹窗仍存在，继续尝试其他收尾方式")
        await asyncio.sleep(1)

    close_selectors = [
        '[aria-label*="关闭"]',
        '[title*="关闭"]',
        '[class*="close"]',
        '[class*="Close"]',
    ]
    for selector in close_selectors:
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(min(count, 5)):
            item = locator.nth(index)
            try:
                if await item.is_visible(timeout=500):
                    await item.click(timeout=3000)
                    print("已点击上传弹窗关闭按钮")
                    await asyncio.sleep(1)
                    if not await modal_still_visible():
                        return True
            except Exception:
                continue

    if await modal_still_visible():
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
            if not await modal_still_visible():
                print("已通过 Escape 关闭上传弹窗")
                return True
        except Exception:
            pass

    print("未发现需要关闭的上传弹窗")
    return False


async def dump_save_stage_snapshot(page, reason="save_stage"):
    """调试期快照保存入口已关闭，保留函数避免旧测试脚本导入失败。"""
    return None


async def reveal_save_controls(page):
    """滚动所有可滚动容器，尽量让底部发布/暂存区域进入 DOM 可见区域。"""
    await page.evaluate(
        """() => {
            const scrollables = [document.scrollingElement, document.documentElement, document.body]
                .concat(Array.from(document.querySelectorAll('*')).filter(el => {
                    const style = window.getComputedStyle(el);
                    return /(auto|scroll)/.test(style.overflowY)
                        && el.scrollHeight > el.clientHeight + 20;
                }));
            for (const el of scrollables) {
                try {
                    el.scrollTop = el.scrollHeight;
                    el.dispatchEvent(new Event('scroll', {bubbles: true}));
                } catch (e) {}
            }
            const publishPage = document.querySelector('.publish-page, [class*="publish-page"], [class*="PublishPage"]');
            if (publishPage) {
                publishPage.scrollTop = publishPage.scrollHeight;
                publishPage.dispatchEvent(new Event('scroll', {bubbles: true}));
            }
            window.scrollTo(0, document.body.scrollHeight);
        }"""
    )
    try:
        await page.mouse.wheel(0, 1800)
    except Exception:
        pass
    await asyncio.sleep(0.8)


async def click_save_and_leave(page):
    """保存草稿并结束本次任务。"""
    target_text = _browser_text("save_and_leave", "target_text")
    save_words = _browser_words("save_and_leave", "save_words")
    publish_words = _browser_words("save_and_leave", "publish_words")
    danger_words = _browser_words("save_and_leave", "danger_words")
    candidate_terms = _browser_words("save_and_leave", "candidate_terms")
    avoid_texts = _browser_words("save_and_leave", "avoid_texts")
    event_names = _browser_words("save_and_leave", "event_names")
    try:
        if await click_semantic_target(
            page,
            target_text,
            intent=_browser_text("save_and_leave", "intent"),
            avoid_texts=avoid_texts,
            event_names=event_names,
        ):
            return True
    except Exception:
        pass

    async def click_xhs_publish_component_save():
        script = """terms => {
            const components = Array.from(document.querySelectorAll('xhs-publish-btn[save-text], xhs-publish-btn[is-save-draft]'));
            const saveWords = terms.saveWords || [];
            const publishWords = terms.publishWords || [];
            const clickableSelector = [
                'button',
                '[role="button"]',
                '[tabindex]',
                '[class*="ce-btn"]',
                '[class*="btn"]',
                '[class*="Btn"]',
                '[class*="button"]',
                '[class*="Button"]',
                'span',
                'div'
            ].join(',');

            function norm(text) {
                return (text || '').replace(/\\s+/g, '').trim();
            }
            function visible(el, allowComponent = false) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (allowComponent && rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                    return true;
                }
                return rect.width > 0 && rect.height > 0
                    && rect.bottom >= 0
                    && rect.top <= window.innerHeight
                    && rect.right >= 0
                    && rect.left <= window.innerWidth
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.pointerEvents !== 'none'
                    && !el.disabled
                    && el.getAttribute('aria-disabled') !== 'true';
            }
            function textOf(el) {
                return norm(
                    el.innerText
                    || el.textContent
                    || el.getAttribute('aria-label')
                    || el.getAttribute('title')
                    || el.getAttribute('save-text')
                    || ''
                );
            }
            function isSaveTarget(el) {
                const text = textOf(el);
                const cls = String(el.className || '');
                if (publishWords.some(word => text.includes(word))) return false;
                if (saveWords.some(word => text.includes(word))) return true;
                return /(^|\\s)white(\\s|$)|ce-btn\\s+white|white.*ce-btn|ce-btn.*white/.test(cls);
            }
            function clickElement(el) {
                const target = el.closest('button, [role="button"], [tabindex], [class*="ce-btn"], [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"]') || el;
                target.scrollIntoView({block: 'center', inline: 'center'});
                target.click();
                target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                const rect = target.getBoundingClientRect();
                return {
                    clicked: true,
                    mode: 'dom',
                    text: textOf(target) || textOf(el),
                    className: String(target.className || ''),
                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                };
            }

            const diagnostics = [];
            for (const component of components) {
                component.scrollIntoView({block: 'center', inline: 'center'});
                const saveText = component.getAttribute('save-text') || '';
                const submitText = component.getAttribute('submit-text') || '';
                const saveDisabled = component.getAttribute('save-disabled') === 'true';
                if (saveText && !saveDisabled) {
                    component.dispatchEvent(new CustomEvent('save', {bubbles: true, composed: true}));
                    return {
                        clicked: true,
                        mode: 'custom-event',
                        text: saveText,
                        component: 'xhs-publish-btn'
                    };
                }
                const roots = [component];
                if (component.shadowRoot) roots.push(component.shadowRoot);
                for (const root of roots) {
                    const nodes = Array.from(root.querySelectorAll ? root.querySelectorAll(clickableSelector) : [])
                        .filter(el => visible(el));
                    const explicit = nodes
                        .filter(el => saveWords.some(word => textOf(el).includes(word)))
                        .sort((a, b) => textOf(a).length - textOf(b).length);
                    if (explicit.length) return clickElement(explicit[0]);

                    const whiteButtons = nodes
                        .filter(isSaveTarget)
                        .filter(el => !publishWords.some(word => textOf(el).includes(word)));
                    if (whiteButtons.length) return clickElement(whiteButtons[0]);
                }

                const rect = component.getBoundingClientRect();
                diagnostics.push({
                    saveText,
                    submitText,
                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                    outer: component.outerHTML.slice(0, 300)
                });
            }
            return {clicked: false, diagnostics};
        }"""
        diagnostics = []
        terms = {"saveWords": save_words, "publishWords": publish_words}
        for frame in page.frames:
            try:
                result = await frame.evaluate(script, terms)
            except Exception:
                continue
            if result.get("clicked"):
                print(f"已通过 xhs-publish-btn 组件点击暂存：{result.get('text')} mode={result.get('mode')}")
                await asyncio.sleep(1.5)
                return True
            diagnostics.extend(result.get("diagnostics") or [])
        if diagnostics:
            print(f"检测到 xhs-publish-btn 但未点击成功：{diagnostics[:3]}")
        return False

    async def click_save_candidate():
        for frame in page.frames:
            try:
                buttons = frame.locator("button.ce-btn.white")
                count = await buttons.count()
            except Exception:
                continue
            for index in range(min(count, 20)):
                button = buttons.nth(index)
                try:
                    text = (await button.inner_text(timeout=500)).replace("\n", "").strip()
                    if text == target_text or target_text in text or any(word in text for word in save_words):
                        await button.scroll_into_view_if_needed(timeout=1000)
                        await button.click(timeout=3000, force=True)
                        print("已通过精确选择器点击：暂存离开（保存到草稿箱）")
                        await asyncio.sleep(1.5)
                        return True
                except Exception:
                    continue

        script = """terms => {
                const saveWords = terms.saveWords || [];
                const dangerWords = terms.dangerWords || [];
                const candidateTerms = terms.candidateTerms || [];
                const targetText = terms.targetText || '';
                const selectors = [
                    'button.ce-btn.white',
                    'button',
                    '[role="button"]',
                    '[tabindex]',
                    '[class*="btn"]',
                    '[class*="Btn"]',
                    '[class*="button"]',
                    '[class*="Button"]',
                    'span',
                    'div'
                ].join(',');

                function collect(root) {
                    const result = [];
                    const visit = node => {
                        if (!node) return;
                        if (node.querySelectorAll) {
                            result.push(...Array.from(node.querySelectorAll(selectors)));
                        }
                        const all = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
                        for (const item of all) {
                            if (item.shadowRoot) visit(item.shadowRoot);
                        }
                    };
                    visit(root);
                    return result;
                }

                function norm(text) {
                    return (text || '').replace(/\\s+/g, '').trim();
                }

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (el.matches && el.matches('button.ce-btn.white') && candidateTerms.some(word => norm(el.innerText || el.textContent).includes(word))) {
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    }
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.pointerEvents !== 'none'
                        && !el.disabled
                        && el.getAttribute('aria-disabled') !== 'true';
                }

                function score(el) {
                    const text = norm(el.innerText || el.textContent);
                    if (!text || dangerWords.some(word => text.includes(word))) return -9999;
                    if (!saveWords.some(word => text.includes(word))) return -9999;
                    const isNativeButton = el.matches('button.ce-btn.white') || el.tagName.toLowerCase() === 'button' || el.getAttribute('role') === 'button';
                    if (text.length > 80 && !isNativeButton) return -9999;
                    let value = 0;
                    if (el.matches('button.ce-btn.white')) value += 1000;
                    if (el.tagName.toLowerCase() === 'button') value += 500;
                    if (el.getAttribute('role') === 'button') value += 300;
                    if (targetText && text === targetText) value += 200;
                    if (text.length <= 12) value += 50;
                    if (el.closest('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="dialog"], [class*="Dialog"]')) value += 20;
                    return value;
                }

                const nodes = collect(document).filter(visible);
                const candidates = nodes
                    .map(el => ({el, text: norm(el.innerText || el.textContent), score: score(el)}))
                    .filter(item => item.score > -9999)
                    .sort((a, b) => b.score - a.score);

                if (!candidates.length) {
                    return {
                        clicked: false,
                        candidates: nodes
                            .map(el => norm(el.innerText || el.textContent))
                            .filter(Boolean)
                            .filter(text => candidateTerms.some(word => text.includes(word)))
                            .filter(text => text.length <= 160)
                            .slice(0, 20)
                    };
                }

                let target = candidates[0].el;
                target = target.closest('button, [role="button"], [tabindex], [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"]') || target;
                target.scrollIntoView({block: 'center', inline: 'center'});
                target.click();
                target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                return {clicked: true, text: candidates[0].text, candidates: candidates.map(item => item.text).slice(0, 5)};
            }"""
        all_candidates = []
        terms = {
            "saveWords": save_words,
            "dangerWords": danger_words,
            "candidateTerms": candidate_terms,
            "targetText": target_text,
        }
        for frame in page.frames:
            try:
                result = await frame.evaluate(script, terms)
            except Exception:
                continue
            if result.get("clicked"):
                print(f"已点击：{result.get('text')}（保存到草稿箱）")
                await asyncio.sleep(1.5)
                return True
            all_candidates.extend(result.get("candidates") or [])
        if all_candidates:
            print(f"未点到暂存按钮，页面相关候选文本：{all_candidates[:20]}")
        return False

    if await click_xhs_publish_component_save():
        return True

    if await click_save_candidate():
        return True

    await reveal_save_controls(page)
    if await click_xhs_publish_component_save():
        return True
    if await click_save_candidate():
        return True

    async def click_text(text):
        locator = page.get_by_text(text, exact=True)
        count = await locator.count()
        for index in range(count):
            item = locator.nth(index)
            try:
                if await item.is_visible(timeout=500):
                    await item.click(timeout=3000)
                    print(f"已点击：{text}")
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                continue
        return False

    if await click_text(target_text):
        return True

    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        if await click_save_candidate():
            return True
        if await click_text(target_text):
            return True
    except Exception:
        pass

    print("未找到“暂存离开”，保持当前草稿页面")
    return False


async def upload_images_directly(page, folder_path, num_images=3):
    """
    绕过系统文件弹窗，直接定位图片 input[type=file] 并 set_input_files。
    这一步由代码完成，不交给 LLM 判断系统弹窗。
    """
    selected_paths = _pick_images(folder_path, num_images)
    if not selected_paths:
        return False

    _press_escape_globally()
    await switch_to_image_upload_tab(page)

    uploaded = 0
    remaining = list(selected_paths)
    for _ in range(min(len(selected_paths), 5)):
        candidates = await _file_input_candidates(page)
        print(f"文件上传 input 候选：{candidates}")
        candidate = _choose_image_file_input(candidates)
        if not candidate:
            print("未找到图片上传 input，停止上传")
            break

        paths_for_this_input = remaining if candidate["multiple"] else remaining[:1]
        print(f"准备向图片 input 上传：{paths_for_this_input}")
        await page.locator('input[type="file"]').nth(candidate["index"]).set_input_files(paths_for_this_input)
        _press_escape_globally()
        uploaded += len(paths_for_this_input)
        remaining = remaining[len(paths_for_this_input):]

        await wait_for_image_upload_done(page, expected_count=uploaded, timeout=20000)
        if not remaining or candidate["multiple"]:
            break
        print("当前图片 input 不支持一次选择多张，已上传 1 张；避免覆盖已选文件，停止本轮上传")
        break

    if uploaded:
        print(f"图片文件已提交给网页：{uploaded}/{len(selected_paths)}")
        await close_upload_dialog_if_present(page)
        return True

    return False


async def upload_video_directly(page, folder_path, default_file=None, num_videos=1):
    """
    绕过系统文件弹窗，直接定位视频 input[type=file] 并 set_input_files。
    """
    selected_paths = _pick_media_files(
        folder_path,
        default_file,
        num_videos,
        {'.mp4', '.mov', '.avi', '.mkv', '.webm'},
        "视频",
    )
    if not selected_paths:
        return False

    _press_escape_globally()
    candidate = None
    candidates = []
    for attempt in range(3):
        candidates = await _file_input_candidates(page)
        print(f"文件上传 input 候选：{candidates}")
        candidate = _choose_video_file_input(candidates)
        if candidate:
            break
        if attempt == 0:
            print("暂未发现视频 input，尝试切换到视频上传页")
            await switch_to_video_upload_tab(page)
            _press_escape_globally()
            await asyncio.sleep(1.5)
        else:
            await asyncio.sleep(1)

    if not candidate:
        print("未找到视频上传 input，停止上传")
        return False

    path_for_input = selected_paths[:1]
    print(f"准备向视频 input 上传：{path_for_input}")
    await page.locator('input[type="file"]').nth(candidate["index"]).set_input_files(path_for_input)
    _press_escape_globally()
    await wait_for_video_upload_done(page, timeout=120000)
    print(f"视频文件已提交给网页：{path_for_input[0]}")
    await close_upload_dialog_if_present(page)
    return True


async def upload_media_directly(
    page,
    post_type,
    image_folder=None,
    video_folder=None,
    image_files=None,
    video_files=None,
    default_image_file=None,
    default_video_file=None,
    num_images=3,
    num_videos=1,
):
    """根据发布类型上传图片或视频素材。"""
    if post_type == "video":
        selected_video_files = _pick_explicit_media_files(
            video_files,
            num_videos,
            {'.mp4', '.mov', '.avi', '.mkv', '.webm'},
            "视频",
        )
        if selected_video_files:
            folder_for_video = str(Path(selected_video_files[0]).parent)
            return await upload_video_directly(
                page,
                folder_for_video,
                default_file=selected_video_files[0],
                num_videos=1,
            )
        return await upload_video_directly(
            page,
            video_folder,
            default_file=default_video_file,
            num_videos=num_videos,
        )

    image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    selected_paths = _pick_explicit_media_files(image_files, num_images, image_exts, "图片")
    if not selected_paths:
        selected_paths = _pick_media_files(
            image_folder,
            default_image_file,
            num_images,
            image_exts,
            "图片",
        )
    if not selected_paths:
        return False

    _press_escape_globally()
    await switch_to_image_upload_tab(page)

    uploaded = 0
    remaining = list(selected_paths)
    for _ in range(min(len(selected_paths), 5)):
        candidates = await _file_input_candidates(page)
        print(f"文件上传 input 候选：{candidates}")
        candidate = _choose_image_file_input(candidates)
        if not candidate:
            print("未找到图片上传 input，停止上传")
            break

        paths_for_this_input = remaining if candidate["multiple"] else remaining[:1]
        print(f"准备向图片 input 上传：{paths_for_this_input}")
        await page.locator('input[type="file"]').nth(candidate["index"]).set_input_files(paths_for_this_input)
        _press_escape_globally()
        uploaded += len(paths_for_this_input)
        remaining = remaining[len(paths_for_this_input):]

        await wait_for_image_upload_done(page, expected_count=uploaded, timeout=20000)
        if not remaining or candidate["multiple"]:
            break
        print("当前图片 input 不支持一次选择多张，已上传 1 张；避免覆盖已选文件，停止本轮上传")
        break

    if uploaded:
        print(f"图片文件已提交给网页：{uploaded}/{len(selected_paths)}")
        await close_upload_dialog_if_present(page)
        return True

    return False


async def click_and_handle_file_chooser(page, target_text, folder_path, num_images=3, target_selector=None, timeout=10000):
    """
    点击目标文本并自动处理弹出的文件选择器，上传随机图片。
    """
    selected = _pick_images(folder_path, num_images)
    if not selected:
        return False

    try:
        async with page.expect_file_chooser(timeout=timeout) as fc_info:
            if target_selector:
                await page.locator(target_selector).click(timeout=10000)
            else:
                await page.get_by_text(target_text, exact=False).first.click(timeout=10000)
            file_chooser = await fc_info.value
    except PlaywrightTimeoutError:
        print("本次点击未弹出文件选择器，按普通点击继续")
        return False

    is_multiple = file_chooser.is_multiple()
    paths = selected if is_multiple else selected[:1]
    print(f"上传图片：{paths}")
    await file_chooser.set_files(paths)
    await wait_for_image_upload_done(page, expected_count=len(paths), timeout=20000)
    print("图片上传完毕")
    return True
