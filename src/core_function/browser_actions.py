# src/core_function/browser_actions.py
import os
from playwright.async_api import async_playwright
from pathlib import Path

# 获取项目根目录（向上三级：browser_actions.py -> core_function -> src -> root）
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
AUTH_FILE = str(PROJECT_ROOT / "auth.json")
async def open_creator_home(headless=False):
    """
    返回一个已登录且位于创作中心首页的 page 对象。
    如果 auth.json 不存在，需要先手动运行登录脚本。
    """
    if not os.path.exists(AUTH_FILE):
        raise FileNotFoundError("未找到登录状态文件，请先运行src下的login_init.py 手动登录。")
    p = await async_playwright().start()
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
