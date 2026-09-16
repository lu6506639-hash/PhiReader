from playwright.sync_api import sync_playwright


def main() -> None:
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.add_init_script("localStorage.setItem('phireader.interfaceLanguage', 'en')")
        page.goto("http://127.0.0.1:1420")
        page.wait_for_load_state("networkidle")

        page.evaluate(
            r"""
            () => {
              const transfer = new DataTransfer()
              transfer.items.add(new File(['%PDF-1.7\n'], 'first-paper.pdf', { type: 'application/pdf' }))
              transfer.items.add(new File(['%PDF-1.7\n'], 'second-paper.pdf', { type: 'application/pdf' }))
              transfer.items.add(new File(['notes'], 'notes.txt', { type: 'text/plain' }))
              window.__quickUploadTransfer = transfer
              document.querySelector('.app-shell').dispatchEvent(new DragEvent('dragenter', {
                bubbles: true,
                cancelable: true,
                dataTransfer: transfer,
              }))
            }
            """
        )

        overlay = page.locator(".quick-upload-overlay")
        overlay.wait_for(state="visible")
        overlay_text = overlay.text_content()
        assert overlay_text is not None
        assert "3 files" in overlay_text
        assert "PDF files only" in overlay_text

        page.evaluate(
            """
            () => document.querySelector('.app-shell').dispatchEvent(new DragEvent('drop', {
              bubbles: true,
              cancelable: true,
              dataTransfer: window.__quickUploadTransfer,
            }))
            """
        )

        overlay.wait_for(state="detached")
        page.get_by_role("heading", name="first-paper").wait_for()
        page.get_by_role("heading", name="second-paper").wait_for()
        assert page.get_by_text("notes.txt", exact=True).count() == 0
        assert console_errors == [], f"browser console errors: {console_errors}"
        browser.close()


if __name__ == "__main__":
    main()
