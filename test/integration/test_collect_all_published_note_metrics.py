import asyncio
import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.browser_session import open_xhs_home
from src.web_note_metrics_collector import collect_all_published_note_metrics


async def main():
    page, browser, context, playwright = await open_xhs_home(headless=False, persistent=True)
    try:
        result = await collect_all_published_note_metrics(page)
        storage = result["storage"]
        notes = result.get("notes") or []
        errors = result.get("errors") or []
        print("collection completed")
        print(f"note_count: {len(notes)}")
        print(f"errors: {len(errors)}")
        for index, note in enumerate(notes, 1):
            print(f"[{index}] {note.get('title')}")
            print(f"    published_at: {note.get('published_at')}")
            print(f"    content_len: {len(note.get('content') or '')}")
            print(
                "    counts: "
                f"like={note.get('like_count')} "
                f"collect={note.get('collect_count')} "
                f"comment={note.get('comment_count')} "
                f"share={note.get('share_count')}"
            )
        if errors:
            print("errors detail:")
            for item in errors:
                print(f"  - index={item.get('note_index')} error={item.get('error')}")
        print(f"output_file: {storage.get('output_file')}")
    finally:
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
