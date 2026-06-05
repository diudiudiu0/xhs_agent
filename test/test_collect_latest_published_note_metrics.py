import asyncio
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import _bootstrap  # noqa: F401

from src.browser_session import open_xhs_home
from src.web_note_metrics_collector import collect_latest_published_note_metrics


async def main():
    page, browser, context, playwright = await open_xhs_home(headless=False, persistent=True)
    try:
        result = await collect_latest_published_note_metrics(page)
        note = result["note"]
        storage = result["storage"]
        print("采集完成")
        print(f"标题：{note.get('title')}")
        print(f"发布时间：{note.get('published_at')}")
        print(f"评论数量：{note.get('comment_count')}")
        print(f"点赞数：{note.get('like_count')}")
        print(f"收藏数：{note.get('collect_count')}")
        print(f"分享数：{note.get('share_count')}")
        print(f"写入状态：added={storage.get('added')} duplicate={storage.get('duplicate')}")
        print(f"保存文件：{storage.get('output_file')}")
    finally:
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
