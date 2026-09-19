from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT = ROOT / "test-paper" / "feedback-button-smoke.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto("http://127.0.0.1:1420")
    page.wait_for_load_state("networkidle")

    feedback = page.get_by_role("button", name="提交反馈")
    feedback.wait_for(state="visible")
    page.screenshot(path=str(SCREENSHOT), full_page=True)

    page.evaluate("window.open = (url) => { window.__feedbackUrl = url; }")
    feedback.click()
    issue_url = page.evaluate("window.__feedbackUrl")
    parsed = urlparse(issue_url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "github.com"
    assert parsed.path == "/lu6506639-hash/PhiReader/issues/new"
    assert query["title"] == ["[反馈] "]
    body = query["body"][0]
    assert "PhiReader 版本：0.1.6" in body
    assert "界面语言：zh-CN" in body
    assert "运行环境：网页预览" in body
    assert "系统信息：Mozilla/5.0" in body

    page.evaluate("localStorage.setItem('phireader.interfaceLanguage', 'en')")
    page.reload()
    page.wait_for_load_state("networkidle")
    feedback = page.get_by_role("button", name="Send feedback")
    page.evaluate("window.open = (url) => { window.__feedbackUrl = url; }")
    feedback.click()
    english_query = parse_qs(urlparse(page.evaluate("window.__feedbackUrl")).query)
    assert english_query["title"] == ["[Feedback] "]
    assert "Interface language: en" in english_query["body"][0]

    browser.close()

print(f"Feedback UI smoke test passed; screenshot: {SCREENSHOT}")
