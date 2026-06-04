# test/test_login.py
import asyncio
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import open_creator_home

async def main():
    page, browser, context, p = await open_creator_home(headless=False)  # 接收 p
    try:
        await asyncio.sleep(5)
    finally:
        await browser.close()
        await p.stop()   # 停掉 Playwright 的后台进程

if __name__ == "__main__":
    asyncio.run(main())
