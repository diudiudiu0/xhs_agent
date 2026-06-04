# test/open_browser_keep_alive.py
"""Open the XHS creator browser and keep it alive for manual observation."""

import asyncio
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_actions import AUTH_FILE, BROWSER_PROFILE_DIR, open_creator_home


async def _wait_for_user_exit():
    loop = asyncio.get_running_loop()
    prompt = "\n浏览器会保持打开。输入 q / quit / exit 后关闭脚本："
    while True:
        command = await loop.run_in_executor(None, input, prompt)
        if command.strip().lower() in {"q", "quit", "exit"}:
            return


async def main():
    page, browser, context, playwright = await open_creator_home(
        headless=False,
        persistent=True,
    )
    try:
        print("浏览器界面已启动，并已加载保存的持久浏览器数据。")
        print(f"持久浏览器目录：{BROWSER_PROFILE_DIR}")
        print(f"兼容登录态文件：{AUTH_FILE}")
        print(f"当前页面：{page.url}")
        await _wait_for_user_exit()
    finally:
        await browser.close()
        await playwright.stop()
        print("浏览器脚本已结束。")


if __name__ == "__main__":
    asyncio.run(main())
