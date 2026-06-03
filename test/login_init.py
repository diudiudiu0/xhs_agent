import asyncio
import pathlib

from playwright.async_api import async_playwright


PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
AUTH_FILE = str(PROJECT_ROOT / "auth.json")
BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser_profile" / "xhs_creator"


async def login():
    async with async_playwright() as p:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://creator.xiaohongshu.com/")
        print("请在打开的浏览器中完成登录（手机扫码或账号密码），登录成功后程序会自动继续...")
        print(f"本次登录会写入持久浏览器目录：{BROWSER_PROFILE_DIR}")

        try:
            await page.wait_for_selector("text=发布笔记", timeout=120000)
            print("登录成功！")
        except Exception:
            await asyncio.sleep(3)
            print(f"当前 URL: {page.url}")
            if "creator" in page.url:
                print("已登录，继续保存状态。")
            else:
                print("登录似乎未成功，请重试。")
                await context.close()
                return

        # auth.json 兼容非持久 context；真正用于保留本地草稿的是 .browser_profile。
        await context.storage_state(path=AUTH_FILE)
        print(f"登录状态已保存到：{AUTH_FILE}")
        print(f"持久浏览器数据已保存在：{BROWSER_PROFILE_DIR}")
        await context.close()


if __name__ == "__main__":
    asyncio.run(login())
