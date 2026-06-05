"""Unified browser session utility for manual XHS login and inspection.

Examples:
    python test/integration/browser_session_control.py --target both --mode login
    python test/integration/browser_session_control.py --target creator --mode keep-open
    python test/integration/browser_session_control.py --target web --mode login --close-after-save
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

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
        label="XHS creator center",
        auth_file=AUTH_FILE,
        profile_dir=BROWSER_PROFILE_DIR,
        opener=open_creator_home,
    ),
    "web": BrowserSite(
        key="web",
        label="XHS main site",
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
        print(f"\nopened: {site.label}")
        print(f"current page: {page.url}")
        print(f"persistent profile dir: {site.profile_dir}")
        print(f"storage-state file: {site.auth_file}")
    return sessions


async def _print_login_hint(session: dict):
    site = session["site"]
    page = session["page"]
    try:
        body_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    if "登录" in body_text[:3000] or "验证码" in body_text[:3000]:
        print(f"{site.label} may still need login. Finish login in the browser window.")
    else:
        print(f"{site.label} did not show an obvious login prompt; current state can be saved.")


async def _save_sessions(sessions: list[dict]):
    for session in sessions:
        site = session["site"]
        context = session["context"]
        await context.storage_state(path=site.auth_file)
        print(f"{site.label} storage state saved to: {site.auth_file}")
        print(f"{site.label} persistent profile dir: {site.profile_dir}")


async def _wait_for_quit(message: str = "enter q / quit / exit to close this script: "):
    while True:
        command = (await _read_input(message)).strip().lower()
        if command in {"q", "quit", "exit"}:
            return


async def _run_login_mode(sessions: list[dict], close_after_save: bool):
    print("\nFinish any required login in the opened browser windows.")
    print("Enter s to save all opened site states, or q to exit without saving.")
    while True:
        for session in sessions:
            await _print_login_hint(session)
        command = (await _read_input("enter s to save, or q to exit: ")).strip().lower()
        if command in {"s", "save", ""}:
            await _save_sessions(sessions)
            if not close_after_save:
                print("\nBrowser windows will remain open for inspection.")
                await _wait_for_quit()
            return
        if command in {"q", "quit", "exit"}:
            return
        print("unknown command")


async def _close_sessions(sessions: list[dict]):
    for session in sessions:
        browser = session.get("browser")
        playwright = session.get("playwright")
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified XHS browser session control script.")
    parser.add_argument(
        "--target",
        choices=["creator", "web", "both"],
        default="both",
        help="creator=creator center, web=main site, both=open both sites.",
    )
    parser.add_argument(
        "--mode",
        choices=["login", "keep-open"],
        default="login",
        help="login=login and save state, keep-open=open and keep browser running.",
    )
    parser.add_argument(
        "--close-after-save",
        action="store_true",
        help="Close the script immediately after saving storage state.",
    )
    return parser


async def main():
    args = _build_parser().parse_args()
    sessions = await _open_sites(_target_sites(args.target))
    try:
        if args.mode == "login":
            await _run_login_mode(sessions, close_after_save=args.close_after_save)
        else:
            print("\nBrowser windows will remain open for manual inspection.")
            await _wait_for_quit()
    finally:
        await _close_sessions(sessions)
        print("browser session script finished")


if __name__ == "__main__":
    asyncio.run(main())
