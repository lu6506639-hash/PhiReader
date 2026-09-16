from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto("http://127.0.0.1:5173")
        page.wait_for_load_state("networkidle")
        sidebar_handle = page.get_by_role("separator", name="调整左侧边栏宽度")
        assert sidebar_handle.count() == 1, "left sidebar resize handle is missing"
        before_sidebar_width = page.locator(".sidebar").bounding_box()["width"]
        handle_box = sidebar_handle.bounding_box()
        assert handle_box is not None
        drag_y = handle_box["y"] + handle_box["height"] / 2
        drag_x = handle_box["x"] + handle_box["width"] / 2
        page.mouse.move(drag_x, drag_y)
        page.mouse.down()
        page.mouse.move(drag_x + 72, drag_y, steps=6)
        page.mouse.up()
        after_drag_width = page.locator(".sidebar").bounding_box()["width"]
        assert after_drag_width >= before_sidebar_width + 60, "left sidebar pointer drag did not resize"
        sidebar_handle.focus()
        sidebar_handle.press("ArrowRight")
        after_keyboard_width = page.locator(".sidebar").bounding_box()["width"]
        assert after_keyboard_width > after_drag_width, "left sidebar keyboard resize did not work"
        page.reload()
        page.wait_for_load_state("networkidle")
        persisted_width = page.locator(".sidebar").bounding_box()["width"]
        assert abs(persisted_width - after_keyboard_width) <= 1, "left sidebar width was not persisted"
        page.get_by_role("button", name="当前阅读").click()
        page.locator(".reader-layout").wait_for()

        layout = page.locator(".reader-layout")
        document = page.locator(".document-view")
        panel = page.locator(".symbol-panel")
        footer = page.locator(".panel-footer")
        assert layout.evaluate("el => el.scrollHeight") <= layout.evaluate("el => el.clientHeight"), "reader layout overflows viewport"
        assert document.evaluate("el => el.scrollHeight") > document.evaluate("el => el.clientHeight"), "document is not scrollable"
        document.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop }")
        assert document.evaluate("el => el.scrollTop") > 0, "document cannot scroll to the bottom"
        assert footer.evaluate("el => el.getBoundingClientRect().bottom") <= panel.evaluate("el => el.getBoundingClientRect().bottom") + 1, "sidebar footer is clipped"

        page.get_by_role("button", name="设置").click()
        page.get_by_role("button", name="＋").click()
        page.get_by_role("button", name="当前阅读").click()
        page.locator(".reader-layout").wait_for()
        list_meaning = page.locator(".list-meaning").first
        assert float(list_meaning.evaluate("el => getComputedStyle(el).fontSize.replace('px','')")) > 8, "sidebar summary text did not follow body-size setting"
        browser.close()


if __name__ == "__main__":
    main()
