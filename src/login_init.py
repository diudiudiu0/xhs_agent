# src/core_function/login_manager.py
import asyncio
import os
from playwright.async_api import async_playwright

# 指定 auth.json 的绝对路径，确保无论从哪运行都保存在项目根目录
import pathlib
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
AUTH_FILE = str(PROJECT_ROOT / "auth.json")

async def login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        await page.goto("https://creator.xiaohongshu.com/")
        print("请在打开的浏览器中完成登录（手机扫码或账号密码），登录成功后程序会自动继续...")

        # 等待登录成功：以页面出现“发布笔记”按钮或跳转到创作中心首页为准
        try:
            await page.wait_for_selector("text=发布笔记", timeout=120000)  # 最长等2分钟
            print("登录成功！")
        except Exception:
            # 如果没检测到按钮，可能是登录后页面跳转了，手动检查
            await asyncio.sleep(3)
            print(f"当前 URL: {page.url}")
            if "creator" in page.url:
                print("已登录，继续保存状态。")
            else:
                print("登录似乎未成功，请重试。")
                await browser.close()
                return

        # 保存登录状态
        await context.storage_state(path=AUTH_FILE)
        print(f"登录状态已保存到：{AUTH_FILE}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(login())
