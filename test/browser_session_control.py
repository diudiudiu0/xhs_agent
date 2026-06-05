"""Unified browser session utility for manual XHS login and inspection.

Examples:
    python test/browser_session_control.py --target both --mode login
    python test/browser_session_control.py --target creator --mode keep-open
    python test/browser_session_control.py --target web --mode login --close-after-save
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import (
    AUTH_FILE,
    BROWSER_PROFILE_DIR,
    XHS_WEB_AUTH_FILE,
    XHS_WEB_PROFILE_DIR,
    open_creator_home,
    open_xhs_home,
)


OpenPageFunc = Callable[..., Awaitable[tuple[object, object, object, object]]]


@dataclass
class BrowserSite:
    key: str
    label: str
    auth_file: str
    profile_dir: Path
    opener: OpenPageFunc


SITES = {
    "creator": BrowserSite(
        key="creator",
        label="小红书创作中心",
        auth_file=AUTH_FILE,
        profile_dir=BROWSER_PROFILE_DIR,
        opener=open_creator_home,
    ),
    "web": BrowserSite(
        key="web",
        label="小红书主站",
        auth_file=XHS_WEB_AUTH_FILE,
        profile_dir=XHS_WEB_PROFILE_DIR,
        opener=open_xhs_home,
    ),
}


async def _read_input(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


def _target_sites(target: str) -> list[BrowserSite]:
    if target == "both":
        return [SITES["creator"], SITES["web"]]
    return [SITES[target]]


async def _open_sites(sites: list[BrowserSite]):
    sessions = []
    for site in sites:
        page, browser, context, playwright = await site.opener(headless=False, persistent=True)
        sessions.append(
            {
                "site": site,
                "page": page,
                "browser": browser,
                "context": context,
                "playwright": playwright,
            }
        )
        print(f"\n已打开：{site.label}")
        print(f"当前页面：{page.url}")
        print(f"持久浏览器目录：{site.profile_dir}")
        print(f"兼容登录态文件：{site.auth_file}")
    return sessions


async def _print_login_hint(session: dict):
    site = session["site"]
    page = session["page"]
    try:
        body_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    if "登录" in body_text[:3000] or "验证码" in body_text[:3000]:
        print(f"{site.label} 仍可能需要登录，请在浏览器中完成扫码、验证码或账号登录。")
    else:
        print(f"{site.label} 未明显检测到登录入口，可以保存当前状态。")


async def _save_sessions(sessions: list[dict]):
    for session in sessions:
        site = session["site"]
        context = session["context"]
        await context.storage_state(path=site.auth_file)
        print(f"{site.label} 登录状态已保存到：{site.auth_file}")
        print(f"{site.label} 持久浏览器数据目录：{site.profile_dir}")


async def _wait_for_quit(message: str = "输入 q / quit / exit 关闭脚本："):
    while True:
        command = (await _read_input(message)).strip().lower()
        if command in {"q", "quit", "exit"}:
            return


async def _run_login_mode(sessions: list[dict], close_after_save: bool):
    print("\n请在已打开的浏览器窗口中完成需要的登录。")
    print("完成后输入 s 保存全部已打开站点的登录状态；输入 q 放弃并关闭。")
    while True:
        for session in sessions:
            await _print_login_hint(session)
        command = (await _read_input("请输入 s 保存，或 q 退出：")).strip().lower()
        if command in {"s", "save", ""}:
            await _save_sessions(sessions)
            if not close_after_save:
                print("\n浏览器会继续保持打开，方便你检查页面状态。")
                await _wait_for_quit()
            return
        if command in {"q", "quit", "exit"}:
            return
        print("未识别的命令。")


async def _close_sessions(sessions: list[dict]):
    for session in sessions:
        browser = session.get("browser")
        playwright = session.get("playwright")
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="小红书浏览器会话统一控制脚本。")
    parser.add_argument(
        "--target",
        choices=["creator", "web", "both"],
        default="both",
        help="creator=创作中心，web=小红书主站，both=两个站点都打开。",
    )
    parser.add_argument(
        "--mode",
        choices=["login", "keep-open"],
        default="login",
        help="login=登录并保存状态，keep-open=只打开并保持浏览器。",
    )
    parser.add_argument(
        "--close-after-save",
        action="store_true",
        help="保存登录状态后立即关闭脚本。",
    )
    return parser


async def main():
    args = _build_parser().parse_args()
    sessions = await _open_sites(_target_sites(args.target))
    try:
        if args.mode == "login":
            await _run_login_mode(sessions, close_after_save=args.close_after_save)
        else:
            print("\n浏览器会保持打开，方便手动观察页面状态。")
            await _wait_for_quit()
    finally:
        await _close_sessions(sessions)
        print("浏览器会话脚本已结束。")


if __name__ == "__main__":
    asyncio.run(main())
