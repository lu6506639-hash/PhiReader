import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Run the PhiReader browser UI smoke test")
    argument_parser.add_argument("--pdf", type=Path, help="optional local PDF to exercise the file chooser")
    args = argument_parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:1420")
        page.wait_for_load_state("networkidle")
        assert page.get_by_role("heading", name="论文库").is_visible()
        assert page.get_by_text("PhiReader", exact=True).is_visible()

        page.get_by_role("article").first.click()
        page.wait_for_timeout(150)
        assert page.get_by_role("heading", name="符号索引").is_visible()
        page.get_by_role("button", name="V", exact=True).click()
        assert page.locator(".symbol-meaning").get_by_text("非负数据矩阵").is_visible()
        assert page.get_by_text("AUTHOR'S DEFINITION").is_visible()
        page.locator(".source-link").click()
        page.wait_for_timeout(100)
        assert page.get_by_label("跳转页码").input_value() == "2"
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(100)
        assert page.get_by_label("跳转页码").input_value() == "3"
        page.keyboard.press("End")
        page.wait_for_timeout(100)
        assert page.get_by_label("跳转页码").input_value() == "7"
        page.get_by_title("增大正文").click()
        assert "--reader-size: 18px" in page.locator(".document-view").get_attribute("style")

        page.get_by_role("button", name="设置").click()
        assert page.get_by_role("heading", name="设置").is_visible()
        api = page.get_by_placeholder("粘贴 API Key")
        api.fill("test-key")
        assert page.get_by_text("已保存").is_visible()
        page.get_by_role("button", name="浅色").click()
        assert page.locator(".app-shell.light").count() == 1

        if args.pdf:
            if not args.pdf.is_file():
                raise FileNotFoundError(f"PDF does not exist: {args.pdf}")
            page.get_by_role("button", name="论文库").click()
            file_input = page.locator('input[type="file"]')
            file_input.set_input_files(str(args.pdf))
            assert page.get_by_text("论文已加入库，桌面版启动后将自动解析").is_visible()
            assert page.get_by_text(args.pdf.stem, exact=True).count() >= 1
        page.screenshot(path="D:/Phi/ui-smoke.png", full_page=True)
        browser.close()
        print("ui-smoke=passed")


if __name__ == "__main__":
    main()
