import asyncio
import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import open_creator_home
from src.browser_tools import dump_save_stage_snapshot


async def main():
    page, browser, context, p = await open_creator_home(headless=False)
    try:
        print("请在浏览器中手动停到“暂存离开/保存草稿”弹窗或阶段。")
        input("准备好后按 Enter，脚本会抓取当前页面全部暂存相关信息...")
        await dump_save_stage_snapshot(page, reason="manual_save_stage_snapshot")
        print("抓取完成，请查看项目 debug 目录中的 save_stage_*.json 和 save_stage_*.html。")
        await asyncio.sleep(5)
    finally:
        await browser.close()
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
