import asyncio
import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.browser_session import open_xhs_home
from src.web_note_metrics_collector import collect_latest_published_note_metrics


async def main():
    page, browser, context, playwright = await open_xhs_home(headless=False, persistent=True)
    try:
        result = await collect_latest_published_note_metrics(page)
        note = result["note"]
        storage = result["storage"]
        print("collection completed")
        print(f"title: {note.get('title')}")
        print(f"published_at: {note.get('published_at')}")
        print(f"comment_count: {note.get('comment_count')}")
        print(f"like_count: {note.get('like_count')}")
        print(f"collect_count: {note.get('collect_count')}")
        print(f"share_count: {note.get('share_count')}")
        print(f"storage: added={storage.get('added')} duplicate={storage.get('duplicate')}")
        print(f"output_file: {storage.get('output_file')}")
    finally:
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
