import asyncio
import sys
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import _bootstrap  # noqa: F401

from src.account_overview_collector import collect_account_overview
from src.browser_session import open_creator_home


async def main():
    page, browser, context, playwright = await open_creator_home(headless=False, persistent=True)
    try:
        result = await collect_account_overview(page)
        overview = result.get("overview") or {}
        storage = result.get("storage") or {}
        print("account overview collection completed")
        print(f"url: {overview.get('url')}")
        print(f"metrics: {overview.get('metrics')}")
        print(f"metric_cards: {len(overview.get('metric_cards') or [])}")
        print(f"output_file: {storage.get('output_file')}")
        print(f"history_file: {storage.get('history_file')}")
    finally:
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
