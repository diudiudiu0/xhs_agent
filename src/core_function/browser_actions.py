import os
from pathlib import Path

from playwright.async_api import async_playwright


# 获取项目根目录（向上三级：browser_actions.py -> core_function -> src -> root）
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
AUTH_FILE = str(PROJECT_ROOT / "auth.json")
BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser_profile" / "xhs_creator"


class _BrowserCloser:
    def __init__(self, close_target):
        self._close_target = close_target

    async def close(self):
        await self._close_target.close()


async def open_creator_home(headless=False, persistent=True):
    """
    返回一个已登录且位于创作中心首页的 page 对象。

    persistent=True 时复用项目内浏览器 profile。小红书草稿提示“草稿存储于当前浏览器本地”，
    因此创建草稿/继续观察草稿时应优先使用 persistent context。
    """
    if not persistent and not os.path.exists(AUTH_FILE):
        raise FileNotFoundError("未找到登录状态文件，请先运行src下的login_init.py 手动登录。")
    p = await async_playwright().start()

    if persistent:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=headless,
            viewport={"width": 1366, "height": 900},
        )
        browser = _BrowserCloser(context)
        page = context.pages[0] if context.pages else await context.new_page()
    else:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=AUTH_FILE,
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()

    await page.goto("https://creator.xiaohongshu.com/creator/home", wait_until="domcontentloaded")
    try:
        # 等待核心元素出现，证明页面加载成功
        await page.wait_for_selector("text=发布笔记", timeout=10000)
        print("已成功进入创作中心")
    except Exception:
        # 如果没找到，打印当前 URL 方便调试
        print(f"警告：未检测到'发布笔记'按钮，当前 URL: {page.url}")
    return page, browser, context,p
