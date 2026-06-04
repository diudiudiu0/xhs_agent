# test/login_xhs_home_init.py
"""Initialize login state for the Xiaohongshu main website."""

import asyncio
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import (
    XHS_HOME_URL,
    XHS_WEB_AUTH_FILE,
    XHS_WEB_PROFILE_DIR,
    open_xhs_home,
)


async def _read_user_input(prompt):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def _print_login_hint(page):
    try:
        body_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    if "登录" in body_text[:3000]:
        print("当前页面仍可能显示登录入口。如果你还没登录，请先在浏览器里完成登录。")
    else:
        print("页面未明显检测到登录入口，可以保存当前浏览器状态。")


async def _wait_for_save_command(page):
    print(f"已打开小红书主页：{XHS_HOME_URL}")
    print("请在浏览器中完成扫码、验证码或账号登录。")
    print("登录完成后，在终端输入 s 保存登录状态；输入 q 放弃并关闭脚本。")
    while True:
        await _print_login_hint(page)
        command = (await _read_user_input("请输入 s 保存，或 q 退出：")).strip().lower()
        if command in {"s", "save", ""}:
            return True
        if command in {"q", "quit", "exit"}:
            return False
        print("未识别的命令。")


async def _wait_for_close_command():
    print("浏览器会继续保持打开，方便你检查主页、个人主页、笔记与评论入口。")
    while True:
        command = (await _read_user_input("输入 q / quit / exit 关闭脚本：")).strip().lower()
        if command in {"q", "quit", "exit"}:
            return


async def main():
    page, browser, context, playwright = await open_xhs_home(
        headless=False,
        persistent=True,
    )
    try:
        should_save = await _wait_for_save_command(page)
        if should_save:
            await context.storage_state(path=XHS_WEB_AUTH_FILE)
            print(f"小红书主页登录状态已保存到：{XHS_WEB_AUTH_FILE}")
            print(f"小红书主页持久浏览器数据已保存到：{XHS_WEB_PROFILE_DIR}")
            print(f"当前页面：{page.url}")
            await _wait_for_close_command()
    finally:
        await browser.close()
        await playwright.stop()
        print("小红书主页登录初始化脚本已结束。")


if __name__ == "__main__":
    asyncio.run(main())
