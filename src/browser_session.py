import os
import asyncio
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, async_playwright


# 获取项目根目录（browser_session.py -> src -> root）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUTH_FILE = str(PROJECT_ROOT / "auth.json")
BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser_profile" / "xhs_creator"
CREATOR_HOME_URL = "https://creator.xiaohongshu.com/creator/home"
XHS_WEB_AUTH_FILE = str(PROJECT_ROOT / "auth_xhs_web.json")
XHS_WEB_PROFILE_DIR = PROJECT_ROOT / ".browser_profile" / "xhs_web"
XHS_HOME_URL = "https://www.xiaohongshu.com/"


class _BrowserCloser:
    def __init__(self, close_target):
        self._close_target = close_target

    async def close(self):
        await self._close_target.close()


async def _page_has_creator_shell(page, timeout=2500):
    if page.is_closed():
        return False
    try:
        await page.wait_for_selector("text=发布笔记", timeout=timeout)
        return True
    except Exception:
        return False


async def _reuse_existing_creator_page(context):
    for page in context.pages:
        if page.is_closed():
            continue
        if "xiaohongshu.com" not in page.url:
            continue
        if await _page_has_creator_shell(page):
            print(f"已复用现有小红书创作页面：{page.url}")
            return page
    return None


async def _goto_url_with_retry(page, url, label, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except PlaywrightError as exc:
            last_error = exc
            message = str(exc)
            print(f"打开{label}失败，准备重试 ({attempt}/{retries})：{message.splitlines()[0]}")
            if "ERR_NAME_NOT_RESOLVED" in message:
                await asyncio.sleep(2 * attempt)
            else:
                await asyncio.sleep(1)
    raise RuntimeError(
        f"无法打开{label}，浏览器 DNS/网络解析失败。"
        "请检查本机网络、代理、DNS 或稍后重试。原始错误："
        f"{last_error}"
    )


async def _goto_creator_home_with_retry(page, retries=3):
    await _goto_url_with_retry(page, CREATOR_HOME_URL, "小红书创作中心", retries=retries)


async def _goto_xhs_home_with_retry(page, retries=3):
    await _goto_url_with_retry(page, XHS_HOME_URL, "小红书主页", retries=retries)


async def _reuse_existing_xhs_web_page(context):
    for page in context.pages:
        if page.is_closed():
            continue
        if "www.xiaohongshu.com" in page.url:
            print(f"已复用现有小红书主页：{page.url}")
            return page
    return None


async def open_creator_home(headless=False, persistent=True):
    """
    返回一个已登录且位于创作中心首页的 page 对象。

    persistent=True 时复用项目内浏览器 profile。小红书草稿提示“草稿存储于当前浏览器本地”，
    因此创建草稿/继续观察草稿时应优先使用 persistent context。
    """
    if not persistent and not os.path.exists(AUTH_FILE):
        raise FileNotFoundError("未找到登录状态文件，请先运行test下的login_init.py 手动登录。")
    p = await async_playwright().start()

    browser = None
    context = None
    try:
        if persistent:
            BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=headless,
                    viewport={"width": 1366, "height": 900},
                )
            except PlaywrightError as exc:
                message = str(exc)
                if "Target page, context or browser has been closed" in message:
                    raise RuntimeError(
                        "无法启动小红书持久浏览器 profile。通常是因为已有另一个 Agent/Chromium "
                        f"正在使用 {BROWSER_PROFILE_DIR}。请先关闭旧的浏览器窗口或停止旧的终端 Agent，"
                        "再重新执行任务。"
                    ) from exc
                raise
            browser = _BrowserCloser(context)
            page = await _reuse_existing_creator_page(context)
            if page is None:
                page = context.pages[0] if context.pages else await context.new_page()
                await _goto_creator_home_with_retry(page)
        else:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                storage_state=AUTH_FILE,
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            await _goto_creator_home_with_retry(page)

        try:
            # 等待核心元素出现，证明页面加载成功
            await page.wait_for_selector("text=发布笔记", timeout=10000)
            print("已成功进入创作中心")
        except Exception:
            # 如果没找到，打印当前 URL 方便调试
            print(f"警告：未检测到'发布笔记'按钮，当前 URL: {page.url}")
        return page, browser, context, p
    except Exception:
        if browser:
            await browser.close()
        await p.stop()
        raise


async def open_xhs_home(headless=False, persistent=True):
    """
    返回一个位于小红书主站首页的 page 对象。

    主站用于个人主页、笔记详情、评论读取与评论回复等能力。它使用独立的 persistent
    profile，避免与创作中心的本地草稿 profile 相互影响。
    """
    if not persistent and not os.path.exists(XHS_WEB_AUTH_FILE):
        raise FileNotFoundError("未找到小红书主页登录状态文件，请先运行 test 下的小红书主页登录初始化脚本。")
    p = await async_playwright().start()

    browser = None
    context = None
    try:
        if persistent:
            XHS_WEB_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(XHS_WEB_PROFILE_DIR),
                    headless=headless,
                    viewport={"width": 1366, "height": 900},
                )
            except PlaywrightError as exc:
                message = str(exc)
                if "Target page, context or browser has been closed" in message:
                    raise RuntimeError(
                        "无法启动小红书主页持久浏览器 profile。通常是因为已有另一个 Agent/Chromium "
                        f"正在使用 {XHS_WEB_PROFILE_DIR}。请先关闭旧的浏览器窗口或停止旧的终端 Agent，"
                        "再重新执行任务。"
                    ) from exc
                raise
            browser = _BrowserCloser(context)
            page = await _reuse_existing_xhs_web_page(context)
            if page is None:
                page = context.pages[0] if context.pages else await context.new_page()
                await _goto_xhs_home_with_retry(page)
        else:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                storage_state=XHS_WEB_AUTH_FILE,
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            await _goto_xhs_home_with_retry(page)

        try:
            await page.wait_for_selector("body", timeout=10000)
            print("已成功进入小红书主页")
        except Exception:
            print(f"警告：未检测到页面 body，当前 URL: {page.url}")
        return page, browser, context, p
    except Exception:
        if browser:
            await browser.close()
        await p.stop()
        raise
