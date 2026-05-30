# src/core_function/element_extractor.py
import asyncio

from playwright.async_api import Error as PlaywrightError


async def extract_interactive_elements(page, max_retries=3):
    """
    提取页面可交互元素，若为空则等待后重试。
    返回示例：
    [{'index': 0, 'desc': '[button] 发布图文笔记', 'selector': '[data-xhs-agent-id="xhs-agent-0"]'}]
    """
    if page.is_closed():
        print("页面已关闭，无法提取元素")
        return []

    for attempt in range(max_retries):
        try:
            elements = await _do_extract(page)
        except PlaywrightError as exc:
            if page.is_closed() or "Target page" in str(exc):
                print("页面或浏览器已关闭，停止元素提取")
                return []
            print(f"元素提取失败：{exc}")
            elements = []

        if elements:
            return elements

        print(f"元素提取为空，等待后重试 ({attempt + 1}/{max_retries})...")
        await asyncio.sleep(2)

    print("警告：多次尝试后仍未提取到任何可交互元素")
    return []


async def _do_extract(page):
    """从当前页面提取可见、可操作或可填写的节点，并为节点写入稳定临时选择器。"""
    await page.wait_for_load_state("domcontentloaded", timeout=10000)
    elements = await page.evaluate(
        '''() => {
        const result = [];
        const selector = [
            'button',
            'a',
            'input',
            'textarea',
            'select',
            'label',
            '[role="button"]',
            '[role="link"]',
            '[role="textbox"]',
            '[contenteditable="true"]',
            '[contenteditable="plaintext-only"]',
            '[tabindex]:not([tabindex="-1"])',
            '[aria-label]',
            '[onclick]',
            '[class*="btn"]',
            '[class*="Btn"]',
            '[class*="button"]',
            '[class*="Button"]',
            '[class*="upload"]',
            '[class*="Upload"]',
            '[class*="publish"]',
            '[class*="Publish"]',
            'xhs-publish-btn',
            '[class*="menu"]',
            '[class*="Menu"]',
            '[class*="item"]',
            '[class*="Item"]',
            '[class*="editor"]',
            '[class*="Editor"]'
        ].join(',');
        const priorityWords = ['暂存离开', '暂存并离开', '保存草稿', '存草稿', '保存并离开', '离开并保存'];

        const nodes = Array.from(document.querySelectorAll(selector));
        const seenNodes = new Set();
        const seenTargets = new Set();
        let rawIndex = 0;

        function normalize(text) {
            return (text || '').replace(/\\s+/g, ' ').trim();
        }

        function isVisible(el) {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return false;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
            if (el.closest('[aria-hidden="true"]')) return false;
            return true;
        }

        function looksInteractive(el) {
            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute('role') || '';
            const cls = el.className ? String(el.className) : '';
            if (['button', 'a', 'input', 'textarea', 'select', 'label'].includes(tag)) return true;
            if (['button', 'link', 'textbox'].includes(role)) return true;
            if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return true;
            if (el.hasAttribute('onclick') || el.hasAttribute('aria-label')) return true;
            if (el.tabIndex >= 0) return true;
            return /(btn|button|upload|publish|menu|item|editor|input|textarea)/i.test(cls);
        }

        function elementType(el) {
            const tag = el.tagName.toLowerCase();
            if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return 'contenteditable';
            return el.getAttribute('role') || tag;
        }

        function actionTarget(el) {
            return el.closest(
                'button,a,[role="button"],[role="link"],[onclick],[tabindex],'
                + '[class*="btn"],[class*="Btn"],[class*="button"],[class*="Button"],'
                + '[class*="publish"],[class*="Publish"],[class*="upload"],[class*="Upload"]'
                + ',xhs-publish-btn,[class*="menu"],[class*="Menu"],[class*="item"],[class*="Item"]'
            ) || el;
        }

        function elementText(el, type) {
            const tag = el.tagName.toLowerCase();
            const aria = normalize(el.getAttribute('aria-label'));
            const title = normalize(el.getAttribute('title'));
            const placeholder = normalize(el.getAttribute('placeholder'));
            const value = normalize(el.value);
            const saveText = normalize(el.getAttribute('save-text'));
            const submitText = normalize(el.getAttribute('submit-text'));

            if (tag === 'xhs-publish-btn') {
                return saveText || submitText || normalize(el.innerText || el.textContent) || '发布操作按钮';
            }

            if (tag === 'input' || tag === 'textarea') {
                const label = el.id ? normalize(document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText) : '';
                let text = placeholder || aria || label || title || value || `${tag} 输入框`;
                if (value) text += ` (当前值: ${value})`;
                return text;
            }

            if (type === 'contenteditable') {
                const inner = normalize(el.innerText || el.textContent);
                return inner || aria || placeholder || title || '空白正文编辑区';
            }

            const imgAlt = normalize(el.querySelector('img[alt]')?.getAttribute('alt'));
            return normalize(el.innerText || el.textContent) || aria || title || imgAlt || value;
        }

        for (const el of nodes) {
            if (seenNodes.has(el)) continue;
            seenNodes.add(el);
            if (!looksInteractive(el) || !isVisible(el)) continue;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
            if (el.tagName.toLowerCase() === 'input' && el.type === 'hidden') continue;

            const type = elementType(el);
            let text = elementText(el, type);
            if (!text) continue;
            if (text.length > 120) text = text.slice(0, 117) + '...';

            const target = actionTarget(el);
            if (seenTargets.has(target)) continue;
            seenTargets.add(target);

            const agentId = `xhs-agent-${Date.now()}-${rawIndex}`;
            target.setAttribute('data-xhs-agent-id', agentId);
            result.push({
                index: result.length,
                type,
                text,
                desc: `[${type}] ${text}`,
                selector: `[data-xhs-agent-id="${agentId}"]`
            });
            rawIndex += 1;
        }

        const seen = new Set();
        const filtered = result.filter(item => {
            const key = `${item.type}:${item.text}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
        filtered.sort((a, b) => {
            const aPriority = priorityWords.some(word => a.text.includes(word)) ? 1 : 0;
            const bPriority = priorityWords.some(word => b.text.includes(word)) ? 1 : 0;
            return bPriority - aPriority;
        });
        return filtered.map((item, index) => ({...item, index}));
    }'''
    )
    return elements
