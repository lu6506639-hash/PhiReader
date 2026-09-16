"""Headless browser smoke test for the non-Tauri frontend shell."""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "1420"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.get_by_role("heading", name="论文库").wait_for()

        assert page.get_by_role("button", name="导入论文").count() == 1
        assert page.get_by_text("Algorithms for Non-negative Matrix Factorization").count() == 1
        assert page.locator(".brand-icon img").get_attribute("src") == "/icon2.png"
        assert page.locator("body").evaluate("element => getComputedStyle(element).overflow") == "hidden"

        page.get_by_placeholder("搜索标题、作者或期刊…").fill("Lee")
        assert page.get_by_text("Algorithms for Non-negative Matrix Factorization").count() == 1
        page.get_by_placeholder("搜索标题、作者或期刊…").fill("不存在的论文")
        assert page.get_by_text("Algorithms for Non-negative Matrix Factorization").count() == 0
        page.get_by_placeholder("搜索标题、作者或期刊…").fill("")

        page.locator(".card-more").first.click()
        page.once("dialog", lambda dialog: dialog.accept("重命名测试论文"))
        page.locator("article.paper-card").first.get_by_role("button", name="重命名").click()
        assert page.get_by_text("重命名测试论文", exact=True).count() == 1

        card_count = page.locator("article.paper-card").count()
        page.locator(".card-more").nth(1).click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("article.paper-card").nth(1).get_by_role("button", name="删除").click()
        assert page.locator("article.paper-card").count() == card_count - 1

        page.locator("article.paper-card").first.focus()
        page.locator("article.paper-card").first.press("Enter")
        page.get_by_role("heading", name="符号索引").wait_for()
        page.get_by_role("button", name="论文库").first.click()

        page.get_by_role("button", name="设置").click()
        page.get_by_role("heading", name="设置").wait_for()
        assert page.get_by_text("当前服务商 API Key").count() == 1
        assert page.locator(".settings-page").evaluate("element => element.scrollHeight <= element.clientHeight")
        page.get_by_label("界面语言").select_option("en")
        page.get_by_role("heading", name="Settings").wait_for()
        assert page.get_by_role("button", name="Library").count() == 1
        page.get_by_label("Interface language").select_option("zh-CN")
        page.get_by_role("heading", name="设置").wait_for()
        page.get_by_role("button", name="浅色").click()

        page.get_by_role("button", name="论文库").first.click()
        page.get_by_role("heading", name="论文库").wait_for()
        page.locator("article.paper-card").first.click()
        page.get_by_role("heading", name="符号索引").wait_for()
        assert page.locator(".summary-button").count() == 1
        assert page.locator(".symbol-meaning").count() >= 1
        assert page.locator(".document-view").evaluate("element => getComputedStyle(element).overflow === 'auto'")
        assert page.locator(".panel-scroll").evaluate("element => getComputedStyle(element).overflowY === 'auto'")

        page.get_by_label("第").fill("2")
        page.get_by_label("第").press("Enter")
        assert page.get_by_label("第").input_value() == "2"
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(100)
        assert page.get_by_label("第").input_value() == "3"
        page.keyboard.press("Home")
        page.wait_for_timeout(100)
        assert page.get_by_label("第").input_value() == "1"
        assert page.locator(".katex").count() >= 1

        initial_zoom = page.locator(".zoom-value").inner_text()
        page.locator(".reader-page").hover()
        page.keyboard.down("Control")
        page.mouse.wheel(0, -240)
        page.keyboard.up("Control")
        assert page.locator(".zoom-value").inner_text() != initial_zoom

        page.locator(".symbol-list-item").first.click()
        assert page.locator(".definition-box").count() == 0
        assert page.get_by_text("生成摘要后查看").count() >= 1
        page.get_by_role("button", name="清除符号选择").click()
        assert page.locator(".empty-symbol").is_visible()
        assert not page.locator(".review-queue").count()

        browser.close()
    print("ui smoke: ok")


if __name__ == "__main__":
    main()
