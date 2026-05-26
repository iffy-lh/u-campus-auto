import asyncio
from playwright.async_api import async_playwright

async def main():
    p = await async_playwright().start()
    ctx = await p.chromium.launch_persistent_context(
        "./browser_data",
        headless=False,
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
    )
    page = await ctx.new_page()
    await page.goto("https://uai.unipus.cn/login")

    print("\n>>> 请在浏览器中手动完成登录 <<<")
    print(">>> 登录成功后回到这里按 Enter <<<")
    input()

    await ctx.close()
    await p.stop()
    print("登录态已保存到 browser_data/")

asyncio.run(main())
