from playwright.sync_api import expect, sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:1420")
        page.wait_for_load_state("networkidle")

        first_paper = "Algorithms for Non-negative Matrix Factorization"
        second_paper = "Blind Audio Source Separation with Minimum-Volume Criterion"
        page.get_by_role("button", name=f"打开 {first_paper}").click()

        page_input = page.locator(".page-readout input")
        page_input.fill("6")
        page_input.press("Enter")
        expect(page_input).to_have_value("6")

        page.locator(".sidebar-bottom .nav-item").click()
        expect(page.get_by_role("heading", name="设置")).to_be_visible()
        page.locator(".primary-nav .nav-item").nth(1).click()
        expect(page_input).to_have_value("6")

        page.locator(".reader-header .back-button").click()
        page.get_by_role("button", name=f"打开 {second_paper}").click()
        expect(page_input).to_have_value("1")
        page_input.fill("4")
        page_input.press("Enter")

        page.locator(".reader-header .back-button").click()
        page.get_by_role("button", name=f"打开 {first_paper}").click()
        expect(page_input).to_have_value("6")

        browser.close()
        print("page-restore=passed")


if __name__ == "__main__":
    main()
